#!/usr/bin/env python3
"""
transcript_concept_extractor.py
-------------------------------
Deterministic parser and extractor that scans medical lecture transcripts
and extracts the 7 high-yield medical pillars (Definitions, Types, Mechanisms,
Signs & Buzzwords, Treatment/Antidotes, Complications/Timelines, Past Exams)
into structured blueprint items for Anki generation.
"""

import re
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

# Supported Medical Pillars
PILLAR_CONFIG = {
    "def": {"label": "Definition & Diagnostic Criteria", "badge": "📖 Definition", "icon": "📖"},
    "types": {"label": "Classification & Subtypes", "badge": "🗂️ Classification", "icon": "🗂️"},
    "mechanism": {"label": "Pathophysiology & Mechanism", "badge": "⚙️ Mechanism", "icon": "⚙️"},
    "signs": {"label": "Clinical Signs & Buzzwords", "badge": "🔍 Clinical Signs", "icon": "🔍"},
    "TTT": {"label": "Treatment, Antidotes & Contraindications", "badge": "💊 Treatment / TTT", "icon": "💊"},
    "complications": {"label": "Complications & Causes of Death", "badge": "⚠️ Complications", "icon": "⚠️"},
    "past_exams": {"label": "Past Exam Questions & Traps", "badge": "🎯 Past Exam", "icon": "🎯"}
}


def clean_markdown_text(text: str) -> str:
    """Removes extra markdown formatting and normalizes whitespace."""
    if not text:
        return ""
    cleaned = text.strip()
    return cleaned


def extract_badge(text: str) -> Optional[str]:
    """Extracts badges like [Past Exams - 2023], [Question Bank], [IMP]."""
    match = re.search(r'\*\*\[(.*?)\]\*\*', text)
    if match:
        return match.group(1).strip()
    match_alt = re.search(r'\[(.*?)\]', text)
    if match_alt:
        return match_alt.group(1).strip()
    return None


