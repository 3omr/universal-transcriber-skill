# AGENTS.md

Instructions, conventions, and terminology for AI agents working in this repository.

---

## Terminology

Use these exact terms. Do not paraphrase or coin synonyms.

| Term | Meaning | Do not use |
| --- | --- | --- |
| **manifest** | The self-contained JSON specification defining lecture sources, references, and exam style | "config", "prompt file", "spec" |
| **source sync** | The module-wide inventory and upload alignment between local files and NotebookLM | "upload script", "syncing files" |
| **audit** | Read-only inspection mode verifying files, conversions, OCR needs, and inventory | "dry-run", "pre-check" |
| **draft** (`.draft.md`) | Evidence-rich intermediate markdown produced before editorial review and finalization | "raw output", "temp file" |
| **editorial review** | Agent-supervised inspection for Egyptian Arabic tone, doctor's explanation completeness, and OCR repair | "post-processing", "manual edit" |
| **finalize** | Atomic validation and commit of the transcript to `Transcripts/` and `Index.md` | "save", "write output" |
| **worker agent** | Native sub-agent assigned to produce and review the draft for exactly one lecture unit | "slave", "subtask" |
| **primary agent** | The orchestrator owning module setup, manifest creation, worker scheduling, and finalization | "master", "main" |
| **ledger** (`batch_state.py`) | The JSON tracking file managing multi-agent batch queues and transitions | "queue db", "lock file" |
| **chronological guide** | Section 1 of the transcript preserving 100% of the lecturer's spoken explanation | "lecture summary", "notes" |

---

## Core Conventions

1. **Progressive Disclosure**:
   - Keep `SKILL.md` files concise (< 200 lines).
   - Detailed specifications, deep schemas, and editorial checklists belong in `references/*.md`.
2. **Never Summarize the Doctor's Explanation**:
   - The Chronological Guide must capture the complete timeline and clinical nuances of the audio recording in natural Egyptian Arabic with English medical terms. Never compress or omit spoken sections.
3. **Strict Badging & Provenance**:
   - Questions must carry verified badges (`**[Past Exams - YYYY]**`, `**[IMP]**`, `**[Question Bank]**`).
   - Sourced past-exam questions must retain verbatim wording after OCR normalization. Never fabricate exam years.
4. **Sub-Agent Boundaries**:
   - Sub-agents operate with 1 worker per lecture unit.
   - Workers run `--draft-only` and return their `.draft.md` handoff. Workers **never** finalize, edit `Index.md`, or call `nlm` directly.
5. **Idempotency & Clean Workspace**:
   - Cache, conversions, OCR files, and ledger states reside in `modules/<module_id>/.transcriber-cache/`.
   - Never commit `.transcriber-cache/`, credentials, or temporary manifests to Git.
6. **Agent-Side In-Flight Repair**:
   - Never repeatedly query NotebookLM when raw text contains OCR artifacts or duplicate questions across years. The Agent repairs joined words and formats questions directly.
7. **Single Source of Truth & Local/Remote Deduplication**:
   - Retain only one clean copy of each document. Immediately delete redundant local formats (e.g. `.ppsx` when `.pdf` is ready) and remove stale/corrupted files from NotebookLM.
8. **Ultra-Concise Keyword Model Answers (Section 4 & 5)**:
   - In Written Questions and Clinical Cases, `Model Answer` must strictly consist of ultra-concise keywords and short phrases (Egyptian exam mark scheme style, 1–5 words per point/bullet). All detailed clinical explanations, physiology, and doctor's remarks belong exclusively in `Clinical Explanation` in Egyptian Arabic.
9. **Subject-Aware Clinical Case Structure (Section 5)**:
   - Clinical Cases must strictly follow the standard Egyptian exam breakdown for the subject (1. Diagnosis & Severity, 2. DDx / Characteristic Clinical Picture, 3. Key Investigations / Lab tests, 4. Treatment / TTT / Antidote / Precautions). Never invent artificial narrative essay sub-questions.
10. **Strict Question-to-Lecture Scope Alignment (Sections 3, 4 & 5)**:
    - During editorial review, the Agent / Worker Agent must cross-check every extracted Past Exam and Question Bank item against Section 1 (Chronological Guide) and the lecture's slide deck.
    - If an assessment question covers topics from another chapter or lecture that were neither explained by the doctor in the audio nor present in the slides (e.g. Firearm wounds in a Mechanical Wounds lecture), it must be pruned/deleted immediately. Questions in the transcript must test only the taught curriculum of that specific lecture. After pruning, re-index question numbers sequentially.

