# Source Synchronization and Manifest Contract

The Agent owns source inventory, provenance classification, and preparation decisions. The engine executes approved operations deterministically.

## 1. Module-Wide Source Synchronization

Before transcribing any lecture in a module, perform a complete source sync:

```bash
# Step 1: Inventory local files (Read-only)
python3 skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module <module_id> --sync-sources --audit-only
```

### Sync Manifest Protocol
1. Classify all local files under `Lecture/`, `Questions/`, and legacy `Exams/`.
2. Explicitly mark reviewed exclusions with `action: "ignore"` and `upload: false` — never silently omit files.
3. Save the manifest outside Git (e.g. in `/tmp/<module>-sync-manifest.json`).
4. Re-run audit with the manifest to verify preparation plans (OCR, conversions, chunking):
   ```bash
   python3 skills/universal-transcriber/scripts/run_transcription.py \
     --workspace "$PWD" --module <module_id> \
     --source-sync-manifest /tmp/<module>-sync-manifest.json --audit-only
   ```
5. Set `"agent_approved": true` and apply the changes:
   ```bash
   python3 skills/universal-transcriber/scripts/run_transcription.py \
     --workspace "$PWD" --module <module_id> \
     --source-sync-manifest /tmp/<module>-sync-manifest.json --apply
   ```

### Sync Manifest Schema
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
    },
    {
      "path": "Questions/final Toxico 2023.pdf",
      "role": "assessment",
      "action": "auto",
      "upload": true,
      "reason": "Past exam paper 2023"
    },
    {
      "path": "Lecture/unrelated_notes.docx",
      "role": "reference",
      "action": "ignore",
      "upload": false,
      "reason": "Duplicate summary notes"
    }
  ]
}
```

---

## 2. Lecture-Specific Source Manifest

For every real transcription run, create a temporary source manifest (e.g. `/tmp/<lecture>-manifest.json`). This defines the authority scope for that specific lecture.

### Lecture Manifest Schema
```json
{
  "title": "Corrosives (Parts 1 & 2)",
  "recording_sources": ["Corrosive 1.m4a", "Corrosive 2.m4a"],
  "slides": {
    "path": "Lecture/corrosives_dr_samir.pptx",
    "action": "use"
  },
  "references": [
    {
      "path": "Lecture/textbook.pdf",
      "type": "textbook",
      "action": "auto",
      "relevance": "Terminology and pathophysiological mechanisms taught in recording",
      "topics": ["mechanism", "complications"],
      "allow_unspoken_additions": true
    }
  ],
  "approved_uploads": [
    "Lecture/Corrosive 2.m4a"
  ],
  "assessment_sources": [
    {
      "path": "Questions/End Toxico 2023.pdf",
      "type": "past_exam",
      "year": 2022,
      "action": "auto"
    },
    {
      "path": "Questions/final Toxico 2023.pdf",
      "type": "past_exam",
      "year": 2023,
      "action": "auto"
    },
    {
      "path": "Questions/Khalsa questions of toxo.pdf",
      "type": "question_bank",
      "action": "auto"
    }
  ],
  "exam_style_profile": {
    "sample_scope": "Same college past exams first, then question banks",
    "mcq": {
      "stem_patterns": ["The following ...:-", "... are:", "... except:-"],
      "options": {"count": 4, "labels": "lowercase a. through d."},
      "register": "Short direct factual stems with concise options"
    },
    "written": {
      "command_patterns": ["Causes of ...: 1.... 2....", "Treatment of ...", "Mechanism of ..."],
      "answer_shape": "Numbered keywords matching requested count"
    }
  }
}
```

---

## 3. Preparation Actions & Storage Rules

| Action | Meaning & Execution |
| --- | --- |
| `auto` | Default inspection. Converts PPTX/DOCX/Keynote to PDF, performs OCR if text layer is missing/scanned, and verifies audio codec. |
| `use` | Use existing verified local file without modification. |
| `use_remote` | Match to ready remote NotebookLM conversion (e.g. local PDF matching remote TXT); avoids redundant upload. |
| `convert` | Explicit document format conversion (e.g. TXT/Markdown/PPTX → PDF). |
| `ocr` | Force OCR with `--force-ocr --deskew --language eng+ara` replacing stale text layers. |
| `compress` | Optimize large PDF/audio assets exceeding NotebookLM payload limits. |
| `chunk` | Slice large reference (e.g. textbook chapter) using explicit 1-based `pages: [120, 145]`. |
| `ignore` | Exclude from authority scope and upload. |

### Derived Artifacts Location
Original files under `Lecture/` and `Questions/` are **never modified in place**. All derived conversions, OCR outputs, and chunks are written to the module's ignored cache:
```text
modules/<module_id>/.transcriber-cache/
├── converted/
├── ocr/
├── compressed/
└── chunks/
```

---

## 4. Conflict Resolution & Provenance Rules

1. **Exact Remote Matching**: When a local file matches a remote source by title/hash or ready text conversion, link using `action: "use_remote"`.
2. **Same-Title Conflict Deletion**: When an approved conversion/OCR produces a replacement for an exact same-title remote UUID with conflicting hash, the engine deletes the old UUID, waits for clearance, and uploads the verified replacement.
3. **Ambiguity Protection**: Any ambiguous multi-match or unverified hash halts execution and requires explicit human/agent resolution.
4. **Assessment Source Provenance**:
   - `past_exam`: Requires explicit `year` (or `years`). Never infer years from filenames alone.
   - `question_bank`: Standard question collection without specific exam year binding.
   - Different papers sharing a year (e.g., *End-of-Module* vs *Final*) are distinct provenance entries.