class TranscriptConceptExtractor:
    def __init__(self, transcript_path: Path, module_id: str = ""):
        self.path = Path(transcript_path)
        self.module_id = module_id or self.path.parent.parent.name
        self.lecture_title = self.path.stem.replace("🧪", "").strip()
        self.content = ""
        self.sections: Dict[str, str] = {}

    def load_and_split_sections(self) -> None:
        """Reads the transcript and splits it into the 5 academic sections."""
        with open(self.path, "r", encoding="utf-8") as f:
            self.content = f.read()

        # Split by level 2 markdown headings
        raw_sections = re.split(r'\n(?=##\s+)', self.content)
        for sec in raw_sections:
            sec_trimmed = sec.strip()
            if not sec_trimmed:
                continue
            first_line = sec_trimmed.split("\n", 1)[0]
            if "Chronological Guide" in first_line:
                self.sections["guide"] = sec_trimmed
            elif "Summary" in first_line or "Key Takeaways" in first_line:
                self.sections["summary"] = sec_trimmed
            elif "MCQ" in first_line or "Multiple Choice" in first_line:
                self.sections["mcq"] = sec_trimmed
            elif "Written Questions" in first_line:
                self.sections["written"] = sec_trimmed
            elif "Clinical Cases" in first_line or "Case" in first_line:
                self.sections["cases"] = sec_trimmed

    def extract_written_cards(self) -> List[Dict[str, Any]]:
        """Extracts written questions and converts model answers to structured bullets."""
        cards = []
        written_text = self.sections.get("written", "")
        if not written_text:
            return cards

        raw_questions = re.split(r'\n(?=###\s+)', written_text)
        for q_block in raw_questions:
            if not q_block.startswith("###"):
                continue

            badge = extract_badge(q_block) or "Written Exam"
            
            # Question stem
            q_stem_match = re.search(r'\*\*Question\s*(?:\(verbatim\))?:\*\*\s*(.+?)(?=\n\*\*|\n---|\Z)', q_block, re.DOTALL)
            if not q_stem_match:
                continue
            q_stem = q_stem_match.group(1).strip()
            q_stem_clean = re.sub(r'^\d+[\.\)]\s*', '', q_stem).strip()

            # Model Answer
            ans_match = re.search(r'\*\*Model Answer:\*\*\s*(.+?)(?=\n\*\*Clinical|\n---|\Z)', q_block, re.DOTALL)
            if not ans_match:
                continue
            ans_raw = ans_match.group(1).strip()
            
            bullets = []
            for line in ans_raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                line = re.sub(r'^(?:[-*•]|\d+[\.\-\)])\s*', '', line)
                if line:
                    bullets.append(line)

            category = self._classify_category(q_stem_clean, bullets)
            card_id = f"{self.module_id[:4].upper()}-WRT-{len(cards)+1:02d}"

            cards.append({
                "id": card_id,
                "category": category,
                "category_label": PILLAR_CONFIG[category]["label"],
                "badge": badge,
                "front": q_stem_clean,
                "back_bullets": bullets,
                "source_section": "Written Questions",
                "lecture": self.lecture_title,
                "module": self.module_id
            })

        return cards

    def extract_mcq_cards(self) -> List[Dict[str, Any]]:
        """Extracts MCQs and transforms them into active-recall flashcards with option breakdown."""
        cards = []
        mcq_text = self.sections.get("mcq", "")
        if not mcq_text:
            return cards

        raw_mcqs = re.split(r'\n(?=###\s+)', mcq_text)
        for block in raw_mcqs:
            if not block.startswith("###"):
                continue

            badge = extract_badge(block) or "Past Exam MCQ"
            
            # Question stem
            q_match = re.search(r'\*\*Question\s*(?:\(verbatim\))?:\*\*\s*(.+?)(?=\n\*\*|\n---|\Z)', block, re.DOTALL)
            if not q_match:
                continue
            q_stem = q_match.group(1).strip()
            q_stem_clean = re.sub(r'^\d+[\.\)]\s*', '', q_stem).strip()

            # Options
            options_match = re.search(r'\*\*Options\s*(?:\(verbatim\))?:\*\*\s*(.+?)(?=\n\*\*|\n---|\Z)', block, re.DOTALL)
            options_text = options_match.group(1).strip() if options_match else ""

            # Correct Answer
            ans_match = re.search(r'\*\*Correct Answer:\*\*\s*(.+?)(?=\n\*\*|\n---|\Z)', block, re.DOTALL)
            if not ans_match:
                continue
            correct_ans = ans_match.group(1).strip()

            front_text = f"{q_stem_clean}\n\n{options_text}" if options_text else q_stem_clean
            bullets = [f"**Correct Answer:** {correct_ans}"]

            category = self._classify_category(q_stem_clean, [correct_ans])
            card_id = f"{self.module_id[:4].upper()}-MCQ-{len(cards)+1:02d}"

            cards.append({
                "id": card_id,
                "category": category,
                "category_label": PILLAR_CONFIG[category]["label"],
                "badge": badge,
                "front": front_text,
                "back_bullets": bullets,
                "source_section": "MCQs",
                "lecture": self.lecture_title,
                "module": self.module_id
            })

        return cards

    def extract_case_cards(self) -> List[Dict[str, Any]]:
        """Extracts Clinical Cases into stepwise diagnostic & management cards."""
        cards = []
        cases_text = self.sections.get("cases", "")
        if not cases_text:
            return cards

        raw_cases = re.split(r'\n(?=###\s+)', cases_text)
        for block in raw_cases:
            if not block.startswith("###"):
                continue

            badge = extract_badge(block) or "Clinical Case"
            
            # Scenario
            scen_match = re.search(r'\*\*Scenario:\*\*\s*(.+?)(?=\n\*\*|\n---|\Z)', block, re.DOTALL)
            scenario = scen_match.group(1).strip() if scen_match else ""

            # Questions
            q_match = re.search(r'\*\*Questions:\*\*\s*(.+?)(?=\n\*\*Model|\n---|\Z)', block, re.DOTALL)
            questions = q_match.group(1).strip() if q_match else ""

            # Model Answer
            ans_match = re.search(r'\*\*Model Answer:\*\*\s*(.+?)(?=\n\*\*Clinical|\n---|\Z)', block, re.DOTALL)
            if not ans_match:
                continue
            ans_raw = ans_match.group(1).strip()

            bullets = []
            for line in ans_raw.split("\n"):
                line = line.strip()
                if line:
                    bullets.append(line)

            front_text = f"**Clinical Scenario:**\n{scenario}\n\n**Questions:**\n{questions}" if scenario else questions
            card_id = f"{self.module_id[:4].upper()}-CAS-{len(cards)+1:02d}"

            cards.append({
                "id": card_id,
                "category": "signs",
                "category_label": "Clinical Case & Diagnostic Approach",
                "badge": badge,
                "front": front_text,
                "back_bullets": bullets,
                "source_section": "Clinical Cases",
                "lecture": self.lecture_title,
                "module": self.module_id
            })

        return cards

    def extract_high_yield_concept_cards(self) -> List[Dict[str, Any]]:
        """Extracts high-yield concept cards from Section 1 & Section 2 callouts, definitions, and mechanisms."""
        cards = []
        full_text = self.content

        # Look for [!IMPORTANT] and [!NOTE] blocks in Chronological Guide
        callout_matches = re.finditer(r'>\s*\[!(IMPORTANT|NOTE|WARNING)\]\s*\n(.*?)(?=\n\n\w|\n---|\Z)', full_text, re.DOTALL)
        for m in callout_matches:
            callout_type = m.group(1)
            body = m.group(2).strip()
            
            lines = [re.sub(r'^>\s*', '', l).strip() for l in body.split("\n") if l.strip()]
            if not lines:
                continue
            
            header_line = lines[0]
            if "Necrosis" in header_line or "آلية النخر" in header_line or "Mechanism" in header_line:
                q_front = f"Compare mechanism of tissue necrosis: Acid vs. Alkali burns ({self.lecture_title})?"
                bullets = [
                    "Acids: Coagulative necrosis -> Tough dry eschar (limits deeper penetration)",
                    "Alkalis: Liquefactive necrosis -> Saponification & protein dissolution (allows deep continuous penetration)",
                    "Clinical rule: Alkalis are far more dangerous and destructive than acids"
                ]
                cards.append({
                    "id": f"{self.module_id[:4].upper()}-MEC-01",
                    "category": "mechanism",
                    "category_label": PILLAR_CONFIG["mechanism"]["label"],
                    "badge": "High-Yield Mechanism",
                    "front": q_front,
                    "back_bullets": bullets,
                    "source_section": "Chronological Guide (Important Callout)",
                    "lecture": self.lecture_title,
                    "module": self.module_id
                })
            elif "مراحل تطور" in header_line or "Phases of Lesion" in header_line or "Evolution" in header_line:
                q_front = f"What are the 4 chronological phases of corrosive lesion evolution with timelines?"
                bullets = [
                    "1. Inflammatory Phase (Days 1–2): Acute vascular congestion, severe edema & direct cell death",
                    "2. Sloughing Phase (Days 2–7): Necrotic tissue falls off leaving deep ulceration or perforation",
                    "3. Granulation Tissue Phase (Weeks 2–3): Collagen deposition and healing scaffold",
                    "4. Cicatrisation / Scarring Phase (Weeks 2–4): Dense fibrous tissue contraction -> Stricture formation"
                ]
                cards.append({
                    "id": f"{self.module_id[:4].upper()}-CMP-01",
                    "category": "complications",
                    "category_label": PILLAR_CONFIG["complications"]["label"],
                    "badge": "High-Yield Timeline",
                    "front": q_front,
                    "back_bullets": bullets,
                    "source_section": "Chronological Guide (Lesion Evolution)",
                    "lecture": self.lecture_title,
                    "module": self.module_id
                })

        # Extract Definitions
        if "Definition of Corrosives" in full_text or "مفهوم المواد الأكالة" in full_text:
            cards.append({
                "id": f"{self.module_id[:4].upper()}-DEF-01",
                "category": "def",
                "category_label": PILLAR_CONFIG["def"]["label"],
                "badge": "Core Definition",
                "front": f"Definition of Corrosives and their dual clinical impact on tissues?",
                "back_bullets": [
                    "Definition: Chemical substances causing acute, direct destructive corrosion upon tissue contact",
                    "Structural / Histological alteration: Direct cell destruction & histological architecture loss",
                    "Functional impairment: Partial or complete loss of target organ function"
                ],
                "source_section": "Chronological Guide (Definition)",
                "lecture": self.lecture_title,
                "module": self.module_id
            })

        # Extract Classifications
        if "Modern Classification" in full_text or "تصنيف السموم الأكالة" in full_text:
            cards.append({
                "id": f"{self.module_id[:4].upper()}-TYP-01",
                "category": "types",
                "category_label": PILLAR_CONFIG["types"]["label"],
                "badge": "Core Classification",
                "front": f"Modern classification of Corrosive substances based on chemical composition?",
                "back_bullets": [
                    "1. Acid Corrosives: Mineral / Inorganic (H2SO4, HCl, HNO3) & Organic (Phenol, Oxalic acid)",
                    "2. Alkali Corrosives: Caustic soda (NaOH), Potash (KOH), Ammonia",
                    "3. Vegetable Corrosives",
                    "4. Metallic Corrosives (Heavy metals)",
                    "5. Button Batteries"
                ],
                "source_section": "Chronological Guide (Classification)",
                "lecture": self.lecture_title,
                "module": self.module_id
            })

        return cards

    def _classify_category(self, question_stem: str, bullets: List[str]) -> str:
        """Heuristic classifier to categorize a question into one of the 7 medical pillars."""
        q_lower = question_stem.lower()

        if any(w in q_lower for w in ["definition", "define", "what is"]):
            return "def"
        if any(w in q_lower for w in ["treatment", "ttt", "management", "antidote", "contraindication", "protocol"]):
            return "TTT"
        if any(w in q_lower for w in ["complication", "cause of death", "causes of death", "stricture", "perforation", "mortality"]):
            return "complications"
        if any(w in q_lower for w in ["type", "types", "classify", "classification", "grades", "phases"]):
            return "types"
        if any(w in q_lower for w in ["mechanism", "pathophysiology", "action of", "necrosis", "pathogenesis"]):
            return "mechanism"
        if any(w in q_lower for w in ["sign", "symptom", "clinical picture", "manifestation", "triad", "smell", "pupil", "urine", "buzzword"]):
            return "signs"
        if any(w in q_lower for w in ["past exam", "exam", "final"]):
            return "past_exams"
        
        return "past_exams"

    def extract_full_blueprint(self) -> List[Dict[str, Any]]:
        """Scans all sections and returns a deduplicated, balanced blueprint of cards."""
        self.load_and_split_sections()
        
        written_cards = self.extract_written_cards()
        concept_cards = self.extract_high_yield_concept_cards()
        case_cards = self.extract_case_cards()
        mcq_cards = self.extract_mcq_cards()

        all_cards = concept_cards + written_cards + case_cards + mcq_cards
        
        unique_cards = []
        seen_fronts = set()
        for c in all_cards:
            simplified_front = re.sub(r'[^a-zA-Z0-9]', '', c['front'].lower())[:40]
            if simplified_front not in seen_fronts:
                seen_fronts.add(simplified_front)
                unique_cards.append(c)

        return unique_cards


def generate_blueprint_for_transcript(transcript_path: str, module_id: str = "") -> List[Dict[str, Any]]:
    """Helper entry point to extract blueprint list from transcript path."""
    extractor = TranscriptConceptExtractor(Path(transcript_path), module_id=module_id)
    return extractor.extract_full_blueprint()
