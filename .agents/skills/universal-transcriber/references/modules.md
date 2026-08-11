# Module layout and configuration

The workspace has one canonical `modules/` directory. Each direct child is an
independent medical module. Do not use the misspelling `moduels/`.

```text
modules/
└── toxo/
    ├── module.json
    ├── Lecture/
    ├── Questions/
    ├── Exams/
    └── Transcripts/
```

`Lecture/` holds local slides, handouts, and optionally recordings.
`Questions/` holds question banks. `Exams/` holds past exams. `Transcripts/` is
managed output. The launcher scans only the selected module, so sources never
cross between modules.

## module.json

```json
{
  "schema_version": 1,
  "module_id": "ent",
  "display_name": "ENT",
  "aliases": ["ear nose throat", "انف واذن"],
  "notebook": {
    "id": "NOTEBOOK_UUID",
    "title": "ENT",
    "profile": null
  },
  "output": {
    "emoji": "👂",
    "language": "Egyptian Arabic mixed with English medical terminology"
  },
  "lecture_slides": {
    "recording filename.mp3": "Lecture/corresponding slides.pptx"
  }
}
```

The directory name and `module_id` must be identical lowercase kebab-case.
Aliases must be unique across all modules. Configure an existing NotebookLM
notebook by exact UUID whenever possible; the manager verifies it through `nlm
notebook list/get`. `profile` is optional and selects an existing authenticated
`nlm` profile.

Add `lecture_slides` entries only when recording and deck names are semantically
related but cannot be matched reliably by filename. Paths are relative to the
module directory and cannot escape it.

## Safe creation

Preview first by omitting `--apply`:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module ent --display-name ENT \
  --alias "انف واذن" --notebook-title ENT
```

After reviewing the resolved notebook and destination, repeat with `--apply`,
then place course data in the three input folders. Validate at any time with:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" validate --module ent
```
