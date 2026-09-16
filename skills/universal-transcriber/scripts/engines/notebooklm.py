#!/usr/bin/env python3
"""The engine this project has always used, now behind the named boundary.

Deliberately thin. Every behaviour that made NotebookLM work -- splitting a
query across source groups, halving a group the service rejects, quarantining a
source that cannot be read, compacting an over-long assessment prompt -- stays
exactly where it was in universal_transcribe. Moving any of it here would be a
rewrite wearing a refactor's clothes, and this pull request's whole claim is
that the NotebookLM path is untouched.
"""

from __future__ import annotations

from transcriber_models import PhaseQuery, QueryResult

from .base import NOTEBOOKLM


class NotebookLMEngine:
    """Runs a phase prompt through the `nlm` CLI against a notebook."""

    name = NOTEBOOKLM

    def __init__(self, runner=None) -> None:
        """``runner`` is the single-call function this wraps.

        universal_transcribe passes its own ``_run_query_once`` in, rather than
        this module importing it back. The test suite loads that module under
        its own name, so a fresh import here would produce a second copy of it
        and every patch the tests apply would land on the copy nobody runs.
        """
        self._runner = runner

    def _resolve_runner(self):
        if self._runner is not None:
            return self._runner
        import universal_transcribe

        return universal_transcribe._run_query_once

    def run_phase(self, query: PhaseQuery) -> QueryResult:
        return self._resolve_runner()(query, query.query_text)


__all__ = ["NotebookLMEngine"]
