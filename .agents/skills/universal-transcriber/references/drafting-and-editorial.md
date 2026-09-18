# Drafting and Editorial Guidelines

Every lecture transcript must adhere to the 5-section academic standard. The Agent owns editorial review and content validation before finalization.

---

## The Five Mandatory Sections

Transcripts are written sequentially in five distinct sections:

```text
## 📖 Chronological Guide
## 🌟 IMP Points
## ❓ MCQs
## ✍️ Written Questions
## 🩺 Clinical Cases
```

> [!IMPORTANT]
> These five strings are the contract, not a description of it. They are
> `phase_validation.SECTION_HEADINGS` verbatim, and a finished transcript is
> parsed by them — rename one and the question bank, exam mode and Anki export
> stop seeing that section. Question headings are likewise fixed: `### MCQ N`
> in the MCQs section, `### Question N` in Written Questions, and
> `### Clinical Case N` in Clinical Cases.

---

### Section 1: 📖 Chronological Guide

- **Chronological Completeness**: Never summarize, compress, or omit parts of the doctor's lecture. Preserve the exact pedagogical progression, examples, clinical anecdotes, and transitions between recordings.
- **Language Blend**: Natural, engaging **Egyptian Arabic** for clinical explanations, reasoning, and conceptual bridges, combined with **English medical terminology** (conditions, drugs, anatomy, lab values).
- **Speaker Emphasis**: Highlight points the lecturer heavily emphasizes using visual callouts (e.g. `> [!IMPORTANT] الدكتور ركز جداً على ...`).
- **Unspoken Additions**: Explicitly allowed book/slide additions that clarify a recording topic must use the standard note callout:
  ```markdown
  > [!NOTE]
  > **إضافة من الكتاب/السلايد — لم يشرحها الدكتور في التسجيل**
  > Concise explanation supplementing the recorded point.
  ```

---

### Section 2: 🌟 IMP Points

Must contain **exactly these five `####` headings**, in this order and spelled
this way — `phase_validation.IMP_HEADINGS` and the phase prompt both enforce it:

```markdown
#### 1. 📌 Doctor's Spoken Pearls
#### 2. ⚠️ Diagnostic Traps
#### 3. 🛑 Lethal Mistakes
#### 4. ❓ Interactive Doctor Questions
#### 5. 📋 Exam Rules
```

1. **Doctor's Spoken Pearls**: what the lecturer actually said, quoted where it is memorable.
2. **Diagnostic Traps**: must contain at least one `> [!WARNING]` callout.
3. **Lethal Mistakes**: must contain at least one `> [!CAUTION]` callout.
4. **Interactive Doctor Questions**: the questions the lecturer threw at the room.
5. **Exam Rules**: what the lecturer said about the exam itself, plus any summary tables.

---

### Section 3: ❓ MCQs

Format every MCQ with clean Markdown field labels and valid badges:

```markdown
### MCQ 1 **[Past Exams - 2022, 2023]**

**Question:** The following are clinical features of acute organophosphate poisoning EXCEPT:-

**Options:**
- **a.** Pinpoint pupil (miosis)
- **b.** Excessive salivation and lacrimation
- **c.** Dry hot skin and mydriasis
- **d.** Bradycardia and bronchospasm

**Source:** final Toxico 2022.pdf
**Source:** final Toxico 2023.pdf

**Correct Answer:** **c.** Dry hot skin and mydriasis

**Clinical Explanation:**
التفسير بالعامية المصرية: الـ Organophosphates بتعمل Cholinergic Toxidrome (SLUDGE/DUMBELS) بسبب زيادة الـ Acetylcholine. الـ Dry hot skin والـ Mydriasis دول بتوع الـ Anticholinergic toxicity (زي الـ Atropine poisoning) وبالتالي ده الاختيار المستثنى.
```

- **Option Labels**: Unordered list with lowercase bold letters (`- **a.**`, `- **b.**`, `- **c.**`, `- **d.**`).
- **Spacing**: Always leave a blank line (`\n\n`) before `**Correct Answer:**`.
- **Provenance comes from `Questions/exam-index.json`, never from your own
  reading of the papers.** Copy the stem, the options, the answer key and the
  years out of the index entry. Do not retype a question, do not decide a year
  by looking at a filename, and do not write a badge a lookup did not give you.
  Build the index first (`--build-exam-index`); a module without one cannot be
  drafted honestly.
