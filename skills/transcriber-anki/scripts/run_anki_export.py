#!/usr/bin/env python3
"""
run_anki_export.py
------------------
Main CLI launcher for the transcriber-anki skill.

Modes:
1. Blueprint Mode (--blueprint-only): Scans transcripts and outputs an itemized proposal table + JSON.
2. Direct Generate Mode (--generate): Scans transcripts and generates .apkg & .tsv directly.
3. From Blueprint Mode (--from-blueprint): Compiles an edited/approved JSON blueprint into .apkg & .tsv.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

# Add script dir to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from transcript_concept_extractor import TranscriptConceptExtractor
from deck_exporter import DeckExporter
from version_checker import __version__, print_update_notice_if_available


def print_blueprint_table(lecture_title: str, cards: List[Dict[str, Any]]) -> None:
    """Prints a beautiful formatted ASCII review table for user inspection."""
    clean_title = lecture_title.replace("🧪", "").strip()
    print("\n" + "=" * 90)
    print(f" 🎴 PROPOSED ANKI CARDS BLUEPRINT FOR: {clean_title} ({len(cards)} Cards)")
    print("=" * 90)
    print(f"{'#':<3} | {'Pillar':<14} | {'Question (Front)':<40} | {'Key Model Answer Points (Back)'}")
    print("-" * 90)

    for i, c in enumerate(cards, 1):
        cat = c.get("category", "")
        front = c.get("front", "").replace("\n", " ")
        if len(front) > 38:
            front = front[:35] + "..."
        
        bullets = c.get("back_bullets", [])
        back_summary = " | ".join(bullets) if bullets else "N/A"
        if len(back_summary) > 40:
            back_summary = back_summary[:37] + "..."

        print(f"{i:<3} | {cat:<14} | {front:<40} | {back_summary}")

    print("=" * 90 + "\n")


def find_transcript_files(workspace_dir: Path, module_id: str, lecture_name: Optional[str] = None) -> List[Path]:
    """Finds target transcript Markdown files under modules/<module_id>/Transcripts/."""
    transcripts_dir = workspace_dir / "modules" / module_id / "Transcripts"
    if not transcripts_dir.is_dir():
        raise FileNotFoundError(f"Transcripts directory not found: {transcripts_dir}")

    all_files = sorted(transcripts_dir.glob("*.md"))
    # Filter out index and drafts
    valid_files = [
        f for f in all_files 
        if not f.name.startswith("Index") and not f.name.endswith(".draft.md") and not f.name.startswith(".")
    ]

    if lecture_name:
        matched = [f for f in valid_files if lecture_name.lower() in f.name.lower()]
        if not matched:
            raise FileNotFoundError(f"No transcript found matching '{lecture_name}' in {transcripts_dir}")
        return matched

    return valid_files


def process_lecture(
    transcript_file: Path,
    module_id: str,
    output_dir: Path,
    blueprint_dir: Path,
    mode: str
) -> Dict[str, Any]:
    """Processes a single transcript file according to mode."""
    extractor = TranscriptConceptExtractor(transcript_file, module_id=module_id)
    cards = extractor.extract_full_blueprint()
    clean_title = extractor.lecture_title

    # Save blueprint JSON
    blueprint_file = blueprint_dir / f"{clean_title}.blueprint.json"
    with open(blueprint_file, "w", encoding="utf-8") as f:
        json.dump(cards, f, indent=2, ensure_ascii=False)

    print_blueprint_table(clean_title, cards)

    result = {
        "lecture": clean_title,
        "cards_count": len(cards),
        "blueprint_path": str(blueprint_file),
        "apkg_path": None,
        "tsv_path": None
    }

    if mode == "generate":
        exporter = DeckExporter(module_id=module_id, lecture_title=clean_title, output_dir=output_dir)
        apkg_file = exporter.export_apkg(cards)
        tsv_file = exporter.export_tsv(cards)
        result["apkg_path"] = str(apkg_file)
        result["tsv_path"] = str(tsv_file)
        print(f"  ✅ Exported Anki Deck: {apkg_file}")
        print(f"  ✅ Exported TSV Deck:  {tsv_file}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Generate high-yield English medical Anki flashcards from lecture transcripts.")
    parser.add_argument("--workspace", default=".", help="Path to repository workspace root")
    parser.add_argument("--module", required=True, help="Module ID (e.g. toxo)")
    parser.add_argument("--lecture", help="Specific lecture title or filename substring")
    parser.add_argument("--all-lectures", action="store_true", help="Process all finalized transcripts in module")
    parser.add_argument("--blueprint-only", action="store_true", help="Only scan and display/save proposed card blueprint table")
    parser.add_argument("--generate", action="store_true", help="Directly generate .apkg and .tsv files")
    parser.add_argument("--from-blueprint", help="Path to an approved/edited blueprint JSON file to compile into Anki decks")
    parser.add_argument("--output-dir", help="Custom output directory for Anki decks")
    parser.add_argument("--version", action="version", version=f"transcriber-anki {__version__}")
    parser.add_argument("--no-update-check", action="store_true", help="Skip checking for newer versions")

    args = parser.parse_args()

    workspace_root = Path(args.workspace).resolve()
    print_update_notice_if_available(workspace=workspace_root, quiet=args.no_update_check)
    module_id = args.module
    
    # Destination directories
    default_out = workspace_root / "modules" / module_id / "Anki"
    output_dir = Path(args.output_dir).resolve() if args.output_dir else default_out
    output_dir.mkdir(parents=True, exist_ok=True)

    blueprint_dir = workspace_root / "modules" / module_id / ".transcriber-cache" / "anki_blueprints"
    blueprint_dir.mkdir(parents=True, exist_ok=True)

    # 1. Compile from existing approved blueprint JSON
    if args.from_blueprint:
        bp_path = Path(args.from_blueprint).resolve()
        if not bp_path.exists():
            print(f"❌ Blueprint file not found: {bp_path}")
            sys.exit(1)
        
        with open(bp_path, "r", encoding="utf-8") as f:
            cards = json.load(f)
        
        lecture_title = bp_path.stem.replace(".blueprint", "")
        exporter = DeckExporter(module_id=module_id, lecture_title=lecture_title, output_dir=output_dir)
        apkg_file = exporter.export_apkg(cards)
        tsv_file = exporter.export_tsv(cards)
        print(f"\n🎉 Successfully compiled {len(cards)} cards from approved blueprint:")
        print(f"  📦 Anki Package: {apkg_file}")
        print(f"  📄 TSV Package:  {tsv_file}\n")
        return

    # 2. Extract from Transcripts
    mode = "generate" if args.generate else "blueprint"
    target_files = find_transcript_files(workspace_root, module_id, args.lecture)

    print(f"\n🔍 Found {len(target_files)} lecture transcript(s) in module '{module_id}'.")
    
    results = []
    for tf in target_files:
        res = process_lecture(
            transcript_file=tf,
            module_id=module_id,
            output_dir=output_dir,
            blueprint_dir=blueprint_dir,
            mode=mode
        )
        results.append(res)

    print(f"\n✨ Summary: Processed {len(results)} lecture(s).")
    if mode == "blueprint":
        print("👉 Review the proposed blueprint tables above. You can proceed with generation by approving or modifying items.")


if __name__ == "__main__":
    main()
