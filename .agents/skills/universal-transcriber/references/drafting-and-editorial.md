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

- **Chronological Completeness & Lecture-Hall Spirit**: Never summarize, compress, or omit parts of the doctor's lecture. Preserve the exact pedagogical progression, student-teacher dialogues, corrections of wrong student answers, clinical anecdotes, jokes, and transitions between recordings in their real spoken order.
- **Natural Egyptian Arabic (No Stiff MSA Medical Translations)**: Write explanations in natural, lively **spoken Egyptian Arabic** paired directly with **English medical terminology** (`Jaundice`, `Hemostasis`, `Hyperbilirubinemia`, drugs, anatomy, lab values). **Never** translate medical terms into stiff formal MSA ("اليرقان الوليدي"، "الإرقاء الأولي"، "فرط بيليروبين الدم") and avoid dry third-person textbook narration ("افتتح الدكتور المحاضرة بالتأكيد على...").
- **Scannable Formatting**: Keep the conversational lecture flow without turning the guide into dense walls of text — break enumerations, cutoffs, numbers, and comparisons into clean bullet points under clear `###`/`####` headings.
- **Purposeful Callouts Only**: Use `> [!IMPORTANT]` **only** when the doctor explicitly stressed or repeated a point (never as decoration on every paragraph). Use `> [!WARNING]` for spoken warnings/pitfalls and `> [!CAUTION]` strictly for absolute contraindications, red flags, or lethal errors.
- **Strict Doctor vs. Book/Slide Separation**: Keep the doctor's spoken prose 100% faithful to the recording — **never silently weave unspoken textbook mechanisms, extra cutoffs, or slide details into the doctor's paragraphs**. Any essential unspoken clarification from a slide or book must be isolated inside the standard note callout:
  ```markdown
  > [!NOTE]
  > **إضافة من الكتاب/السلايد — لم يشرحها الدكتور في التسجيل**
  > Concise explanation supplementing the recorded point.
  ```

---

### Section 2: 🌟 IMP Points

**Strictly restricted to the doctor's spoken lecture (recording only)** — mirror
the Radiology transcripts style. Never pull facts, traps, guideline updates, or
summary tables from textbooks, slides, handouts, past-exam papers, or question
banks into this section. Write in natural **Egyptian Arabic** mixed with precise
**English medical terms**, formatted as clean markdown bullet lists (`- `),
**without summarizing** — capture every emphasized point, exam hint, warning,
class question, and non-medical guideline the doctor actually voiced, quoting
memorable doctor phrasing verbatim whenever supported by the recording.

Must contain **exactly these five `####` headings**, in this order and spelled
this way — `phase_validation.IMP_HEADINGS` and the phase prompt both enforce it:

```markdown
#### 1. 📌 النقاط الطبية الحرجة اللي ركز عليها الدكتور (Critical Medical Points)
#### 2. 🎯 نقاط الامتحان اللي أشار ليها الدكتور (Exam Points)
#### 3. ⚠️ تحذيرات الدكتور (Warnings)
#### 4. ❓ أسئلة الدكتور للطلبة أثناء الشرح (Questions Asked to the Class)
#### 5. 📋 توجيهات وتعليمات غير طبية (Non-Medical Guidelines)
```

1. **النقاط الطبية الحرجة اللي ركز عليها الدكتور (Critical Medical Points)**: every critical medical concept, comparison, number, or clinical pearl the lecturer explicitly focused on and emphasized in the recording — no book/slide additions.
2. **نقاط الامتحان اللي أشار ليها الدكتور (Exam Points)**: every topic, question, image, sign, or trick the lecturer explicitly indicated would appear in the exam (written, MCQ, practical/OSCE, or oral), preserving their exact hint or quote.
3. **تحذيرات الدكتور (Warnings)**: every medical, clinical, legal, safety, or practical warning the lecturer actually voiced (contraindications, red flags, mistakes to avoid). List directly as clear bullets — never invent warnings the doctor did not say, and never force callout boilerplate.
4. **أسئلة الدكتور للطلبة أثناء الشرح (Questions Asked to the Class)**: every interactive question the lecturer threw at the room, verbatim/near-verbatim, along with the answer actually given (if any).
5. **توجيهات وتعليمات غير طبية (Non-Medical Guidelines)**: non-medical instructions actually spoken by the lecturer (exam format/structure, grading, attendance, booklets/slides/sources to study from, revision sessions, schedule notices, or classroom reminders). Never add textbook or slide summary tables here.

- **Empty categories**: If the doctor did not touch on one of the five categories in this recording, keep its `####` heading and add one brief italicized Arabic note — e.g. `*(ملحوظة: الدكتور لم يتطرق في هذه المحاضرة لأي قواعد غير طبية زي الحضور أو الغياب أو نظام الدرجات أو المذكرات)*`. Never pad or fabricate items from external sources.

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
- **Badges** (applies across Sections 3, 4, and 5):
  - `**[Past Exams - 2023]**`
  - `**[Past Exams - 2021, 2022, 2023]**`
  - `**[Question Bank]**`
  - `**[IMP]**`
  - **Dual badges (Past Exams / Question Bank + IMP)**: always stack `**[IMP]**`
    side by side — e.g. `**[Past Exams - 2022, 2023]** **[IMP]**`,
    `**[Question Bank]** **[IMP]**`, or
    `**[Past Exams - 2023]** **[Question Bank]** **[IMP]**` — whenever a
    past-exam or question-bank question tests a point the doctor also explicitly
    emphasized or flagged for the exam in the recording (Section 2). Never drop
    `**[IMP]**` just because the question already carries a Past Exams badge,
    and never attach `**[IMP]**` unless the doctor actually stressed that point.
    *(The legacy single-badge form `**[Past Exams (2022) / IMP]**` remains
    accepted for backwards compatibility, but prefer the two separate badges so
    both `Past Exams` and `IMP` stay cleanly visible and filterable.)*

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

### 2. Then fill every gap from the web — a missing deck is not an excuse

A transcript with no pictures is not finished. When the deck has no images,
when there is no deck, or when its figures do not cover what the doctor
described, source the missing pictures from the web. "The slides had none" is
never a reason to ship a picture-less guide.

A passage needs a picture when it describes something the student must
recognise by sight — an anatomical relationship, a characteristic radiological
sign, a rash or lesion whose appearance *is* the diagnosis, an ECG pattern, a
clinical sign, a dosing or management algorithm. Walk the Chronological Guide
heading by heading and give each such passage a figure, from the deck if it has
one, otherwise from the web.

**Surgery is the strictest case.** Surgical teaching is visual: swellings,
ulcers, skin and subcutaneous lesions, hernias, neck and breast masses,
deformities, incisions, operative steps, instruments, and positioning. In a
surgery module every clinical entity the doctor names and describes — each type
of swelling, lesion, ulcer, or sign; each operation or approach — gets its own
picture. A surgery transcript with fewer than about one figure per such entity,
and never fewer than six in a full lecture or round, is not ready to finalize.

- **Only real gaps.** A figure that repeats one already placed, or decorates a
  definition or a list of numbers the words carry fine, is noise. Coverage
  means every *visual* entity has a picture, not that every paragraph does.
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

### 3. Check coverage before finalizing

Before `--finalize-draft`, list the visual entities in the Chronological Guide
and the figure placed for each. Every entity has a figure or a `NEEDS_FIGURE`
note with the reason nothing licensed was found; a surgery lecture also meets
the floor above. Report the count (deck figures, web figures, `NEEDS_FIGURE`)
in the handoff, so a picture-less transcript cannot pass unnoticed.
