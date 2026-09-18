import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).parents[1] / "skills" / "universal-transcriber" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from engines import (
    NOTEBOOKLM_RAW,
    TRANSCRIPTION_ENGINES,
    EngineError,
    TranscriptionEngine,
    get_transcription_engine,
)
from engines.notebooklm_raw import NotebookLMRawEngine, _content_body
from transcriber_models import RemoteSource

NOTEBOOK = "85046dcb-89ea-475c-b782-ca721981d5b8"


def _source(
    title: str, source_id: str = "src-1", source_type: str = "audio"
) -> RemoteSource:
    from source_naming import normalize_source_key, normalize_source_stem

    return RemoteSource(
        source_id=source_id,
        title=title,
        normalized_name=normalize_source_key(title),
        normalized_stem=normalize_source_stem(title),
        source_type=source_type,
        notebook_uuid=NOTEBOOK,
    )


def _engine(sources, content="what the doctor said", **options):
    return NotebookLMRawEngine(
        notebook_uuid=NOTEBOOK,
        source_lister=lambda uuid, config: list(sources),
        content_fetcher=lambda source_id, config, timeout: content,
        **options,
    )


class RegistrationTests(unittest.TestCase):
    def test_it_is_the_default_transcription_engine(self) -> None:
        self.assertEqual(get_transcription_engine().name, NOTEBOOKLM_RAW)

    def test_it_is_offered_ahead_of_whisper(self) -> None:
        self.assertEqual(TRANSCRIPTION_ENGINES[0], NOTEBOOKLM_RAW)

    def test_it_satisfies_the_transcription_protocol(self) -> None:
        self.assertIsInstance(_engine([]), TranscriptionEngine)


class AvailabilityTests(unittest.TestCase):
    def test_a_module_with_no_notebook_cannot_use_this_engine(self) -> None:
        engine = NotebookLMRawEngine(notebook_uuid="")

        self.assertFalse(engine.is_available())

    def test_a_missing_nlm_executable_is_reported_rather_than_raised(self) -> None:
        import nlm_client

        with patch.object(
            nlm_client, "_find_nlm_executable", side_effect=RuntimeError("no nlm")
        ):
            self.assertFalse(_engine([]).is_available())


class SourceMatchingTests(unittest.TestCase):
    def test_it_finds_the_recording_by_name_ignoring_the_extension(self) -> None:
        engine = _engine([_source("مراجعه اشعه.m4a", "audio-1")])

        result = engine.transcribe(Path("/tmp/مراجعه اشعه.m4a"))

        self.assertEqual(result.model, f"{NOTEBOOKLM_RAW}:audio-1")

    def test_an_audio_source_wins_over_a_text_file_of_the_same_name(self) -> None:
        # A module may hold both the recording and a text file of notes named
        # after it. Only one of them has a spoken transcript behind it.
        engine = _engine(
            [
                _source("lecture.txt", "text-1", source_type="generated_text"),
                _source("lecture.m4a", "audio-1", source_type="audio"),
            ]
        )

        result = engine.transcribe(Path("/tmp/lecture.m4a"))

        self.assertEqual(result.model, f"{NOTEBOOKLM_RAW}:audio-1")

    def test_an_unmatched_recording_names_the_audio_actually_present(self) -> None:
        engine = _engine([_source("Doppler.m4a", "audio-9")])

        with self.assertRaises(EngineError) as raised:
            engine.transcribe(Path("/tmp/Mammography.m4a"))

        message = str(raised.exception)
        self.assertIn("Mammography.m4a", message)
        self.assertIn("Doppler.m4a", message)


class TranscriptTests(unittest.TestCase):
    def test_the_text_is_returned_unedited(self) -> None:
        spoken = "اهم حاجه سيمبل\n\nايوه برافو بنعمل ابليشن لليفر تيومر"
        engine = _engine([_source("lecture.m4a")], content=spoken)

        self.assertEqual(engine.transcribe(Path("/tmp/lecture.m4a")).text, spoken)

    def test_a_recording_still_being_processed_says_so(self) -> None:
        engine = _engine([_source("lecture.m4a")], content="   \n  ")

        with self.assertRaisesRegex(EngineError, "still being processed"):
            engine.transcribe(Path("/tmp/lecture.m4a"))

    def test_there_are_no_timestamps_and_the_text_stands_in_for_them(self) -> None:
        # nlm content source returns prose, not segments. RawTranscription was
        # written to tolerate that; this pins the behaviour so a later change
        # to with_timestamps() cannot silently return an empty string here.
        result = _engine([_source("lecture.m4a")], content="spoken").transcribe(
            Path("/tmp/lecture.m4a")
        )

        self.assertEqual(result.segments, ())
        self.assertEqual(result.with_timestamps(), result.text)


class PayloadShapeTests(unittest.TestCase):
    """The CLI decides the shape, so both shapes have to be read."""

    def test_a_json_object_is_unwrapped_to_its_content(self) -> None:
        self.assertEqual(
            _content_body('{"content": "spoken words", "char_count": 11}'),
            "spoken words",
        )

    def test_plain_text_is_passed_through(self) -> None:
        self.assertEqual(_content_body("spoken words"), "spoken words")

    def test_a_transcript_that_merely_starts_with_a_brace_survives(self) -> None:
        self.assertEqual(_content_body("{not json at all"), "{not json at all")

    def test_json_without_a_known_content_key_falls_back_to_the_raw_body(self) -> None:
        payload = '{"error": "nope"}'

        self.assertEqual(_content_body(payload), payload)


if __name__ == "__main__":
    unittest.main()
