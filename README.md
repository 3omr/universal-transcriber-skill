# Universal Transcriber Skill

An Antigravity workspace skill that turns any configured medical module's
NotebookLM recordings, slides, references, question banks, and exams into
validated five-section Markdown transcripts. One shared engine serves every
module; course data and credentials remain local and are excluded from Git.

## Requirements

- Python 3.10 or newer.
- An authenticated `nlm` CLI (`notebooklm-mcp-cli`; tested with 0.9.8)
  available on `PATH`.
- NotebookLM MCP configured for Antigravity through `nlm setup`.
- `pdfinfo` and `pdftotext` for PDF text-quality checks.

The engine uses the supported `nlm notebook list/get/query` and `nlm source
list/add` commands. It never creates a NotebookLM notebook.

## Workspace layout

```text
.agents/skills/universal-transcriber/
├── SKILL.md
├── agents/openai.yaml
├── references/modules.md
└── scripts/
    ├── manage_modules.py
    ├── module_registry.py
    └── run_transcription.py
modules/
└── <module-id>/
    ├── module.json
    ├── Lecture/
    ├── Questions/
    ├── Exams/
    └── Transcripts/
universal_transcriber/
├── config.example.json
└── universal_transcribe.py
```

## Add a module

The manager links a local module to an existing NotebookLM notebook. It is a dry
run unless `--apply` is supplied:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module ent --display-name ENT \
  --notebook-title ENT

python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module ent --display-name ENT \
  --notebook-title ENT --apply
```

Then put that module's files in `Lecture/`, `Questions/`, and `Exams/`. Optional
recording-to-slide mappings live in `module.json`; see the bundled module
reference for the full schema.

If the notebook title or ID is missing or ambiguous, the manager exits with a
`[Module Error]` before creating any directory. Check `nlm notebook list`, then
repeat the dry run with an exact `--notebook-id`.

## Use with Antigravity

Open this repository as the workspace and ask naturally:

```text
اعمل تفريغ لمحاضرة Volatile poison في موديول toxo
اعمل كل تفريغات موديول ENT
```

Equivalent commands:

```bash
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo --lecture "Volatile poison"

python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module ent --all
```

Inventory and read-only audit commands:

```bash
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --list-modules

python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo --list

python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module toxo --lecture "Volatile poison" --audit-only
```

Each normal run audits OCR and the live NotebookLM inventory, uploads only
missing files, runs the five phases sequentially, validates the complete
document, and atomically updates the module's transcript and `Index.md`.