- **`**Source:**` is optional once the index exists.** It was how a badge was
  made checkable before there was an index; now the index records provenance
  per question — paper *and* section — and validation accepts an indexed
  question without it. Repeating the same filename under every question puts
  bookkeeping in front of the student. Keep a `**Source:**` line only for a
  question the index does not know, where it is the only record of where the
  question came from; a block badged only `**[IMP]**` never carries one, and
  fails with `[source_role_mismatch]` if it does.
- **Badges**:
  - `**[Past Exams - 2023]**`
  - `**[Past Exams - 2021, 2022, 2023]**`
  - `**[Question Bank]**`
  - `**[IMP]**`
  - `**[Past Exams (2022) / IMP]**` — note the **parentheses**: the combined
    form is `(YYYY)`, not `- YYYY`, and it additionally requires the recording
    to be cited or named in a `**Source:**` line

> [!WARNING]
> **A year badge is a promise to a student revising by it.** `**[Past Exams -
> 2022]**` says *this exact question was on the 2022 paper*. Write it only when
> the index says so.
>
> Two ways that goes wrong, both of which have shipped:
>
> 1. **A question that was never on a paper.** Six clinical cases once carried
>    `**[Past Exams - 2022, 2023]**` on vignettes that existed in no paper at
>    all — their *topics* came from short essays, which is not the same claim.
>    Most papers in these modules have no clinical-case section: they are MCQs
>    and short essays. A case you built to tie the lecture together is
>    `**[IMP]**`, and the section should say plainly that it was built.
> 2. **A year read off a filename.** `Radiology_Exams_2026.txt` is a *compiled
>    bank* holding 2021-2025 papers; a question in it is `**[Question Bank]**`
>    unless its own section names a year. Nothing in it is a 2026 question.
>
> Before finalizing, run `--verify-provenance` on the transcript. It holds every
> year badge against the paper it names and exits non-zero on an unbacked claim.

---

### Section 4: ✍️ Written Questions

Structured short and long questions reflecting actual exam commands:

```markdown
### Question 1 **[Past Exams - 2023]**

**Question:** Outline the treatment of CO poisoning: 1...........2........3......4......

**Source:** final Toxico 2023.pdf

**Model Answer:**
1- Fresh air / Prevent exposure
2- 100% O2
3- Hyperbaric oxygen (HBO)
4- Supportive therapy

**Clinical Explanation:**
التفسير بالعامية المصرية: في الامتحانات المصرية بيحب إجابات الكلمات المفتاحية المختصرة (Keywords). الخطوات بتبدأ فوراً بإبعاد المريض عن مصدر الغاز، ثم إعطاؤه أكسجين 100%، واستخدام كبسولة الأكسجين المضغوط (HBO) في الحالات الشديدة، وأخيراً العلاج التدعيمي لمضاعفات المخ والرئة.
```

---

### Section 5: 🩺 Clinical Cases

Grounded clinical vignettes with all original exam sub-questions reproduced verbatim:

```markdown
### Clinical Case 1 **[Past Exams - 2022]**

**Scenario:** A 4-year-old child is brought to the emergency department 2 hours after accidentally ingesting a bottle of cleaning solution. The child presents with severe drooling, dysphagia, stridor, and burns around the lips and oral cavity.

**Questions:**
1. What is the most likely diagnosis?
2. What are the immediate emergency management steps?
3. Which diagnostic investigation is indicated, and what is the optimal timing?
4. Mention two absolute contraindications in the initial management.

**Source:** final Toxico 2022.pdf

**Model Answer:**
1. **Diagnosis:** Corrosive ingestion (Alkali/Acid caustic burn)
2. **Immediate Management:**
   - Airway maintenance (intubation if stridor)
   - IV fluids & analgesia
   - NPO (Nil per os)
3. **Investigation & Timing:** Upper endoscopy (EGD) within 12-24h
4. **Contraindications:**
   - Induction of emesis
   - Gastric lavage / neutralization

**Clinical Explanation:**
التفسير بالعامية المصرية: حالات الـ Corrosive Ingestion من أشهر الكيسات المتكررة. النقطة المحورية هنا هي تجنب الـ Emesis والـ Neutralization تماماً لأنها بتعمل Perforation، وعمل المنظار في أول 12-24 ساعة لتحديد درجة الـ Burn.
```

---

## Editorial Normalization & Review Rules

1. **OCR Damage Restoration**:
   - Repair split characters, merged words, and garbled option letters (`- **a.**`).
   - Do NOT rewrite or paraphrase questions — retain the original exam wording.
   - If a damaged character cannot be resolved with certainty from source images, flag as `NEEDS_OCR_REVIEW`.
