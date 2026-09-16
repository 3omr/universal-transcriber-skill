"""Coverage for the anki skill's extraction and card formatting.

transcript_concept_extractor.py (373 lines) and card_builder.py (255) had
one test each.  These exercise the seven-pillar classifier, the section
split, deduplication, and the HTML each card kind produces -- using a
transcript shaped like the real five-section standard.
"""

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "transcriber-anki" / "scripts"
)
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from card_builder import format_card_back_html, format_card_front_html
from transcript_concept_extractor import (
    PILLAR_CONFIG,
    TranscriptConceptExtractor,
    extract_badge,
    generate_blueprint_for_transcript,
)

TRANSCRIPT = """# 🧪 Organophosphates (Toxicology)

## 📖 Chronological Guide

### Mechanism of toxicity
Organophosphates inhibit acetylcholinesterase irreversibly.

> [!IMPORTANT]
> **Definition of organophosphate poisoning:**
> A cholinergic crisis caused by irreversible cholinesterase inhibition.

## 🌟 IMP Points

#### 🎯 Doctor Emphasis
- Atropine is the physiological antidote.

#### ⚠️ Diagnostic Traps
> [!WARNING]
> - Do not miss the garlic smell.

## ❓ MCQs

### MCQ 1 **[Past Exams - 2022]**

**Question:** Atropine is used as an antidote in:
**Options:**
a. Organophosphates.
b. Opium.
c. Iron.
d. Lead.
**Correct Answer:** a. Organophosphates.
**Clinical Explanation:** شرح بالعربي.

## ✍️ Written Questions

### Question 1 **[Past Exams - 2023]**

**Question:** Treatment of acute organophosphate poisoning:
**Model Answer:**
1- Atropine until atropinization
2- Pralidoxime within 24h
3- Decontamination and airway support
- Contraindicated: Morphine, Aminophylline

**Clinical Explanation:** شرح بالعربي.

### Question 2 **[Past Exams - 2021]**

**Question:** Complications of organophosphate poisoning:
**Model Answer:**
1- Respiratory failure
2- Intermediate syndrome
3- Delayed neuropathy

**Clinical Explanation:** شرح بالعربي.

## 🩺 Clinical Cases

### Clinical Case 1 **[IMP]**

**Scenario:** A farmer presents with pinpoint pupils and excessive salivation.
**Questions:** 1. Diagnosis? 2. Treatment?
**Model Answer:**
1- Organophosphate poisoning
2- Atropine and pralidoxime

**Clinical Explanation:** شرح بالعربي.
"""


class ExtractorFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "OPs 🧪.md"
        self.path.write_text(TRANSCRIPT, encoding="utf-8")

    def extractor(self) -> TranscriptConceptExtractor:
        return TranscriptConceptExtractor(self.path, module_id="toxo")


class SectionSplitTests(ExtractorFixture):
    def test_the_five_sections_are_separated(self) -> None:
        extractor = self.extractor()
        extractor.load_and_split_sections()

        joined = " ".join(extractor.sections)
        for marker in ("MCQ", "Written", "Case"):
            self.assertTrue(
                any(marker.casefold() in key.casefold() for key in extractor.sections),
                f"{marker} missing from {joined}",
            )

    def test_the_lecture_title_drops_the_emoji(self) -> None:
        self.assertEqual(self.extractor().lecture_title, "OPs")

    def test_the_module_id_is_honoured(self) -> None:
        self.assertEqual(self.extractor().module_id, "toxo")


class PillarClassifierTests(ExtractorFixture):
    def test_every_pillar_it_returns_is_configured(self) -> None:
        extractor = self.extractor()
        stems = [
            "Treatment of acute poisoning:",
            "Complications of poisoning:",
            "Definition of cholinergic crisis:",
            "Types of organophosphates:",
            "Mechanism of action:",
            "Clinical picture of poisoning:",
            "Past exam question:",
        ]
        for stem in stems:
            with self.subTest(stem=stem):
                self.assertIn(extractor._classify_category(stem, []), PILLAR_CONFIG)

    def test_treatment_stems_reach_the_ttt_pillar(self) -> None:
        extractor = self.extractor()

        for stem in ("Treatment of X:", "Management of X:", "Antidote of X:"):
            with self.subTest(stem=stem):
                self.assertEqual(extractor._classify_category(stem, []), "TTT")

    def test_complication_stems_reach_the_complications_pillar(self) -> None:
        extractor = self.extractor()

        for stem in ("Complications of X:", "Causes of death in X:"):
            with self.subTest(stem=stem):
                self.assertEqual(
                    extractor._classify_category(stem, []), "complications"
                )

    def test_an_unrecognised_stem_falls_back_to_past_exams(self) -> None:
        self.assertEqual(
            self.extractor()._classify_category("Something entirely novel:", []),
            "past_exams",
        )


