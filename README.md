# Universal Medical Transcriber

An Agent skill and Python engine for turning NotebookLM lecture recordings into
grounded, student-ready medical study documents. The Agent audits the local
course files and NotebookLM projects, creates or configures modules, chooses
safe uploads, learns the local exam style, and reviews the generated Markdown.
The engine performs the mechanical audit, five sequential NotebookLM queries,
validation, and atomic output commit.

The generated document contains exactly:

1. `Chronological Guide` — the doctor's complete teaching sequence.
2. `IMP Points` — spoken pearls, traps, lethal mistakes, interactive questions,
   and exam rules.
3. `MCQs` — source-preserved (after obvious OCR normalization) grounded
   past-exam/question-bank items plus clearly marked `IMP` items.
4. `Written Questions` — the same source/IMP distinction and concise model
   answers.
5. `Clinical Cases` — short evidence-backed cases.

The lecture explanation is not shortened. Concision is limited to answer
explanations, short model answers, and case answers.

## Requirements

- Python 3.10 or newer for the engine. The current `notebooklm-mcp-cli` release
  requires Python 3.11 or newer for the external `nlm` command.
- An authenticated `nlm` CLI (the project was verified with version 0.9.8).
- A Google NotebookLM account that can access the selected project(s).
- `pdfinfo` and `pdftotext` for PDF text-layer checks.
- Optional preparation tools, needed only when a selected source requires them:
  `ocrmypdf` or `pdfocr` for scanned PDFs, LibreOffice for legacy slides,
  `ffmpeg` for unsupported media, Ghostscript for PDF compression, and the
  Python packages `pypdf`/`reportlab` for page extraction or text-to-PDF.
- An Agent host that can load a workspace skill, such as Codex or Antigravity.

This repository does not package the external `nlm` executable or your course
files. The tested CLI is the public `notebooklm-mcp-cli` package. Install it
in an isolated environment, then verify it:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
python3 -m pipx install notebooklm-mcp-cli
nlm --version
nlm login --check
```

If `nlm` is already installed by your NotebookLM tooling, keep that installation
and only run the two verification commands. The login check needs network access
and a browser session with NotebookLM credentials.

Install Poppler when it is missing. On Debian/Ubuntu:

```bash
sudo apt-get install poppler-utils
```

On macOS, install the equivalent `poppler` package with Homebrew. On Windows,
install a Poppler distribution and ensure both `pdfinfo` and `pdftotext` are on
`PATH`. Verify with:

```bash
pdfinfo -v
pdftotext -v
```

## Install the skill

Clone the repository and open the repository root as the Agent workspace:

```bash
git clone https://github.com/3omr/universal-transcriber-skill.git
cd universal-transcriber-skill
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py --help
```

The skill is the complete `.agents/skills/universal-transcriber/` directory.
The shared runtime is `universal_transcriber/`; copy both when embedding the
skill in an existing workspace. Do not copy `modules/` from another user: that
directory contains private course data and is ignored by Git.

If the Agent host requires NotebookLM MCP registration, configure it with the
CLI using one of the client names supported by your installation, then inspect
the result:

```bash
nlm setup add antigravity # or another client shown by `nlm setup add --help`
nlm setup list
```

The transcriber itself uses the `nlm notebook` and `nlm source` commands and
does not create a project during a normal transcription run. Project creation
is owned by the module setup workflow below.

## Add a module through the Agent

The normal user flow is conversational:

```text
عاوز أضيف موديول Cardiology
```

The Agent should choose a safe lowercase module ID, check existing module
aliases, inspect NotebookLM projects, and run the bundled manager. The user
does not need to write `module.json`.

The manager creates this structure:

```text
modules/<module-id>/
├── module.json
├── Lecture/
├── Questions/
└── Transcripts/
```

The complete `module.json` schema and migration notes are in
[references/modules.md](.agents/skills/universal-transcriber/references/modules.md);
the Agent should generate the file instead of asking you to edit it manually.

`Questions/` is the single folder for both past exams and question banks. The
Agent reads the files and classifies them by content; filenames are not trusted
as the only source of year or provenance.

NotebookLM project selection follows these rules:

- One exact match is used automatically.
- If no match exists, applying the module setup creates a new NotebookLM
  project named after the module.
- If several projects match, the Agent asks the user whether to choose one or
  combine several. It never guesses. Multiple selected projects are stored in
  `module.json` and queried together; uploads go to the primary project. Each
  project keeps its own source scope, so an exam-only project cannot silently
  contribute unrelated lecture material.

The manager can also be run directly. Preview first:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module cardiology \
  --display-name Cardiology --notebook-title Cardiology
```

