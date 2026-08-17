#!/usr/bin/env python3
"""
deck_exporter.py
----------------
Exports generated medical flashcards into:
1. Native Anki Package (.apkg) using genanki.
2. Universal Tab-Separated File (.tsv / .csv) for manual or web import.
"""

import csv
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    import genanki
    GENANKI_AVAILABLE = True
except ImportError:
    GENANKI_AVAILABLE = False

from card_builder import ANKI_CARD_CSS, format_card_front_html, format_card_back_html


def get_deterministic_id(seed_str: str) -> int:
    """Generates a stable 32-bit positive integer ID from a string seed."""
    h = hashlib.md5(seed_str.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def create_anki_model(model_name: str = "Medical High-Yield Written Model") -> Any:
    """Creates a custom genanki.Model with our specialized CSS styling."""
    if not GENANKI_AVAILABLE:
        return None

    model_id = get_deterministic_id(model_name)
    
    model = genanki.Model(
        model_id,
        model_name,
        fields=[
            {"name": "Front"},
            {"name": "Back"},
            {"name": "Category"},
            {"name": "Badge"},
            {"name": "Lecture"},
            {"name": "Module"}
        ],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Front}}",
                "afmt": "{{Back}}"
            }
        ],
        css=ANKI_CARD_CSS
    )
    return model


class DeckExporter:
    def __init__(self, module_id: str, lecture_title: str, output_dir: Path):
        self.module_id = module_id
        self.lecture_title = lecture_title
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Deck naming
        clean_lecture = self.lecture_title.replace("🧪", "").strip()
        self.deck_name = f"Medical::{self.module_id.upper()}::{clean_lecture}"
        self.clean_filename = clean_lecture.replace(" ", "_").replace("(", "").replace(")", "")

    def export_apkg(self, cards: List[Dict[str, Any]]) -> Path:
        """Exports cards to an .apkg file."""
        if not GENANKI_AVAILABLE:
            raise RuntimeError("genanki package is not installed. Please run: pip install genanki")

        deck_id = get_deterministic_id(self.deck_name)
        deck = genanki.Deck(deck_id, self.deck_name)
        model = create_anki_model()

        for c in cards:
            front_html = format_card_front_html(c)
            back_html = format_card_back_html(c)
            category = c.get("category", "past_exams")
            badge = c.get("badge", "")
            lecture = c.get("lecture", self.lecture_title)
            module = c.get("module", self.module_id)

            # Build hierarchical tags
            tags = [
                f"Module::{module}",
                f"Lecture::{self.clean_filename}",
                f"Pillar::{category}"
            ]
            if badge:
                clean_badge_tag = badge.replace(" ", "_").replace("-", "_").replace("[", "").replace("]", "")
                tags.append(f"Badge::{clean_badge_tag}")

            note = genanki.Note(
                model=model,
                fields=[front_html, back_html, category, badge, lecture, module],
                tags=tags
            )
            deck.add_note(note)

        out_path = self.output_dir / f"{self.clean_filename}.apkg"
        package = genanki.Package(deck)
        package.write_to_file(str(out_path))
        return out_path

    def export_tsv(self, cards: List[Dict[str, Any]]) -> Path:
        """Exports cards to a TSV file ready for Anki manual import."""
        out_path = self.output_dir / f"{self.clean_filename}.tsv"
        
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            # Header
            writer.writerow(["#Front", "Back", "Tags"])
            
            for c in cards:
                front_html = format_card_front_html(c).replace("\n", " ")
                back_html = format_card_back_html(c).replace("\n", " ")
                category = c.get("category", "past_exams")
                badge = c.get("badge", "")
                
                tags = f"Module::{self.module_id} Lecture::{self.clean_filename} Pillar::{category}"
                if badge:
                    clean_badge_tag = badge.replace(" ", "_").replace("-", "_").replace("[", "").replace("]", "")
                    tags += f" Badge::{clean_badge_tag}"
                
                writer.writerow([front_html, back_html, tags])

        return out_path
