# Exam Style Profile

The agent owns the style judgment. This reference defines what to observe and
what may be passed to the engine; it is not a content source.

## Sampling order

1. Same lecture or module past exams.
2. Other exams from the same college/course.
3. Question-bank material used by that course.
4. Other modules only when the first three do not provide enough examples.

Inspect several clean examples of each question type. Ignore OCR glitches,
page headers, marks, and a single unusual item. Record recurring patterns only.

## MCQ observations

Capture:

- common stem shapes (`The following ...:-`, `... are:`, `... except:-`, or a
  short direct question);
- option count and label style (`a.`–`d.`, `A.`–`D.`, parentheses, or mixed);
- casing, punctuation, and whether options are fragments or full sentences;
- approximate option length and the usual distractor relationship;
- when a clinical vignette is actually common enough to use.

For generated IMP items, imitate this form while deriving facts from the current
recording. Do not copy a sample's medical content, answer, wording, or badge.
Keep sourced past-exam wording semantically verbatim after repairing obvious OCR
damage; do not paraphrase it into a new question.

## Written-question observations

Capture:

- command verbs (`complete`, `enumerate`, `mention`, `causes of`, `mechanism
  of`, `treatment of`, `give reason`, `compare`);
- punctuation and blank conventions (`:-`, `:`, numbered dotted blanks);
- the number of requested points;
- whether answers are keywords, short sentences, a table, or a case outline.

Generated IMP questions should use the same direct exam command and request the
same number of points. Model answers should be short numbered points matching
that request. Do not turn a short completion item into a long academic essay.

## Manifest shape

Keep the profile compact JSON and presentation-only, for example:

```json
{
  "sample_scope": "same college exams and question bank",
  "mcq": {
    "stem_patterns": ["The following ...:-", "... except:-"],
    "options": {"count": 4, "labels": "lowercase a. through d."},
    "max_stem_words": 18,
    "register": "short direct factual stems; parallel concise options"
  },
  "written": {
    "command_patterns": ["Causes of ...: 1.... 2....", "Treatment of ..."],
    "answer_shape": "numbered keywords, usually four points"
  }
}
```

The profile is supplied through the temporary source manifest and is injected
into only the MCQ and Written prompts. It never overrides source authority,
badge validation, or the requirement that the chronological lecture guide stay
complete.

## Duplicate review boundary

The engine merges only identical or OCR-safe question blocks. A shared normalized
stem with different options, answer, requested count, negation, or provenance is
reported as `unsafe_duplicate_merge`; the Agent must compare the source evidence
and either merge it with all verified years or keep the blocks separate. A changed
original exam number alone is formatting noise and does not make two copies
different.