After the Agent confirms the project choice, apply it:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module cardiology \
  --display-name Cardiology --notebook-title Cardiology --apply
```

For an intentional multi-project module, repeat `--notebook-id`:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module cardiology \
  --display-name Cardiology \
  --notebook-id FIRST_NOTEBOOK_UUID \
  --notebook-id SECOND_NOTEBOOK_UUID --apply
```

If the requested title matches several projects, the manager stops with their
IDs so the Agent can ask the user. If it matches none, omitting `--apply` only
previews the project that would be created; `--apply` performs the creation.

Validate a configured module:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" validate --module cardiology
```

## Organize course files

Put local material in the selected module:

```text
modules/cardiology/
├── Lecture/       # recordings, slides, handouts, textbooks
├── Questions/     # past exams and question banks together
└── Transcripts/   # generated Markdown and Index.md
```

The Agent decides whether each `Questions/` file is a `past_exam` (with a
verified year), a `question_bank`, a duplicate, irrelevant, or ambiguous. The
temporary manifest records that decision. The engine validates the paths and
uses the approved classification for badges and source scopes.

Existing modules with a legacy `Exams/` folder can be migrated safely:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" merge-exams --module toxo
```

The command stops before overwriting a conflicting file.

## Source preparation and contextual references

### Agent-supervised module source sync

Before the first transcription in a module, inventory every local source:

```bash
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo --sync-sources --audit-only
```

The inventory is read-only and labels every file `PENDING AGENT REVIEW`. The
Agent then creates a complete manifest outside Git. Every file under `Lecture/`,
`Questions/`, or the legacy `Exams/` folder must be classified, including files
that should be ignored:

```json
{
  "version": 1,
  "module": "toxo",
  "notebook_targets": ["4a9cf6ee-2974-4848-bd5f-6bc5cc5bf7a3"],
  "agent_approved": true,
  "sources": [
    {
      "path": "Lecture/PSYCHOTROPIC DRUGS.ppsx",
      "role": "slides",
      "action": "auto",
      "upload": true,
      "reason": "Toxicology lecture slides"
    }
  ]
}
```

Audit the decisions without writing files or uploading:

```bash
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo --sync-sources \
  --source-sync-manifest /tmp/toxo-source-sync.json --audit-only
```

After the Agent reviews the conversion, ambiguity, and upload plan, apply it:

```bash
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo --sync-sources \
  --source-sync-manifest /tmp/toxo-source-sync.json --apply
```

The apply run records original and prepared SHA-256 values, preparation output,
NotebookLM source IDs, and per-notebook status in
`.transcriber-cache/source-sync/state.json`. It also writes a timestamped run
record under `.transcriber-cache/source-sync/runs/`. A failed file does not undo
successful files; rerunning the same approved manifest reuses deterministic
artifacts and remote matches.

Once a state file exists, every real transcription performs an incremental
preflight. A new, changed, removed, or previously incomplete source blocks the
transcription until the Agent audits and applies a refreshed sync manifest.
Normal source synchronization never deletes or replaces a same-title/different-hash
source automatically; it reports that source as `changed` for an explicit Agent
decision. During a real transcription, however, a NotebookLM source that is
proven non-queryable is eligible for the bounded recovery path: the engine first
requires one unique local uploadable match, deletes only that exact remote UUID,
waits for it to disappear, uploads the local file, and rebuilds the phase scopes
before retrying. If NotebookLM rotated the UUID between audit and recovery, one
unique canonical title/stem match in the live inventory may be reconciled; an
ambiguous or local-missing match remains blocked without deletion.

