# Exam Style Profile and Grounded Question Authoring

The Agent owns the style judgment and observes exam patterns across real question sources. This reference defines what patterns to extract and inject into the generation engine.

---

## 1. Style Sampling Hierarchy

Sample questions in this strict order of priority:
1. **Same lecture or module past exams**: Highest authority for specific department wording.
2. **Other exams from the same college/course**: Identifies recurring university exam style.
3. **Question-bank material used by that course**: Useful for question formatting and distractor distributions.
4. **Other modules**: Used only when earlier tiers do not provide enough examples.

Inspect several clean examples of each question type. Ignore isolated formatting noise or page headers. Record recurring structural patterns only.

---

## 2. MCQ Pattern Observations

Extract:
- **Stem shapes**: `The following ...:-`, `... are:`, `... except:-`, or direct interrogative questions.
- **Option conventions**: 4 or 5 options, label style (`a.`–`d.`, `A.`–`D.`), parentheses, capitalization, and whether options are fragments or complete sentences.
- **Distractor relations**: Option length, parallel structure, and plausible clinical distractors.
- **Clinical vignettes**: Frequency of case-based MCQ stems in past exams.

**Rules for Generation**:
- **Sourced Past Exams**: Retain semantically verbatim wording after repairing OCR artifacts. Do not paraphrase.
- **Generated IMP Items**: Imitate the observed stylistic structure while deriving facts solely from the current recording.

---

## 3. Written Question Observations & Formatting

Extract:
- **Command verbs**: `Enumerate`, `Mention`, `Causes of`, `Mechanism of`, `Treatment of`, `Give reason for`, `Compare between`.
- **Punctuation & blanks**: `:-`, `:`, numbered blanks (`1.... 2.... 3....`).
- **Point requirements**: Expected count of points (e.g. 4 points, 5 causes).
- **Answer shape**:
  - `Model Answer`: Strictly ultra-concise keywords or short phrases (Egyptian exam mark scheme style, 1–5 words per bullet/numbered item).
  - `Give Reason`: A short, direct clause identifying the primary physiological/chemical reason (e.g. `Hypothermia increases CO-Hb affinity (prevents CO dissociation)`).
  - `Enumerate / Complete / List`: Exact numbered keywords (`1- ...`, `2- ...`, `3- ...`).
  - `Clinical Explanation`: Full comprehensive clinical rationale, mechanisms, and lecturer remarks in natural Egyptian Arabic. Never put lengthy paragraphs inside `Model Answer`.

---

## 4. Clinical Cases & Specialty-Aware Standards

Extract and follow the standard Egyptian medical exam breakdown matching the subject/specialty:

### A. Subject-Specific Case Question Conventions
- **Toxicology & Forensic Medicine**:
  1. `What is the most likely diagnosis and severity?`
  2. `What is the differential diagnosis (DDx) / characteristic sign (e.g. 3Cs of red skin)?`
  3. `Mention key diagnostic investigations (e.g. COHb level, dilution test, ABG).`
  4. `Outline the lines of treatment (TTT) / antidote / precautions / HBO indications.`
- **Cardiology & Internal Medicine**:
  1. `What is the most likely diagnosis?`
  2. `What is the differential diagnosis (DDx) / characteristic clinical features (CP)?`
  3. `Mention key investigations (ECG, Echocardiography, Biomarkers).`
  4. `Outline pharmacological and interventional treatment (TTT).`
- **Pediatrics / General Surgery / Pharmacology / Pathology**:
  Follow standard clinical sequence: `Diagnosis` → `DDx / CP` → `Investigations` → `Treatment (TTT) / Contraindications`.

### B. Case Rules for Generation
- **Past-Exam Cases**: Reproduce all original sub-questions verbatim in their exact count and sequence.
- **Synthesized IMP Cases**: Strictly follow the 4-part clinical breakdown above. Never create long narrative essay sub-questions (e.g. "Explain the dual physiological mechanisms...").
- **Model Answer in Cases**: Ultra-concise keywords under clean sub-headings (1–5 words per point).
- **Clinical Explanation in Cases**: Complete reasoning in Egyptian Arabic with lecturer pearls and diagnostic traps.

---

## 5. Manifest Profile Format

Pass the observed profile inside the temporary source manifest:

```json
{
  "sample_scope": "Same college past exams and official question bank",
  "mcq": {
    "stem_patterns": ["The following ...:-", "... are:", "... except:-"],
    "options": {
      "count": 4,
      "labels": "lowercase a. through d."
    },
    "max_stem_words": 20,
    "register": "Short direct factual stems with parallel concise options"
  },
  "written": {
    "command_patterns": [
      "Causes of ...: 1.... 2....",
      "Treatment of ...",
      "Mechanism of ...",
      "Give Reason: ..."
    ],
    "answer_shape": "Ultra-concise numbered keywords matching requested count (1-5 words)"
  },
  "cases": {
    "style": "Standard Egyptian medical exam case breakdown matching subject conventions",
    "sub_questions_pattern": [
      "1. Diagnosis (or Most likely diagnosis)",
      "2. DDx (Differential diagnosis) or Characteristic Clinical Picture (CP)",
      "3. Investigations / Confirmatory laboratory tests",
      "4. Treatment / Management (TTT / Antidote / Emergency measures)"
    ],
    "answer_shape": "Ultra-concise keyword bullets under standard clinical headings (1 to 5 words per point)"
  }
}
```

---

## 6. Duplicate Question Review & Fast-Fail Boundary

- **Automatic Safe Merge**: The engine merges identical or safely OCR-equivalent questions across exam years, combining years in ascending order:
  `### Question 1 **[Past Exams - 2021, 2022, 2023]**`
- **Fast-Fail on Duplicate Discrepancies & Joined OCR**: When questions share a similar stem but differ in options/count, or when joined OCR words occur, the engine fast-fails immediately to Agent Recovery without repeating slow NotebookLM retry loops. The Agent normalizes and repairs questions directly.

---

## 7. Lecture Scope & Out-of-Scope Question Filtering

- **Assessment Sourcing Scope**: Exam files uploaded to NotebookLM often encompass multiple lectures or the whole module curriculum.
- **Filtering Obligation**: Grounded questions must be strictly filtered against what was taught in the selected lecture (audio recording and slide deck).
- **Pruning Rule**:
  - Reject questions matching general keywords that actually belong to distinct lectures (e.g. Firearms questions extracted into Mechanical Wounds).
  - Delete out-of-scope questions and re-number the remaining items in Section 3 (MCQs), Section 4 (Written), and Section 5 (Cases).