class BadgeTests(unittest.TestCase):
    def test_a_year_badge_is_read(self) -> None:
        self.assertEqual(
            extract_badge("### Question 1 **[Past Exams - 2023]**"), "Past Exams - 2023"
        )

    def test_an_imp_badge_is_read(self) -> None:
        self.assertEqual(extract_badge("### MCQ 1 **[IMP]**"), "IMP")

    def test_a_combined_badge_is_read_whole(self) -> None:
        self.assertEqual(
            extract_badge("### MCQ 1 **[Past Exams (2022) / IMP]**"),
            "Past Exams (2022) / IMP",
        )

    def test_no_badge_returns_none(self) -> None:
        self.assertIsNone(extract_badge("### MCQ 1"))


class BlueprintTests(ExtractorFixture):
    def test_written_questions_become_cards(self) -> None:
        cards = self.extractor().extract_full_blueprint()

        fronts = " ".join(card["front"] for card in cards)
        self.assertIn("Treatment of acute organophosphate poisoning", fronts)

    def test_every_card_carries_the_required_fields(self) -> None:
        for card in self.extractor().extract_full_blueprint():
            with self.subTest(card=card.get("id")):
                self.assertTrue(card["front"].strip())
                self.assertTrue(card["back_bullets"])
                self.assertEqual(card["module"], "toxo")
                self.assertIn(card["category"], PILLAR_CONFIG)

    def test_duplicate_fronts_are_collapsed(self) -> None:
        doubled = TRANSCRIPT + TRANSCRIPT.split("## ✍️ Written Questions", 1)[1]
        path = self.path.parent / "Doubled 🧪.md"
        path.write_text(doubled, encoding="utf-8")

        cards = generate_blueprint_for_transcript(str(path), module_id="toxo")
        fronts = [card["front"] for card in cards]

        self.assertEqual(len(fronts), len(set(fronts)))

    def test_the_helper_entry_point_matches_the_class(self) -> None:
        by_class = self.extractor().extract_full_blueprint()
        by_helper = generate_blueprint_for_transcript(str(self.path), module_id="toxo")

        self.assertEqual(len(by_class), len(by_helper))


class CardHtmlTests(unittest.TestCase):
    WRITTEN_CARD = {
        "id": "OPS-TTT-01",
        "category": "TTT",
        "badge": "💊 Treatment / TTT",
        "front": "Treatment of acute organophosphate poisoning:",
        "back_bullets": [
            "Atropine until atropinization",
            "Pralidoxime within 24h",
            "Contraindicated: Morphine, Aminophylline",
        ],
        "lecture": "OPs",
        "module": "toxo",
    }

    MCQ_CARD = {
        "id": "OPS-PAST-01",
        "category": "past_exams",
        "badge": "🎯 Past Exam",
        "front": (
            "Atropine is used as an antidote in:\n\n"
            "a. Organophosphates.\nb. Opium.\nc. Iron.\nd. Lead."
        ),
        "back_bullets": ["a. Organophosphates."],
        "lecture": "OPs",
        "module": "toxo",
    }

    def test_the_front_carries_the_badge_and_the_lecture(self) -> None:
        front = format_card_front_html(self.WRITTEN_CARD)

        self.assertIn("Treatment / TTT", front)
        self.assertIn("badge-ttt", front)
        self.assertIn("OPs", front)

    def test_mcq_options_are_rendered_as_their_own_block(self) -> None:
        front = format_card_front_html(self.MCQ_CARD)

        self.assertIn("options-block", front)
        self.assertIn("a. Organophosphates.", front)
        self.assertIn("<br>", front)

    def test_contraindications_get_their_own_warning_box(self) -> None:
        back = format_card_back_html(self.WRITTEN_CARD)

        self.assertIn("warning-box", back)
        self.assertIn("Contraindications", back)
        self.assertIn("Morphine", back)

    def test_ordinary_bullets_stay_in_the_answer_list(self) -> None:
        back = format_card_back_html(self.WRITTEN_CARD)
        answer_list = back.split('<ol class="model-answer-list">', 1)[1].split("</ol>")[0]

        self.assertIn("Atropine until atropinization", answer_list)
        self.assertNotIn("Contraindicated", answer_list)

    def test_html_in_card_text_is_escaped(self) -> None:
        card = {**self.WRITTEN_CARD, "front": "<script>alert(1)</script>"}

        front = format_card_front_html(card)

        self.assertNotIn("<script>", front)
        self.assertIn("&lt;script&gt;", front)

    def test_a_card_with_no_bullets_still_renders(self) -> None:
        card = {**self.WRITTEN_CARD, "back_bullets": []}

        back = format_card_back_html(card)

        self.assertIn("card-container", back)
        self.assertNotIn("model-answer-list", back)


if __name__ == "__main__":
    unittest.main()
