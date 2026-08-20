#!/usr/bin/env python3
"""
test_anki_generator.py
----------------------
Unit tests for the transcriber-anki skill.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "skills" / "transcriber-anki" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from transcript_concept_extractor import TranscriptConceptExtractor
from card_builder import format_card_front_html, format_card_back_html
from deck_exporter import DeckExporter


class TestAnkiGenerator(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.sample_transcript = self.test_dir / "Test_Lecture 🧪.md"
        
        sample_md = """# 🧪 Test Lecture (Toxicology)

## 📖 Chronological Guide

### Definition of Corrosives
Substances causing direct corrosion of tissues.

> [!IMPORTANT]
> **Mechanism of tissue necrosis:**
> Acids cause coagulative necrosis. Alkalis cause liquefactive necrosis.

## ✍️ Written Questions

### Question 1 **[Past Exams - 2023]**

**Question (verbatim):** Early complications of corrosion:
**Model Answer:**
1- Neurogenic / hemorrhagic shock (Day 1)
2- Upper airway obstruction / Laryngeal edema (Day 1)
3- Perforation & septic peritonitis (Week 1)
4- Starvation & esophageal stricture (Week 3)

**Clinical Explanation:**
Explanation text in Arabic.

### Question 2 **[Past Exams - 2022]**

**Question (verbatim):** Treatment protocol for corrosive poisoning:
**Model Answer:**
1- Airway maintenance (ABCD)
2- Cold milk within 30 min
3- IV Morphine & Steroids
4- Early Endoscopy in 12-24h
- Contraindicated: Emesis, gastric lavage, neutralization

**Clinical Explanation:**
Treatment rationale in Arabic.

## ❓ MCQs

### MCQ 1 **[Past Exams - 2023]**

**Question (verbatim):** When ingested corrosive the following occurs:
**Options (verbatim):**
a. Severe burning pain.
b. Drooling of saliva.
c. All of the above.
**Correct Answer:** c. All of the above.
**Clinical Explanation:**
Arabic explanation.
"""
        with open(self.sample_transcript, "w", encoding="utf-8") as f:
            f.write(sample_md)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_concept_extraction(self):
        extractor = TranscriptConceptExtractor(self.sample_transcript, module_id="test_module")
        cards = extractor.extract_full_blueprint()
        
        self.assertGreaterEqual(len(cards), 3)
        
        categories = [c["category"] for c in cards]
        self.assertIn("complications", categories)
        self.assertIn("TTT", categories)
        
        # Verify pure English front and bullets
        for c in cards:
            self.assertTrue(len(c["front"]) > 0)
            self.assertTrue(len(c["back_bullets"]) > 0)
            self.assertIn(c["module"], ["test_module"])

    def test_card_html_formatting(self):
        card_sample = {
            "id": "TST-TTT-01",
            "category": "TTT",
            "badge": "Treatment / TTT",
            "front": "What is the treatment protocol for corrosive poisoning?",
            "back_bullets": [
                "Airway maintenance (ABCD)",
                "Cold milk within 30 min",
                "IV Morphine & Steroids",
                "Contraindicated: Emesis, gastric lavage, neutralization"
            ],
            "lecture": "Test Lecture",
            "module": "test_module"
        }
        
        front_html = format_card_front_html(card_sample)
        back_html = format_card_back_html(card_sample)
        
        self.assertIn("Treatment / TTT", front_html)
        self.assertIn("What is the treatment protocol", front_html)
        self.assertIn("badge-ttt", front_html)
        
        self.assertIn("Airway maintenance (ABCD)", back_html)
        self.assertIn("Contraindications", back_html)
        self.assertIn("Emesis", back_html)

    def test_deck_export(self):
        extractor = TranscriptConceptExtractor(self.sample_transcript, module_id="test_module")
        cards = extractor.extract_full_blueprint()
        
        out_dir = self.test_dir / "Anki"
        exporter = DeckExporter(module_id="test_module", lecture_title="Test Lecture", output_dir=out_dir)
        
        apkg_path = exporter.export_apkg(cards)
        tsv_path = exporter.export_tsv(cards)
        
        self.assertTrue(apkg_path.exists())
        self.assertGreater(apkg_path.stat().st_size, 1000)
        
        self.assertTrue(tsv_path.exists())
        self.assertGreater(tsv_path.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
