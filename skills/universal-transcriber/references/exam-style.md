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

## 3. Written Question Observations

Extract:
- **Command verbs**: `Enumerate`, `Mention`, `Causes of`, `Mechanism of`, `Treatment of`, `Give reason for`, `Compare between`.
- **Punctuation & blanks**: `:-`, `:`, numbered blanks (`1.... 2.... 3....`).
- **Point requirements**: Expected count of points (e.g. 4 points, 5 causes).
- **Answer shape**: Structured sub-bullets, concise numbered keywords, or comparative tables.

---

## 4. Manifest Profile Format

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
      "Mechanism of ..."
    ],
    "answer_shape": "Numbered keywords matching requested count"
  }
}
```

---

## 5. Duplicate Question Review Boundary

- **Automatic Safe Merge**: The engine merges identical or safely OCR-equivalent questions across exam years, combining years in ascending order:
  `### Question 1 **[Past Exams - 2021, 2022, 2023]**`
- **Unsafe Duplicate Merge**: When two questions share a similar stem but differ in options, negation (`except`), requested count, or answer key, the engine halts and flags `unsafe_duplicate_merge`. The Agent must inspect source evidence and either keep them separate or resolve the discrepancy.
