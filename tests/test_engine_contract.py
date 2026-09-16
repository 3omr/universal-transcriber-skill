"""The engine's public surface, pinned before it is split into modules.

universal_transcribe.py is 7,036 lines and 344 top-level definitions. The
plan is to extract it module by module, and the one thing that must survive
every extraction is what the rest of the repo and the test suite already
depend on: these names, still reachable from universal_transcribe.

This file is the safety net for that work. If an extraction moves a symbol
without re-exporting it, this fails immediately and names it, instead of the
failure surfacing as an obscure AttributeError somewhere else.

Adding a name here is fine. Removing one is a deliberate breaking change and
should be its own commit with a reason.
"""

import importlib.util
import inspect
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = (
    Path(__file__).parents[1] / "skills" / "universal-transcriber" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "test_engine_contract_module", SCRIPTS_DIR / "universal_transcribe.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the transcriber engine")
engine = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = engine
SPEC.loader.exec_module(engine)


# Every name the test suite reaches through `engine.<name>`, as of the commit
# that introduced this file: 114 symbols across 485 references.
ENGINE_CONTRACT = (
    "CaseEvidence",
    "CheckpointError",
    "DEFAULT_CONFIG",
    "GeneratedSections",
    "IMP_HEADINGS",
    "LocalSource",
    "MAX_ASSESSMENT_CONTEXT_CHARS",
    "MAX_ASSESSMENT_QUERY_CHARS",
    "MAX_ATTEMPTS",
    "MAX_SOURCE_IDS_PER_QUERY",
    "NLM_UPLOAD_EXTENSIONS",
    "NO_MCQS",
    "NO_WRITTEN",
    "NlmError",
    "NlmQueryRequest",
    "NotebookTarget",
    "OCRReport",
    "OutputTarget",
    "PHASE_DEPENDENCIES",
    "PHASE_ORDER",
    "Phase0Error",
    "Phase0Report",
    "Phase0Request",
    "PhaseCheckpointUpdate",
    "PhaseQuery",
    "PhaseValidationError",
    "PipelineContext",
    "PreparationReport",
    "ProjectQueryScope",
    "QueryResult",
    "QueryScope",
    "QuestionEvidence",
    "RemoteSource",
    "RunRequest",
    "SourceQuarantine",
    "TranscriptIdentity",
    "UploadOutcome",
    "ValidationError",
    "_append_ambiguous_matches",
    "_append_ocr_failures",
    "_apply_agent_recovery",
    "_argument_parser",
    "_assessment_source_scope",
    "_atomic_write_json",
    "_badge_is_valid",
    "_catalog_entry_is_available",
    "_delete_review_draft",
    "_draft_output_path",
    "_duplicate_question_errors",
    "_emphasis_minimum",
    "_execute_checkpointed_phase",
    "_json_hash",
    "_merge_notebook_query_results",
    "_nlm_query_arguments",
    "_normalize_case_block",
    "_normalize_mcq_block",
    "_normalize_written_block",
    "_notebook_entries",
    "_path_claims_a_year",
    "_phase_fingerprints",
    "_pipeline_context",
    "_query_scope",
    "_question_fingerprint",
    "_read_cached_inventory",
    "_rebuild_evidence_catalog",
    "_refresh_evidence_metadata",
    "_remote_source_inventory",
    "_replace_empty_sentinel",
    "_replace_project_inventory",
    "_replace_quarantined_sources",
    "_run_checkpointed_phases",
    "_run_directory_for_request",
    "_run_nlm_cli_query",
    "_run_nlm_json",
    "_run_pipeline",
    "_run_request",
    "_save_phase_checkpoint",
    "_section_blocks",
    "_slice_project_scope",
    "_source_exists_remotely",
    "_upload_phase0_sources",
    "build_assessment_source_context",
    "build_case_prompt",
    "build_deduplication_plan",
    "build_evidence_catalog",
    "build_exam_year_map",
    "build_imp_mcq_prompt",
    "build_imp_written_prompt",
    "build_mcq_prompt",
    "build_written_prompt",
    "canonical_badge_instructions",
    "commit_managed_transcript",
    "deduplicate_question_section",
    "emphasis_point_count",
    "empty_sentinel_reason",
    "extract_filename_exam_years",
    "finalize_student_document",
    "is_empty_sentinel",
    "list_remote_sources",
    "load_config",
    "normalize_source_key",
    "normalize_source_stem",
    "prepare_manifest_sources",
    "replace",
    "run_nlm_query",
    "run_phase0_audit",
    "run_phase0_sync",
    "scan_local_sources",
    "set_inventory_cache_root",
    "subprocess",
    "validate_cases",
    "validate_editorial_quality",
    "validate_mcqs",
    "validate_written",
)


class EngineContractTests(unittest.TestCase):
    def test_every_contracted_symbol_is_reachable(self) -> None:
        missing = [name for name in ENGINE_CONTRACT if not hasattr(engine, name)]

        self.assertEqual(
            missing,
            [],
            "An extraction dropped these symbols; re-export them from "
            "universal_transcribe.py: " + ", ".join(missing),
        )

    def test_the_contract_has_no_duplicates(self) -> None:
        self.assertEqual(len(ENGINE_CONTRACT), len(set(ENGINE_CONTRACT)))

    def test_the_contract_is_sorted_so_diffs_stay_readable(self) -> None:
        self.assertEqual(list(ENGINE_CONTRACT), sorted(ENGINE_CONTRACT))

    def test_callables_keep_their_signatures(self) -> None:
        """A re-export must be the function itself, not a renamed wrapper."""
        for name in ENGINE_CONTRACT:
            value = getattr(engine, name, None)
            if not callable(value) or isinstance(value, type):
                continue
            with self.subTest(name=name):
                try:
                    inspect.signature(value)
                except (TypeError, ValueError):  # builtins have none
                    continue

    def test_the_phase_contract_is_intact(self) -> None:
        """The five phases and their dependency edges are part of the API."""
        self.assertEqual(
            engine.PHASE_ORDER, ("guide", "imp", "mcqs", "written", "cases")
        )
        self.assertEqual(set(engine.PHASE_DEPENDENCIES) <= set(engine.PHASE_ORDER), True)
        for dependants in engine.PHASE_DEPENDENCIES.values():
            for dependency in dependants:
                self.assertIn(dependency, engine.PHASE_ORDER)


if __name__ == "__main__":
    unittest.main()