The Agent owns the judgment call; the engine owns the repeatable file work. Before
Phase 0 it writes a temporary manifest describing which references are relevant,
whether an unspoken detail may be added, and what preparation is allowed. The
read-only audit prints the plan but does not create files. A real run executes the
plan only after the audit passes.

Original files are never overwritten. Derived files go under the module's
`.transcriber-cache/` in `converted/`, `ocr/`, `compressed/`, or `chunks/`; the
cache is ignored by Git and can be reused after a timeout. The engine records a
SHA-256 for local artifacts and skips an upload when the remote inventory exposes
the same hash, name, or compatible converted stem in the selected NotebookLM
project.

Supported preparation decisions include:

- `.ppt`, `.pps`, and `.ppsx` → searchable PDF through LibreOffice.
- Scanned PDFs → searchable OCR PDF through `ocrmypdf` or `pdfocr`; the PDF is
  rechecked for usable text before it can be uploaded.
- Text/Markdown sources → PDF automatically when the selected NotebookLM setup
  does not accept those extensions.
- Unsupported video/audio containers → speech-only `.m4a` through `ffmpeg`, with
  the original part order preserved.
- Large books → retain the original and wait longer (the default threshold is
  80 MiB), or compress/chunk only the Agent-selected relevant pages.

If no preparation tool is available, the run stops with the missing tool and the
original remains intact. The Agent can prepare an OCR PDF itself and list that
derived file with `action: "use"`.

Books, handouts, and slides are not copied wholesale into the lecture. The
recording remains the authority for the doctor's words and chronology; references
verify terminology and can add only a directly useful, Agent-selected detail. An
unspoken detail is rendered with this label:

```markdown
> [!NOTE]
> **إضافة من الكتاب/السلايد — لم يشرحها الدكتور في التسجيل**
> concise contextual addition
```

Conflicts are surfaced for the Agent's editorial review. Reference additions do
not become `IMP` points and are never attributed to the doctor.

## Run a transcription through the Agent

Ask naturally:

```text
اعمل تفريغ لمحاضرة Corrosives في موديول toxo
اعمل كل التفريغات الموجودة في موديول ENT
```

The Agent workflow is:

1. List the selected module and NotebookLM project inventory.
2. Discover local sources and reconcile names, types, stems, hashes, and expected
   NotebookLM conversions.
3. Decide whether a recording has one part or several parts.
4. Inspect prior exams and build an exam-style profile.
5. Select relevant references and write a temporary source manifest outside Git.
6. Run the launcher; its preparation plan is read-only in audit mode.
7. After the audit passes, execute conversion/OCR/compression/chunking, wait for
   each upload to become visible, then run the five sequential queries.
8. Review the five generated sections and the managed `Index.md`; the question
   review must normalize OCR, enforce the learned exam form, and resolve answer
   conflicts before finalization.

## Multi-lecture sub-agent batches

When one request selects at least two pending lecture units, the skill uses the
host's native sub-agents: one worker owns one resolved lecture, while the primary
Agent owns shared source decisions, queueing, acceptance, and finalization.
Multipart recordings are grouped before delegation, so `Part 1` and `Part 2` of
one lecture go to one worker rather than competing workers.

The primary creates and audits every lecture manifest, then initializes an
ignored batch ledger:

```bash
python3 .agents/skills/universal-transcriber/scripts/batch_state.py init \
  --module toxo \
  --cache-root MODULE_ROOT/.transcriber-cache \
  --manifest /tmp/toxo-corrosives.json \
  --manifest /tmp/toxo-volatile-poisons.json
```

The ledger rejects overlapping recording ownership and records queue, worker,
run, draft, failure, and recovery state. Worker capacity is taken from the host;
it is not hardcoded. Each worker runs the five phases sequentially, reviews its
own evidence-rich draft, and stops before finalization. The primary verifies the
handoff and finalizes accepted drafts one at a time.

The launcher uses a stable lecture lock, so different lectures in one module can
run together while a duplicate owner for the same lecture is rejected. Shared
NotebookLM uploads, prepared cache artifacts, and the complete `Index.md`
read-render-commit transaction have narrower locks. A failed lecture remains
resumable without undoing successful siblings.

