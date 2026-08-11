# Universal Transcriber Skill

Antigravity workspace skill for turning NotebookLM medical-course sources into
validated five-section Markdown transcripts. The repository contains the skill,
its launcher, and the transcription engine. It intentionally excludes lecture
recordings, slides, exams, question banks, generated transcripts, OCR backups,
and local credentials.

## Included files

```text
.agents/skills/universal-transcriber/SKILL.md
.agents/skills/universal-transcriber/scripts/run_transcription.py
universal_transcriber/universal_transcribe.py
universal_transcriber/config.example.json
```

## Prerequisites

- Python 3.10 or newer.
- An authenticated `nlm` CLI that supports `source list <notebook-id> --json`.
- A local PleasePrompto NotebookLM MCP wrapper that exposes
  `list_notebooks`, `get_notebook`, `add_notebook`, `source_add`, and
  `ask_question`.
- `pdfinfo` and `pdftotext` for the PDF text-layer audit.
- A course directory containing `Lecture/` plus at least one of `Questions/` or
  `Exams/` for automatic discovery. `Transcripts/` is created when needed.

## Configure

Create the local configuration, then replace the placeholders:

```bash
cp universal_transcriber/config.example.json universal_transcriber/config.json
```

`config.json` is ignored by Git. Set the NotebookLM UUID for each subject and
the absolute path to the local MCP wrapper.

## Use with Antigravity

Open the repository as the Antigravity workspace, add the course directories,
and ask:

```text
اعمل تفريغ
```

That phrase instructs the skill to process every pending recording. To run one
recording, say:

```text
اعمل تفريغ لمحاضرة Plant poisons
```

The equivalent launcher commands are:

```bash
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --all

python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --lecture "Plant poisons.mp3"
```

For a read-only inventory check:

```bash
python3 .agents/skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --list
```

If the MCP notebook library is empty, the launcher exits with a
`[Launcher Error]`. Registering the existing NotebookLM URL requires an explicit
approved rerun with `--register-notebook`. OCR failures also stop generation;
the engine does not replace source documents automatically.

## Output contract

The engine runs and validates these sections sequentially:

1. `📖 Chronological Guide`
2. `🌟 IMP Points`
3. `❓ MCQs`
4. `✍️ Written Questions`
5. `🩺 Clinical Cases`

The transcript and `Transcripts/Index.md` are committed only after the complete
document passes validation.
