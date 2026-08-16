# Module Layout and Configuration Reference

The workspace follows a strict modular structure under the canonical `modules/` directory.

```text
modules/
└── <module_id>/
    ├── module.json
    ├── Lecture/          # Slides, audio recordings, handouts, textbooks
    ├── Questions/        # Past exams and question banks
    └── Transcripts/      # Finalized Markdown transcripts and Index.md
```

---

## 1. `module.json` Specification

```json
{
  "schema_version": 1,
  "module_id": "ent",
  "display_name": "ENT",
  "aliases": ["ear nose throat", "انف واذن"],
  "notebooks": [
    {
      "id": "NOTEBOOK_UUID",
      "title": "ENT"
    }
  ],
  "notebook_profile": null,
  "output": {
    "emoji": "👂",
    "language": "Egyptian Arabic mixed with English medical terminology"
  },
  "lecture_slides": {
    "recording_filename.mp3": "Lecture/corresponding_slides.pptx"
  }
}
```

- **Module ID**: Lowercase kebab-case matching folder name.
- **Notebooks**: Array of NotebookLM projects. The first is the primary upload target; all listed notebooks are queried across during synthesis.
- **Lecture Slides**: Optional map pairing audio files to slides.

---

## 2. Module Management Commands

Use `skills/universal-transcriber/scripts/manage_modules.py`:

### List Configured Modules
```bash
python3 skills/universal-transcriber/scripts/manage_modules.py --workspace "$PWD" list
```

### Preview Module Creation
```bash
python3 skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module cardiology --display-name "Cardiology" \
  --notebook-title "Cardiology"
```

### Apply Module Creation
```bash
python3 skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" create --module cardiology --display-name "Cardiology" \
  --notebook-title "Cardiology" --apply
```

### Validate Existing Module
```bash
python3 skills/universal-transcriber/scripts/manage_modules.py \
  --workspace "$PWD" validate --module cardiology
```