11. **Edit `skills/` Only; `.agents/skills/` Is Generated**:
    - `skills/` is the source tree. `.agents/skills/` is a byte-for-byte mirror for Google Antigravity and Codex, regenerated with `bash scripts/sync-agents-mirror.sh`. Never hand-edit the mirror, and never import from it in tests. CI fails when the two drift.
12. **One Version, One File — And Nobody Bumps It By Hand**:
    - The repository `VERSION` file is the source of truth for `FALLBACK_VERSION` in every skill and for `tests/test_version_checker.py`.
    - **The Release workflow bumps it on merge.** Do not edit `VERSION` in a pull request; `scripts/bump-version.sh <version>` exists for the workflow and for a deliberate manual release, not for routine work.
    - The bump comes from the PR itself: a `release:major` / `release:minor` / `release:patch` / `release:skip` label if one is set, otherwise the conventional-commit type in the PR **title** — `feat` → minor, `fix`/`perf`/`refactor`/`revert` → patch, `feat!:` or a `BREAKING CHANGE:` footer → major, and `chore`/`docs`/`ci`/`test`/`build`/`style` → no release at all. **So write PR titles as conventional commits**; a bot comments the planned version on every PR before it is merged.
    - The decision lives in `scripts/next_version.py` and is covered by `tests/test_next_version.py`. Change the rules there, not in the workflow YAML.
    - Users discover updates through the latest **GitHub release tag**, not the `VERSION` file in the repo — a merge that cuts no release is invisible to anyone who installed the skill.
13. **Preflight External Tooling**:
    - The pipeline shells out to `nlm`, poppler, `ocrmypdf`, LibreOffice, Ghostscript, and `ffmpeg`. When a run fails on a missing tool, run `run_transcription.py --doctor` and report the install hint to the user rather than guessing or working around the gap.
14. **Cross-Platform File Locking**:
    - Never `import fcntl` directly. Use `exclusive_file_lock` from `skills/universal-transcriber/scripts/file_lock.py`, which keeps `flock` semantics on POSIX and falls back to `msvcrt` on Windows.
15. **The Engine Is Being Split, One Module At A Time**:
    - `universal_transcribe.py` is the entry point and the compatibility surface. Extracted modules are imported and **re-exported** from it, so every existing `universal_transcribe.<name>` keeps working. `tests/test_engine_contract.py` pins that surface — if an extraction drops a symbol, it fails and names it.
    - Extracted so far, in dependency order (each imports only from the ones above it): `exam_years.py`, `file_lock.py`, `transcriber_models.py`, `document_verify.py`, `question_prompts.py`, `output_assembly.py`, `question_coverage.py`.
    - Still inside the engine, hardest last: checkpoint/recovery, the phase validators, the nlm transport, and source authority. Extract one per change and run the full suite after each.
    - Never add an import from an extracted module back into `universal_transcribe.py`'s namespace — that is the cycle the layering exists to prevent.
16. **Exam Years Are Provenance**:
    - Parse exam years only through `exam_years.py`. A `**[Past Exams - YYYY]**` badge is a claim that the question came from that year's paper; a year invented anywhere else in the code becomes a fabricated citation in a student's revision notes.

---

## Telegram Transcription Workflow

When a Telegram message asks to transcribe a lecture or generate study materials, operate from this repository root and use the project-local launcher and skills. Resolve the requested module from `modules/<module_id>/module.json`; do not use a sibling checkout or a profile copy.

1. Inspect `Lecture/` and `Questions/`. If they contain no user data, verify if the data was already uploaded to the configured NotebookLM. If so, continue seamlessly in remote-only mode and do not create placeholder files.
2. Build one explicit manifest for the lecture. Multipart recordings belong to one `recording_sources` array in chronological order.
3. Run audit, draft, editorial review, and finalize with checkpoint/recovery. Do not start duplicate runs or re-query completed phases.
4. After successful finalize, send the user the transcript as a Telegram attachment using `MEDIA:/absolute/path/to/Transcripts/<file>.md` and include a short completion message. If Anki flashcards were requested, compile and attach them via `MEDIA:/absolute/path/to/Anki/<file>.apkg`.

---

## Local Agent Symlink

For orchestrators that read `CLAUDE.md`:
```bash
ln -s AGENTS.md CLAUDE.md
```
