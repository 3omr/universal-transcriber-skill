import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = (
    Path(__file__).parents[1] / "skills" / "universal-transcriber" / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

from phase_validation import (
    JOINED_COMMON_WORD_PATTERN,
    MEDICAL_OCR_ALLOWLIST,
)


def _flagged(word: str) -> bool:
    """Whether the validator would call this OCR damage."""
    if word.casefold() in MEDICAL_OCR_ALLOWLIST:
        return False
    return bool(JOINED_COMMON_WORD_PATTERN.search(word))


# Every one of these was rejected as "joined OCR words" by the previous
# pattern, because each merely contains "the" or "and" as a substring. The
# -therapy family alone made the validator unusable in a radiology or oncology
# module: a transcript could not say "radiotherapy".
REAL_TERMS = (
    "radiotherapy",
    "chemotherapy",
    "physiotherapy",
    "brachytherapy",
    "immunotherapy",
    "anesthesiology",
    "anesthesiologist",
    "esthesioneuroblastoma",
    "mesothelioma",
    "atherosclerosis",
    "synthesis",
    "Tomosynthesis",
    "hypothesis",
    "parenthesis",
    "prosthesis",
    "prostheses",
    "aesthetic",
    "understanding",
    "outstanding",
    "nevertheless",
    "furthermore",
    "grandfather",
    "thermometer",
    "Pantomammography",
)

# Real lost-space damage: two or more words run together.
OCR_DAMAGE = (
    "diagnosisandtreatment",
    "causesofthedisease",
    "theresultsofthescan",
    "treatmentwithantibiotics",
    "listofthecauses",
)


class JoinedWordHeuristicTests(unittest.TestCase):
    def test_no_real_medical_term_is_called_ocr_damage(self) -> None:
        # The regression this guards: a past-exam MCQ had "Tomosynthesis"
        # rewritten to "DBT" purely to get past this check, silently changing
        # the verbatim wording of an exam question.
        for word in REAL_TERMS:
            with self.subTest(word):
                self.assertFalse(_flagged(word))

    def test_genuinely_run_together_text_is_still_caught(self) -> None:
        for word in OCR_DAMAGE:
            with self.subTest(word):
                self.assertTrue(_flagged(word))

    def test_a_single_function_word_between_short_fragments_is_not_damage(self) -> None:
        # "radio" + "the" + "rapy" is a word, not three words.
        self.assertFalse(_flagged("radiotherapy"))

    def test_the_allowlist_is_for_rare_exceptions_not_whole_word_families(self) -> None:
        # If the -therapy family ever needs allowlisting again, the pattern has
        # regressed and the allowlist is papering over it.
        for word in ("radiotherapy", "chemotherapy", "synthesis"):
            with self.subTest(word):
                self.assertNotIn(word, MEDICAL_OCR_ALLOWLIST)


if __name__ == "__main__":
    unittest.main()
