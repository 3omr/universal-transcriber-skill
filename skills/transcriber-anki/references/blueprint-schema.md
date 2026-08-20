# Card Blueprint Schema

The intermediate blueprint JSON represents the complete set of cards extracted from a lecture before compilation into Anki deck `.apkg` or `.tsv` files.

---

## Schema Structure

```json
[
  {
    "id": "TOXO-DEF-01",
    "category": "def",
    "category_label": "Definition & Diagnostic Criteria",
    "badge": "Core Definition",
    "front": "Definition of Corrosives and their dual clinical impact on tissues?",
    "back_bullets": [
      "Definition: Chemical substances causing acute, direct destructive corrosion upon tissue contact",
      "Structural / Histological alteration: Direct cell destruction & histological architecture loss",
      "Functional impairment: Partial or complete loss of target organ function"
    ],
    "source_section": "Chronological Guide (Definition)",
    "lecture": "Corrosive 1",
    "module": "toxo"
  }
]
```

---

## Fields Specification

- `id` *(string, required)*: Deterministic unique identifier (e.g. `<MODULE>-<CATEGORY>-<INDEX>`).
- `category` *(string, required)*: One of `def`, `types`, `mechanism`, `signs`, `TTT`, `complications`, `past_exams`.
- `category_label` *(string, required)*: Human-readable category label.
- `badge` *(string, optional)*: Pill badge text displayed on top of the card.
- `front` *(string, required)*: Question stem or active-recall prompt in English.
- `back_bullets` *(list of strings, required)*: Model answer bullet points in English.
- `source_section` *(string, optional)*: Transcript origin section.
- `lecture` *(string, required)*: Lecture title without decorative emojis.
- `module` *(string, required)*: Module ID.