The complete primary/worker contract and failure matrix are in
[multi-agent.md](.agents/skills/universal-transcriber/references/multi-agent.md).

Read-only inventory commands:

```bash
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --list-modules

python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo --list

python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo \
  --lecture "Corrosives (Parts 1 & 2)" --audit-only
```

An actual run must use the Agent-approved manifest. The launcher rejects a real
run that has only `--lecture` or `--all` because those forms do not contain the
Agent's source decision.

Use the manifest first to create a draft, then let the Agent review it and run
the same command with `--finalize-draft`:

```bash
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo \
  --source-manifest /tmp/corrosives-manifest.json --draft-only

# Agent reviews Transcripts/Corrosives\ (Parts\ 1\ \&\ 2)\ 🧪.md.draft.md
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo \
  --source-manifest /tmp/corrosives-manifest.json --finalize-draft
```

## Source manifest

The Agent creates a temporary JSON file, for example:

```json
{
  "title": "Corrosives (Parts 1 & 2)",
  "recording_sources": ["Corrosive 1.m4a", "Corrosive 2.m4a"],
  "slides": {"path": "Lecture/corrosivesد.سمير.pptx", "action": "use"},
  "references": [
    {
      "path": "Lecture/textbook.pdf",
      "type": "textbook",
      "action": "auto",
      "relevance": "terminology and the mechanism taught in the recording",
      "topics": ["mechanism", "complications"],
      "allow_unspoken_additions": true
    }
  ],
  "assessment_sources": [
    {"path": "Questions/End Toxico 2023.pdf", "type": "past_exam", "year": 2022, "action": "auto"},
    {"path": "Questions/final Toxico 2023.pdf", "type": "past_exam", "year": 2023, "action": "auto"},
    {"path": "Questions/Past Exams Collection.pdf", "type": "past_exam", "years": [2022, 2024, 2025], "action": "auto"},
    {"path": "Questions/Khalsa questions of toxo.pdf", "type": "question_bank", "action": "auto"}
  ],
  "approved_uploads": ["Lecture/Corrosive 2.m4a"],
  "exam_style_profile": {
    "sample_scope": "same college exams and question bank",
    "mcq": {
      "stem_patterns": ["The following ...:-", "... except:-"],
      "options": {"count": 4, "labels": "lowercase a. through d."},
      "max_stem_words": 18,
      "register": "short direct factual stems"
    },
    "written": {
      "command_patterns": ["Causes of ...: 1.... 2....", "Treatment of ..."],
      "answer_shape": "short numbered keywords"
    }
  }
}
```

NotebookLM's upload-safe document set is PDF, PPTX, DOCX, and supported audio;
plain TXT/Markdown, legacy slides, scanned PDFs, and unsupported media are
prepared into a safe derived artifact before upload. `assessment_sources` is the source of truth for exam provenance. A `past_exam`
entry must declare either `year` or `years`; `years` supports one collection file
containing multiple verified exams. `question_bank` entries never receive a
Past Exams year automatically, and `year` plus `years` must agree when both are
provided. Current years such as 2025 and 2026 are accepted only when explicitly
declared and the source is available in the selected NotebookLM project; a year
found only in a filename is not evidence.

Every content-distinct exam paper remains a separate `past_exam`, even when
several papers share one year. End-of-module, semester-final, midyear, group, and
sitting labels describe exam variants; they are not `assessment_sources.type`
values. Do not classify one as `ignore` merely because its year, course, or
filename resembles another retained exam. Use `ignore` only after content review
establishes that a file is not an assessment authority or is an exact duplicate,
and record that evidence in `reason`. Shared questions are only partial overlap:
retain both exams and deduplicate identical questions later while preserving
both sources' provenance. If the filename year conflicts with the date printed
on the paper, use the printed year.

Rules enforced by the launcher and engine:

- `recording_sources` are listed in spoken order; several parts become one
  transcript.
- A recording may be a remote-only NotebookLM source; use an object with
  `action: "use_remote"` when a local copy is intentionally absent.
