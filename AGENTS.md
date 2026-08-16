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

---

## Local Agent Symlink

For orchestrators that read `CLAUDE.md`:
```bash
ln -s AGENTS.md CLAUDE.md
```
