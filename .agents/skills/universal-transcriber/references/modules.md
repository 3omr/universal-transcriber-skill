# Module layout and configuration

The workspace has one canonical `modules/` directory. Each direct child is an
independent medical module. Do not use the misspelling `moduels/`.

```text
modules/
└── cardiology/
    ├── module.json
    ├── Lecture/
    ├── Questions/
    └── Transcripts/
```

`Lecture/` holds local slides, handouts, and optionally recordings.
`Questions/` holds both past exams and question banks. The agent classifies each
file from its content and records that decision in the temporary transcription
manifest as `past_exam`, `question_bank`, or `ignore`. Every file must be listed
for a real run. `Transcripts/` is managed output. `Exams/` is a legacy input name and
is supported only long enough to migrate its files into `Questions/`.

The temporary transcription manifest may also contain a `references` list for
Agent-selected books or handouts. Each entry can specify `relevance`, `topics`,
one-based `pages`, `allow_unspoken_additions`, and a preparation `action`. The
engine keeps originals under `Lecture/`/`Questions/` and writes derived OCR,
conversion, compression, or page-selection artifacts under the module's ignored
`.transcriber-cache/`; it never edits a source in place. The recording remains
the authority for chronology and the doctor's words. A reference is used for
terminology by default; an unspoken detail is included only when the Agent marks
it relevant and allows it, with the visible label “إضافة من الكتاب/السلايد — لم
يشرحها الدكتور في التسجيل”.

## module.json

The manager creates this file; the agent should not ask the user to write it by
hand:

```json
{
  "schema_version": 1,
  "module_id": "ent",
  "display_name": "ENT",
  "aliases": ["ear nose throat", "انف واذن"],
  "notebooks": [
    {"id": "NOTEBOOK_UUID", "title": "ENT"}
  ],
  "notebook_profile": null,
  "output": {
    "emoji": "👂",
    "language": "Egyptian Arabic mixed with English medical terminology"
  },
  "lecture_slides": {
    "recording filename.mp3": "Lecture/corresponding slides.pptx"
  }
}
```

`notebooks` may contain more than one existing NotebookLM project when the user
has explicitly chosen to combine them. The first entry is the primary upload
target; uploads go there only, while each query keeps a separate source scope
for each selected project. A legacy single `notebook` object is still accepted
while modules are migrated.

The directory name and `module_id` must be identical lowercase kebab-case.
Aliases must be unique across all modules. Paths in `lecture_slides` are
relative to the module directory and cannot escape it.

## Agent-owned creation

When the user says they are starting a module, the agent should:

1. Choose a safe module ID and display name.
2. List NotebookLM projects and match by exact title or an explicitly supplied
   ID.
3. Use the only exact match automatically.
4. If no match exists, create a new project on `--apply` with
   `nlm notebook create`.
5. If several projects match, ask whether to select one or combine several;
   never guess.
6. Run the manager, populate the module folders, and validate the result.

Preview mode omits `--apply`; it never creates a project or directory:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module ent --display-name ENT \
  --notebook-title ENT
```

After the agent confirms the project choice, apply it:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module ent --display-name ENT \
  --notebook-title ENT --apply
```

For an intentional multi-project module, repeat `--notebook-id`:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module ent --display-name ENT \
  --notebook-id FIRST_NOTEBOOK_UUID --notebook-id SECOND_NOTEBOOK_UUID --apply
```

## Safe migration

For an existing module that still has `Exams/`, use the manager's migration
command. It moves files into `Questions/` and stops before any conflicting
destination is overwritten:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" merge-exams --module toxo
```

The command does not delete a conflicting file or silently choose between
duplicates. Review a conflict, then rerun after resolving it.

Validate at any time with:

```bash
python3 .agents/skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" validate --module ent
```