- `approved_uploads` contains relative local paths such as
  `Lecture/part-2.m4a` or `Questions/final.pdf`, and only files confirmed
  missing and safe to upload. A bare filename is accepted only when it matches
  exactly one local file. Already-present sources are never uploaded again.
- `slides` and assessment paths must stay inside the selected module.
- `references` entries must stay under `Lecture/` or `Questions/`. `relevance`,
  `topics`, `pages`, and `allow_unspoken_additions` are editorial constraints,
  not medical evidence or instructions.
- `action` can be `use`, `use_remote`, `auto`, `convert`, `ocr`, `compress`,
  `chunk`, `wait`, or `ignore`. `chunk` requires explicit one-based `pages`.
- A `slides` entry with `action: "use_remote"` may omit the local slide file;
  `action: "ignore"` removes it from the lecture authority scope.
- `assessment_sources` must explicitly classify every file under `Questions/`
  as `past_exam`, `question_bank`, or `ignore`; a past exam must carry an
  explicit verified year. An unclassified file blocks a real run.
- The style profile describes form only. It does not provide facts, answers, or
  provenance. `max_stem_words` is an observed upper bound for IMP stems, not a
  medical-content limit.
- A malformed, ambiguous, duplicated, or unsafe manifest stops before output.

Past-exam questions that occur in multiple verified years are deduplicated only
when the wording/options are identical or safely equivalent after OCR cleanup.
The result keeps one question, one canonical badge containing all years in
ascending order, and one `Source:` line for each supporting exam. Questions with
different negation, requested counts, options, command verbs, or medical meaning
remain separate; semantic uncertainty is left for Agent review.

Every successful phase is checkpointed under `.transcriber-cache/runs/`. If a
phase fails after its retries, the last response, structured validation errors,
manifest snapshot, and evidence catalog remain in a recovery bundle. Use
`--resume-latest`, `--resume-run RUN_ID`, or `--retry-phase written` to reuse
validated earlier phases and rerun only the failed phase and its dependents.

For source-scoped queries, an invalid or non-queryable source ID is isolated
automatically by recursive group splitting. The run records the exact Notebook
ID, source ID, canonical remote title, and error in the phase checkpoint and
recovery bundle. When a unique local replacement exists, the engine deletes that
exact remote source, uploads the local file, records the old/new IDs, rebuilds
the scopes, and retries the failed phase once immediately; it does not repeat
the same failed source request three times. Only source-specific invalid-
request errors enter this recovery path; timeouts, authentication, transport,
and service errors remain retryable phase failures. If replacement is unsafe or
the retry still fails, the phase remains blocked and no questions are fabricated
or silently generated from recording-only evidence.

The provider's generic `query request is invalid` response is not, by itself,
evidence of a bad source. Assessment prompts now send only a compact canonical
exam/question-bank manifest and retry once with an even shorter prompt when the
provider rejects the query arguments. A source is deleted/replaced only when
the error explicitly identifies that source (or a source-specific group) and a
unique local replacement is available.

When the Agent repairs a failed phase, save the complete repaired section inside
the same run directory (for example, `phase-written-agent-response.md`) and
apply it through the launcher:

```bash
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo \
  --source-manifest /tmp/corrosives-manifest.json \
  --resume-run RUN_ID \
  --recovery-phase written \
  --recovery-response "$PWD/.transcriber-cache/runs/RUN_ID/phase-written-agent-response.md"
```

The engine validates the repaired section against the same provenance, OCR,
exam-style, and duplicate rules before accepting it. A valid repair is marked
`repaired`, dependent phases are rerun, and earlier successful phases are
reused. An invalid repair is kept as a separate rejection artifact and the
original failed response remains untouched. The Agent must resolve
`unsafe_duplicate_merge` candidates explicitly; the engine never merges a
semantic match solely because the stems look similar.

For a careful editorial workflow, pass `--draft-only` to save an evidence-rich
`.draft.md` file. After the Agent normalizes recoverable OCR, puts each option on
its own line, checks every answer against its options, matches IMP questions to
the observed exam style, removes repeated questions and editorial residue, and
leaves no unresolved review marker, run the same manifest with
`--finalize-draft` to validate and atomically publish the student-facing Markdown
and `Index.md`. A successful finalize deletes only that lecture's draft; a failed
validation or commit keeps it for repair.