2. **Medical Fact Integrity**:
   - Verify that `Correct Answer` strictly matches the designated option letter.
   - Ensure the clinical explanation agrees with the designated answer.
   - If the source has contradictory keys, surface as `UNRESOLVED_CONFLICT` rather than guessing.
3. **Student-Facing Presentation**:
   - Hide internal NotebookLM source UUIDs, raw filenames, and internal hashes.
   - Retain all grounded year badges and evidence provenance.
4. **Lecture Scope & Question Relevance Gate**:
   - Cross-check every extracted question in MCQs, Written Questions, and Clinical Cases against the **Chronological Guide** and the lecture's **slides/PowerPoint**.
   - If an exam question addresses a topic belonging to a separate chapter/lecture that was neither taught by the lecturer in the recording nor included in the slide deck (e.g. Firearm inlet/exit, powder marks, or bevelling appearing in a Mechanical/General Wounds transcript), **prune and delete** the question entirely from the draft.
   - Re-index all remaining question numbers sequentially (`### MCQ 1`, `### MCQ 2`, ..., `### Question 1`, ..., `### Clinical Case 1`).

---

## Figures: Carrying the Pictures into the Transcript

Slides reach NotebookLM as a PPTX or a PDF and NotebookLM answers in text, so
everything that was a picture — the anatomy of the anterior chamber, a
gonioscopy view, the photo of the Toxogonin box that is the whole point of an
antidote slide — is gone by the time a transcript is written. In ophthalmology
and toxicology that is most of the teaching.

### 1. First, take the figures from the lecture's own deck

```bash
python3 skills/universal-transcriber/scripts/run_transcription.py \
  --workspace "$PWD" --module <module_id> \
  --extract-figures --lecture "<lecture_name>"
# add --slides "Lecture/<deck>.pptx" to illustrate a deck module.json does not map,
# --figure-resolution <dpi> to change the 150 DPI default,
# --all-slides to render every page instead of only the diagram pages.
```

A page is rendered when it carries almost no extractable text **and** actually
embeds an image — both tests, because a section divider reading just `Warfarin`
has eight characters and no picture, and rendering it produces a blank slide
with a title on it. The run writes the PNGs plus a `figures.json` into
`Transcripts/Figures/<lecture>/` and prints the markdown to paste. Place each
image in the **Chronological Guide**, at the point the doctor was talking about
it — not in a gallery at the end.

The doctor's own slide always wins. It is what the students saw, it is what the
exam was written from, and it needs no attribution.

### 2. Only if the deck has none, source one from the web

When a passage genuinely cannot be understood without a picture — an anatomical
relationship, a characteristic radiological sign, a rash or lesion whose
appearance *is* the diagnosis, an ECG pattern, a dosing or management algorithm —
and neither the deck nor `Figures/` has one, search the web for a replacement.

- **Only when the text needs it.** A figure that decorates a paragraph the words
  already carry is noise. Prose that reads fine without a picture gets no picture.
- **Openly licensed sources only**: Wikimedia Commons, Open-i, NIH/CDC/PHIL,
  Radiopaedia cases marked reusable, or an open-access journal figure (CC BY /
  CC BY-SA / public domain). Do not take images from paid textbooks, lecture
  decks belonging to other faculties, Google Images thumbnails, or anything
  whose licence you could not name if asked.
- **Verify before you place it.** Read the source page and confirm the image
  really shows the finding named in the caption. A plausible-looking image of the
  wrong pathology is worse than no image at all — the student revises from it.
- **Save it beside the extracted ones** in `Transcripts/Figures/<lecture>/` with
  a descriptive filename (`web-gonioscopy-open-angle.png`), so the transcript
  keeps working offline and does not rot when a URL dies.
- **Caption it as external, with attribution**, so nobody mistakes it for what
  the doctor showed:

  ```markdown
  ![Open-angle gonioscopy view](./Figures/Glaucoma/web-gonioscopy-open-angle.png)
  > 🌐 **صورة من الإنترنت** (مش من سلايدات الدكتور) — [Wikimedia Commons](<url>), CC BY-SA 4.0.
  ```

- **Never invent, generate, or edit a medical image**, and never relabel one to
  fit the text. If nothing suitable and properly licensed exists, write the
  passage without a figure and note `NEEDS_FIGURE` so a human can decide.
