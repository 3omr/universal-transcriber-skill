import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS_DIR = (
    Path(__file__).parents[1]
    / "skills"
    / "universal-transcriber"
    / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import engines
from engines import (
    NOTEBOOKLM,
    WHISPER,
    EngineError,
    EngineUnavailable,
    PhaseEngine,
    RawTranscription,
    TranscriptionEngine,
    TranscriptionSegment,
    get_phase_engine,
    get_transcription_engine,
)
from engines.notebooklm import NotebookLMEngine
from engines.whisper import INSTALL_HINT, WhisperEngine
from transcriber_models import NotebookTarget, PhaseQuery, QueryResult

SPEC = importlib.util.spec_from_file_location(
    "test_engines_engine", SCRIPTS_DIR / "universal_transcribe.py"
)
engine_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = engine_module
SPEC.loader.exec_module(engine_module)


def _query(text: str = "Create the section", phase: str = "MCQs") -> PhaseQuery:
    return PhaseQuery(
        config={},
        notebook=NotebookTarget("one", "one", "url", "One"),
        query_text=text,
        phase_name=phase,
        validator=lambda _result: [],
        source_ids=("source-one",),
        source_names=("one.pdf",),
    )


class RegistryTests(unittest.TestCase):
    def test_the_default_phase_engine_is_notebooklm(self) -> None:
        self.assertEqual(get_phase_engine().name, NOTEBOOKLM)

    def test_engines_satisfy_the_protocols_they_claim(self) -> None:
        self.assertIsInstance(get_phase_engine(), PhaseEngine)
        self.assertIsInstance(get_transcription_engine(), TranscriptionEngine)

    def test_asking_whisper_to_answer_a_phase_prompt_explains_why_not(self) -> None:
        # Whisper returns what was said; it does not write the five sections.
        with self.assertRaises(EngineError) as raised:
            get_phase_engine(WHISPER)

        self.assertIn("verbatim", str(raised.exception))

    def test_an_unknown_transcription_engine_is_refused(self) -> None:
        with self.assertRaisesRegex(EngineError, "Unknown transcription engine"):
            get_transcription_engine("deepgram")


class NotebookLMEngineTests(unittest.TestCase):
    """The wrapper must add nothing at all to the existing call."""

    def test_it_forwards_the_query_and_returns_the_result_unchanged(self) -> None:
        expected = QueryResult(answer="section text", source_names=("one.pdf",))
        seen = {}

        def runner(query, query_text):
            seen["query"] = query
            seen["text"] = query_text
            return expected

        result = NotebookLMEngine(runner=runner).run_phase(_query())

        self.assertIs(result, expected)
        self.assertEqual(seen["text"], "Create the section")
        self.assertEqual(seen["query"].phase_name, "MCQs")

    def test_it_sends_the_text_the_query_carries_not_a_remembered_one(self) -> None:
        # run_phase_query rewrites the text between attempts (repair context,
        # compact retry). The engine must send what it is handed.
        texts = []
        engine = NotebookLMEngine(runner=lambda q, text: texts.append(text) or QueryResult(answer="x" * 60))

        engine.run_phase(_query("first"))
        engine.run_phase(_query("second, repaired"))

        self.assertEqual(texts, ["first", "second, repaired"])


class PhaseEngineWiringTests(unittest.TestCase):
    """run_phase_query must stay engine-neutral, and the old name must work."""

    def tearDown(self) -> None:
        engine_module.set_phase_engine(None)

    def test_the_historical_name_still_resolves(self) -> None:
        self.assertIs(engine_module.run_nlm_query, engine_module.run_phase_query)

    def test_a_substituted_engine_answers_the_phase(self) -> None:
        class StubEngine:
            name = "stub"

            def __init__(self) -> None:
                self.calls = 0

            def run_phase(self, query: PhaseQuery) -> QueryResult:
                self.calls += 1
                return QueryResult(answer="a grounded answer " * 5)

        stub = StubEngine()
        engine_module.set_phase_engine(stub)

        result = engine_module.run_phase_query(_query())

        self.assertEqual(stub.calls, 1)
        self.assertIn("grounded answer", result.answer)

    def test_retrying_a_failed_call_stays_outside_the_engine(self) -> None:
        # The engine does one call. Deciding to call again belongs to
        # run_phase_query and must not migrate into a backend -- a backend that
        # retried internally would defeat the quarantine and compact-prompt
        # recovery built around these attempts.
        attempts = []

        class FlakyEngine:
            name = "flaky"

            def run_phase(self, query: PhaseQuery) -> QueryResult:
                attempts.append(query.query_text)
                if len(attempts) == 1:
                    raise engine_module.NlmError("transient upstream failure")
                return QueryResult(answer="a properly grounded answer " * 4)

        engine_module.set_phase_engine(FlakyEngine())
        result = engine_module.run_phase_query(_query())

        self.assertEqual(len(attempts), 2)
        self.assertIn("properly grounded", result.answer)

    def test_a_short_answer_stops_early_for_agent_repair(self) -> None:
        # Not a retry: an answer that came back but is unusable is handed to
        # the Agent rather than asked for again, and that decision also lives
        # outside the engine.
        class ThinEngine:
            name = "thin"

            def run_phase(self, query: PhaseQuery) -> QueryResult:
                return QueryResult(answer="too short")

        engine_module.set_phase_engine(ThinEngine())

        with self.assertRaises(engine_module.PhaseValidationError) as raised:
            engine_module.run_phase_query(_query())

        self.assertIn("too short", str(raised.exception))

    def test_setting_none_restores_the_default(self) -> None:
        engine_module.set_phase_engine(SimpleNamespace(name="stub"))
        engine_module.set_phase_engine(None)

        self.assertEqual(engine_module.get_phase_engine().name, NOTEBOOKLM)


class WhisperEngineTests(unittest.TestCase):
    class FakeSegment:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    def _fake_model(self, segments, language="ar", duration=90.0):
        class Model:
            def __init__(self, *_args, **_kwargs):
                pass

            def transcribe(self, _path, **_kwargs):
                return iter(segments), SimpleNamespace(language=language, duration=duration)

        return lambda *args, **kwargs: Model()

    def test_it_reports_unavailable_rather_than_raising_on_import(self) -> None:
        with patch.object(engines.whisper.importlib.util, "find_spec", return_value=None):
            self.assertFalse(WhisperEngine().is_available())

    def test_a_missing_install_names_what_to_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            recording = Path(temporary_directory) / "lecture.m4a"
            recording.write_bytes(b"audio")

            with patch.dict(sys.modules, {"faster_whisper": None}):
                with self.assertRaises(EngineUnavailable) as raised:
                    WhisperEngine().transcribe(recording)

        self.assertIn("pip install faster-whisper", str(raised.exception))
        self.assertEqual(INSTALL_HINT, str(raised.exception))

    def test_a_missing_recording_is_reported_before_the_model_loads(self) -> None:
        with self.assertRaisesRegex(EngineError, "Recording not found"):
            WhisperEngine(model_factory=self._fake_model([])).transcribe(
                Path("/nowhere/absent.m4a")
            )

    def test_the_transcript_is_verbatim_and_in_order(self) -> None:
        segments = [
            self.FakeSegment(0.0, 4.0, " التريتمنت في الاطفال "),
            self.FakeSegment(4.0, 9.0, " كونجنتال جلوكوما only surgical "),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            recording = Path(temporary_directory) / "lecture.m4a"
            recording.write_bytes(b"audio")
            result = WhisperEngine(model_factory=self._fake_model(segments)).transcribe(recording)

        # Joined, stripped, nothing reworded, nothing dropped.
        self.assertEqual(
            result.text, "التريتمنت في الاطفال كونجنتال جلوكوما only surgical"
        )
        self.assertEqual(result.language, "ar")
        self.assertEqual(len(result.segments), 2)

    def test_timestamps_are_rendered_for_navigating_the_audio(self) -> None:
        segments = [self.FakeSegment(0.0, 3.0, "first"), self.FakeSegment(65.0, 70.0, "later")]
        with tempfile.TemporaryDirectory() as temporary_directory:
            recording = Path(temporary_directory) / "lecture.m4a"
            recording.write_bytes(b"audio")
            result = WhisperEngine(model_factory=self._fake_model(segments)).transcribe(recording)

        self.assertEqual(result.with_timestamps(), "[00:00:00] first\n[00:01:05] later")

    def test_a_transcript_with_no_segments_still_returns_its_text(self) -> None:
        raw = RawTranscription(recording=Path("x.m4a"), text="plain text")

        self.assertEqual(raw.with_timestamps(), "plain text")
        self.assertEqual(raw.word_count, 2)


class SegmentTests(unittest.TestCase):
    def test_a_timestamp_crosses_the_hour_correctly(self) -> None:
        self.assertEqual(TranscriptionSegment(3725.0, 3730.0, "x").timestamp, "01:02:05")


if __name__ == "__main__":
    unittest.main()