## Exam style and question generation

The Agent samples prior exams in this order: the same lecture/module, the same
college or course, question banks, then other modules if needed. It records
recurring MCQ stem and option conventions and written command/answer shape,
including an evidence-based `max_stem_words` bound when the exams are short and
direct.

Sourced questions remain semantically verbatim after obvious OCR normalization:
split letters, joined words, broken labels, and NotebookLM citation residue are
repaired, but the wording is not paraphrased. IMP questions use the learned form
without copying the sample's content. This is why a direct past-exam pattern such as
`Early complication of corrosion:- 1............` remains direct instead of
being rewritten as a long academic essay prompt.

For MCQs, sourced items use `Question (verbatim)` and `Options (verbatim)`, while
Agent-created IMP items use `Question` and `Options`. The engine blocks obvious
OCR spacing, malformed option lists, answer labels that do not exist, unresolved
review markers, and IMP stems that exceed the supplied style profile.

The full observation guide is in
[exam-style.md](.agents/skills/universal-transcriber/references/exam-style.md).

## Output and privacy

Generated files are written to the selected module's `Transcripts/` directory;
the final `Index.md` is updated atomically with the transcript. During the
draft/finalize workflow, the draft retains evidence fields for validation, then
the student-facing document removes `Source:` lines and must not expose local
filenames, source IDs, or upload details; supported years and canonical badges
remain. The Chronological Guide stays complete; only answer explanations, short
model answers, and case answers are intentionally concise. Any selected book or
slide addition remains visibly labeled as not explained in the recording.

Course data, recordings, PDFs, generated transcripts, NotebookLM credentials,
and private project IDs are excluded from this repository by `.gitignore`.
Review `git status` before committing or opening a PR.

## Troubleshooting

`nlm` is not found: install the external CLI, put it on `PATH`, then run
`nlm --version`.

Authentication fails: run `nlm login` or `nlm login --check` and select the
correct profile with `--nlm-profile` where supported.

No project is found: let the Agent apply module creation; it will create a new
project. Several matching projects: stop and ask the user which project(s) to
use, then repeat `--notebook-id` for an intentional combination.

The audit reports a duplicate: do not add it to `approved_uploads`; the remote
copy will be used. An ambiguous match requires Agent review.

OCR fails: repair or replace the local PDF/DOCX text layer, or confirm that a
usable remote copy already exists. For a new upload, add an explicit `ocr` action
and install `ocrmypdf`/`pdfocr`, or provide an Agent-created searchable PDF with
`action: "use"`. The engine blocks unsafe new uploads.

Legacy slides or media fail to prepare: install the tool named in the audit
(`libreoffice`/`soffice` or `ffmpeg`) and rerun the same manifest. The original
file is still untouched.

A large book is slow: leave it with `action: "wait"` to use the extended upload
wait, or choose `compress`/`chunk` with a reason and relevant pages. Compression
is kept only when it produces a smaller PDF; `chunk` never guesses pages.

The launcher rejects a real run: confirm that a temporary
`--source-manifest` exists, has a non-empty `exam_style_profile`, and lists
recordings in NotebookLM's exact spoken order.

## Developer checks

Run the repository tests without touching course data:

```bash
PYTHONPYCACHEPREFIX=/tmp/codex-pycache \
  python3 -m unittest discover -s tests -p 'test_*.py'
PYTHONPYCACHEPREFIX=/tmp/codex-pycache \
  python3 -m py_compile \
    universal_transcriber/universal_transcribe.py \
    universal_transcriber/source_preparation.py \
    universal_transcriber/source_sync.py \
    .agents/skills/universal-transcriber/scripts/batch_state.py \
    .agents/skills/universal-transcriber/scripts/run_transcription.py \
    .agents/skills/universal-transcriber/scripts/module_registry.py \
    .agents/skills/universal-transcriber/scripts/manage_modules.py
git diff --check
```

The CLI references are always available with:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py --help
python3 .agents/skills/universal-transcriber/scripts/batch_state.py --help
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py --help
python3 universal_transcriber/universal_transcribe.py --help
```
