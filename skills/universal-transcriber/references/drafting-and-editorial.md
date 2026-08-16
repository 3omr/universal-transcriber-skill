# Drafting and Editorial Guidelines

Every lecture transcript must adhere to the 5-section academic standard. The Agent owns editorial review and content validation before finalization.

---

## The Five Mandatory Sections

Transcripts are written sequentially in five distinct sections:

```text
1. 📖 Chronological Guide
2. ⭐ High-Yield Summary & IMP Points
3. ❓ Multiple Choice Questions (MCQs)
4. 📝 Written Questions (Short & Long Form)
5. 🏥 Clinical Cases
```

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

### Section 2: ⭐ High-Yield Summary & IMP Points

Must contain the five canonical subsections:
1. **Core Clinical Concepts**: High-yield pathophysiological and pharmacological mechanisms.
2. **Golden Diagnostic Rules**: Definitive diagnostic criteria, pathognomonic signs, and investigation of choice.
3. **Treatment Protocols & Red Flags**: First-line therapies, contraindications, and emergency management.
4. **Classic Exam Traps & Differentials**: Key points where examiners try to confuse students.
5. **High-Yield Summary Table**: Comparative markdown table summarizing clinical classifications, drugs, or toxic agents.

---

### Section 3: ❓ Multiple Choice Questions (MCQs)

Format every MCQ with clean Markdown field labels and valid badges:

```markdown
### Question 1 **[Past Exams - 2022, 2023]**

**Question:** The following are clinical features of acute organophosphate poisoning EXCEPT:-

**Options:**
- **a.** Pinpoint pupil (miosis)
- **b.** Excessive salivation and lacrimation
- **c.** Dry hot skin and mydriasis
- **d.** Bradycardia and bronchospasm

**Correct Answer:** **c.** Dry hot skin and mydriasis

**Clinical Explanation:**
التفسير بالعامية المصرية: الـ Organophosphates بتعمل Cholinergic Toxidrome (SLUDGE/DUMBELS) بسبب زيادة الـ Acetylcholine. الـ Dry hot skin والـ Mydriasis دول بتوع الـ Anticholinergic toxicity (زي الـ Atropine poisoning) وبالتالي ده الاختيار المستثنى.
```

- **Option Labels**: Unordered list with lowercase bold letters (`- **a.**`, `- **b.**`, `- **c.**`, `- **d.**`).
- **Spacing**: Always leave a blank line (`\n\n`) before `**Correct Answer:**`.
- **Badges**:
  - `**[Past Exams - 2023]**`
  - `**[Past Exams - 2021, 2022, 2023]**`
  - `**[Question Bank]**`
  - `**[IMP]**`
  - `**[Past Exams (2022) / IMP]**`

---

### Section 4: 📝 Written Questions

Structured short and long questions reflecting actual exam commands:

```markdown
### Question 1 **[Past Exams - 2023]**

**Question:** Enumerate four causes of sudden cardiac death in young athletes:-

**Model Answer:**
1. **Hypertrophic Cardiomyopathy (HCM):** Most common cause; asymmetric septal hypertrophy leading to LVOT obstruction and ventricular arrhythmias.
2. **Anomalous Coronary Artery Origin:** Compression of the anomalous vessel between the aorta and pulmonary artery during strenuous exertion.
3. **Arrhythmogenic Right Ventricular Cardiomyopathy (ARVC):** Fibrofatty replacement of RV myocardium predisposing to fatal ventricular tachycardia.
4. **Channelopathies (e.g., Congenital Long QT Syndrome, Brugada Syndrome):** Ion channel dysfunction triggering polymorphic VT or Torsades de Pointes.

**Clinical Explanation:**
التفسير بالعامية المصرية: في الامتحانات بيحب يسأل عن أشهر أسباب الـ Sudden Cardiac Death في السن الصغير. الإجابة النموذجية لازم تبدأ بالـ HCM كأول وأهم سبب، مع ذكر السبب الميكانيكي لكل نقطة باختصار.
```

---

### Section 5: 🏥 Clinical Cases

Grounded clinical vignettes with all original exam sub-questions reproduced verbatim:

```markdown
### Clinical Case 1 **[Past Exams - 2022]**

**Scenario:** A 4-year-old child is brought to the emergency department 2 hours after accidentally ingesting a bottle of cleaning solution. The child presents with severe drooling, dysphagia, stridor, and burns around the lips and oral cavity.

**Questions:**
1. What is the most likely diagnosis?
2. What are the immediate emergency management steps?
3. Which diagnostic investigation is indicated, and what is the optimal timing?
4. Mention two absolute contraindications in the initial management.

**Model Answer:**
1. **Diagnosis:** Corrosive ingestion (Alkali/Acid caustic burn).
2. **Immediate Management:**
   - Airway assessment and maintenance (intubation/tracheostomy if severe stridor is present).
   - IV fluid resuscitation and analgesia.
   - Nil per os (NPO) status.
3. **Investigation & Timing:** Early upper GI endoscopy (Esophagogastroduodenoscopy - EGD) performed within 12 to 24 hours of ingestion.
4. **Contraindications:**
   - Induction of emesis (causes re-exposure of esophagus to corrosive).
   - Gastric lavage or chemical neutralization (causes thermal injury and perforation risk).

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
