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
3. `MCQs` — verbatim grounded past-exam/question-bank items plus clearly marked
   `IMP` items.
4. `Written Questions` — the same distinction and concise model answers.
5. `Clinical Cases` — short evidence-backed cases.

The lecture explanation is not shortened. Concision is limited to answer
explanations, short model answers, and case answers.

## Requirements

- Python 3.10 or newer for the engine. The current `notebooklm-mcp-cli` release
  requires Python 3.11 or newer for the external `nlm` command.
- An authenticated `nlm` CLI (the project was verified with version 0.9.8).
- A Google NotebookLM account that can access the selected project(s).
- `pdfinfo` and `pdftotext` for PDF text-layer checks.
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

## Run a transcription through the Agent

Ask naturally:

```text
اعمل تفريغ لمحاضرة Corrosives في موديول toxo
اعمل كل التفريغات الموجودة في موديول ENT
```

The Agent workflow is:

1. List the selected module and NotebookLM project inventory.
2. Reconcile local and remote source names, types, stems, and expected
   NotebookLM conversions.
3. Decide whether a recording has one part or several parts.
4. Inspect prior exams and build an exam-style profile.
5. Create a temporary source manifest outside Git.
6. Run the launcher; it audits again before uploading or querying.
7. Review the five generated sections and the managed `Index.md`.

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
  "slides": "Lecture/corrosivesد.سمير.pptx",
  "assessment_sources": [
    {"path": "Questions/End Toxico 2023.pdf", "type": "past_exam", "year": 2023},
    {"path": "Questions/Khalsa questions of toxo.pdf", "type": "question_bank"}
  ],
  "approved_uploads": ["Lecture/Corrosive 2.m4a"],
  "exam_style_profile": {
    "sample_scope": "same college exams and question bank",
    "mcq": {
      "stem_patterns": ["The following ...:-", "... except:-"],
      "options": {"count": 4, "labels": "lowercase a. through d."}
    },
    "written": {
      "command_patterns": ["Causes of ...: 1.... 2....", "Treatment of ..."],
      "answer_shape": "short numbered keywords"
    }
  }
}
```

Rules enforced by the launcher and engine:

- `recording_sources` are listed in spoken order; several parts become one
  transcript.
- `approved_uploads` contains relative local paths such as
  `Lecture/part-2.m4a` or `Questions/final.pdf`, and only files confirmed
  missing and safe to upload. A bare filename is accepted only when it matches
  exactly one local file. Already-present sources are never uploaded again.
- `slides` and assessment paths must stay inside the selected module.
- `assessment_sources` must explicitly classify every file under `Questions/`
  as `past_exam`, `question_bank`, or `ignore`; a past exam must carry an
  explicit verified year. An unclassified file blocks a real run.
- The style profile describes form only. It does not provide facts, answers, or
  provenance.
- A malformed, ambiguous, duplicated, or unsafe manifest stops before output.

For a careful editorial workflow, pass `--draft-only` to save an evidence-rich
`.draft.md` file. After the Agent removes repeated questions, source lines,
filenames, and other editorial residue without shortening the lecture guide,
run the same manifest with `--finalize-draft` to validate and atomically publish
the student-facing Markdown and `Index.md`.

## Exam style and question generation

The Agent samples prior exams in this order: the same lecture/module, the same
college or course, question banks, then other modules if needed. It records
recurring MCQ stem and option conventions and written command/answer shape.

Sourced questions remain verbatim. IMP questions use the learned form without
copying the sample's content. This is why a direct past-exam pattern such as
`Early complication of corrosion:- 1............` remains direct instead of
being rewritten as a long academic essay prompt.

The full observation guide is in
[exam-style.md](.agents/skills/universal-transcriber/references/exam-style.md).

## Output and privacy

Generated files are written to the selected module's `Transcripts/` directory;
the final `Index.md` is updated atomically with the transcript. During the
draft/finalize workflow, the draft retains evidence fields for validation, then
the student-facing document removes `Source:` lines and must not expose local
filenames, source IDs, or upload details; supported years and canonical badges
remain.

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
usable remote copy already exists. The engine blocks unsafe new uploads.

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
    .agents/skills/universal-transcriber/scripts/run_transcription.py \
    .agents/skills/universal-transcriber/scripts/module_registry.py \
    .agents/skills/universal-transcriber/scripts/manage_modules.py
git diff --check
```

The CLI references are always available with:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py --help
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py --help
python3 universal_transcriber/universal_transcribe.py --help
```
