# Multi-lecture sub-agent workflow

## Contents

1. [Activation and boundaries](#activation-and-boundaries)
2. [Primary-agent preflight](#primary-agent-preflight)
3. [Batch ledger](#batch-ledger)
4. [Capacity-aware scheduling](#capacity-aware-scheduling)
5. [Lecture-worker packet](#lecture-worker-packet)
6. [Worker return contract](#worker-return-contract)
7. [Fan-in and finalization](#fan-in-and-finalization)
8. [Failure and recovery matrix](#failure-and-recovery-matrix)

## Activation and boundaries

Use this workflow when one user request selects two or more unresolved lecture
units in one module. A lecture unit may contain several recordings when the
source evidence shows that they are ordered parts of one lecture. Never spawn
one worker per file before resolving multipart ownership.

Use the host's native sub-agent facility. In Codex, use `spawn_agent`,
`list_agents`, `wait_agent`, and `followup_task` as available. Do not launch a
nested Codex CLI, create user-visible app tasks, or use shell subprocesses as
agents. In particular, do not use the legacy `transcribe_batch.py` launcher as
a substitute for native sub-agents. Do not let a lecture worker spawn another
agent.

Keep a single lecture in the primary agent. If native sub-agents are unavailable,
state that limitation. When the user explicitly requested parallel agents, do
not silently run sequentially. When delegation came only from this skill, ask
whether to continue sequentially.

Parallelize lecture units, not the five phases inside one lecture. Each worker
must run `guide → imp → mcqs → written → cases` in that order.

## Primary-agent preflight

Complete these decisions before spawning any worker:

1. Resolve one module and list its pending recordings.
2. Complete the mandatory Agent-supervised module source sync from `SKILL.md`,
   then compare its saved state and the live NotebookLM inventory with local
   `Lecture/` and `Questions/`.
3. Group multipart recordings in spoken order. Give every selected recording to
   exactly one lecture unit.
4. Classify the shared assessment files once and build one format-only
   `exam_style_profile` for the batch.
5. Resolve module-wide ambiguity. Stop the batch when notebook selection,
   assessment provenance, or shared source identity is ambiguous.
6. Create one temporary source manifest per lecture outside Git. Copy the shared
   assessment classification and exam profile into each manifest, then add only
   that lecture's recordings, slide, references, and approved uploads.
7. Run an audit for every manifest and repair all malformed or overlapping
   manifests before spawning.
8. Initialize the batch ledger. Treat its lecture keys as the ownership keys
   used in agent task names and status reports.

A lecture-specific ambiguity blocks only that lecture after the shared preflight.
Do not give an ambiguous upload or source mapping to a worker and hope it can
guess.

## Batch ledger

Store orchestration state under the module's ignored `.transcriber-cache/`.
Initialize it after all manifests pass validation:

```bash
python3 .agents/skills/universal-transcriber/scripts/batch_state.py init \
  --module toxo \
  --cache-root MODULE_ROOT/.transcriber-cache \
  --manifest /tmp/toxo-corrosives.json \
  --manifest /tmp/toxo-volatile-poisons.json
```

The command rejects a batch with fewer than two manifests or any recording that
appears in more than one lecture. Read the returned `ledger` path and lecture
keys. The primary agent alone updates the ledger; workers return evidence and do
not race on shared orchestration state.

Update a lecture before spawning and after each observed transition:

```bash
python3 .agents/skills/universal-transcriber/scripts/batch_state.py update \
  --ledger MODULE/.transcriber-cache/batches/BATCH_ID/batch.json \
  --lecture-key LECTURE_KEY --status queued
```

Use only the transitions enforced by the helper. The normal path is:

```text
manifest_ready → queued → running → draft_ready → accepted → finalized → verified
```

`manifest_ready` may also become `blocked`; `queued`, `running`, and
`draft_ready` may become `blocked` or `failed`; `accepted` and `finalized` may
become `failed`; and either recovery state may return to `queued`. Repeating the
current status is an idempotent update.

Record `agent_id` when running, `run_id` and the draft path when draft-ready,
and a concise reason when blocked or failed. Never store credentials or Notebook
IDs in the ledger.

## Capacity-aware scheduling

Inspect live agents before scheduling. Reserve the primary-agent slot and fill
only the remaining native capacity. Do not hardcode a worker count because host
limits differ.

For each available slot:

1. Mark the next lecture `queued`.
2. Spawn one worker with a task name derived from its lecture key.
3. Mark it `running` with the returned agent ID.
4. Continue useful primary work while workers run.
5. Wait for a worker result, process its handoff, then refill the freed slot.

Wait for every spawned worker before reporting the batch. A worker failure must
not cancel successful siblings. Use `followup_task` on the same worker for a
recoverable correction and include new evidence or a corrected specification;
never repeat an unchanged prompt. If the original worker cannot continue, mark
the lecture failed and leave it resumable instead of silently assigning a fresh
worker with hidden context loss.

## Lecture-worker packet

Give each worker a complete, bounded packet using this shape:

```text
ROLE
Own one medical lecture. Do not delegate or work on another lecture.

OBJECTIVE
Produce and editorially review the evidence-rich draft for <TITLE>.
Stop before --finalize-draft and return a handoff to the primary agent.

OWNERSHIP
Module: <MODULE_ID>
Lecture key: <LECTURE_KEY>
Ordered recordings: <RECORDING_SOURCES>
Manifest: <ABSOLUTE_MANIFEST_PATH>
Allowed writes: this lecture's draft and run/checkpoint artifacts only.
Shared files are read-only. Do not edit Index.md or another transcript/draft.

COMMAND CONTRACT
Use only .agents/skills/universal-transcriber/scripts/run_transcription.py.
Run the supplied manifest with --audit-only, then --draft-only.
Use resume/retry only for the run ID created for this lecture.

EDITORIAL CONTRACT
Keep the Chronological Guide complete. Review every sourced and IMP assessment
item under SKILL.md. Resolve safe OCR damage, duplicates, option/answer shape,
exam-style compliance, and evidence-only residue. Stop on unresolved medical or
provenance conflict; never guess.

PROHIBITED
Do not call nlm directly. Do not change the manifest's ownership. Do not upload
an unapproved source. Do not finalize, edit Index.md, commit Git changes, or
claim success from a query response without the validators.

VERIFY
Confirm the draft exists, the five sections are ordered, the run checkpoint is
recorded, and no unresolved review marker remains.

RETURN
Use the exact LECTURE WORKER HANDOFF schema.
```

Workers share the same filesystem. Tell every worker that other workers may be
active and that ownership boundaries are mandatory. A worker may read shared
assessment sources but must not edit them.

## Worker return contract

Require exactly:

```text
LECTURE WORKER HANDOFF
STATUS: ready | blocked | failed
LECTURE_KEY: <key>
TITLE: <title>
MANIFEST: <absolute path>
RUN_ID: <run id or none>
DRAFT: <absolute path or none>
VERIFIED: <commands and concrete evidence>
LIMITATIONS: <source/OCR limitations or none>
RECOVERY: <specific next action or none>
```

Treat the handoff as a claim. Inspect the actual draft, checkpoint, manifest
snapshot, and validator evidence before changing the ledger to `draft_ready`.

## Fan-in and finalization

For every ready worker:

1. Confirm the lecture key, recordings, manifest, run ID, and draft agree.
2. Read the complete draft and perform the editorial acceptance pass from
   `SKILL.md` yourself.
3. Send precise corrections to the same worker when needed and recheck the
   returned artifact.
4. Mark an accepted draft `accepted`.
5. Run the same manifest with `--finalize-draft` from the primary agent.
6. Mark it `finalized`, then verify one transcript file and exactly one Index row
   before marking it `verified`.

Finalize accepted lectures one at a time in the primary workflow. The engine
also locks the complete Index read-render-commit transaction so an accidental
concurrent finalize cannot lose another row.

Report a table of every lecture and its final state. Include output paths for
verified lectures and the precise blocker/recovery command for incomplete ones.

## Failure and recovery matrix

| Failure | Scope | Action |
| --- | --- | --- |
| Notebook/project ambiguity | Whole batch | Stop before spawning. |
| Shared assessment provenance conflict | Whole batch | Stop before spawning. |
| Recording appears in two manifests | Whole batch | Fix ownership; ledger init must fail. |
| Lecture-specific slide/reference ambiguity | One lecture | Mark blocked; continue siblings. |
| Duplicate lecture lock | One lecture | Keep the existing owner; do not spawn another. |
| Upload/query transient failure | One lecture | Follow the saved run's bounded retry/resume path. |
| Phase validator failure | One lecture | Repair only that phase, then rerun dependents. |
| Editorial conflict requiring judgment | One lecture | Mark blocked; never guess. |
| Finalize/index failure | One lecture | Keep its draft, repair, and finalize again. |
| Worker loses context or stops | One lecture | Preserve ledger/run artifacts and report resumable state. |

Successful siblings remain finalized. Never restart a whole batch solely because
one lecture needs recovery.
