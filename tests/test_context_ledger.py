"""Context accounting has to be traceable to something that was measured.

The dashboard once showed 「節省 context 38%」 with nothing behind it. The box
was removed rather than left in, and this ledger is what earns it back. So the
tests worth having are less about arithmetic than about provenance: a number
appears only when a run produced it, "saved" means a payload the orchestrator
decided not to re-send, and a day with no runs shows nothing rather than zero.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app.services.context_ledger import ContextLedger

STATS = {
    "contract_characters": 800,
    "memory_characters": 5000,
    "document_characters": 6000,
    "raw_evidence_characters": 1200,
    "total_characters": 13000,
    "estimated_tokens": 3250,
}


def test_a_run_records_the_breakdown_it_actually_carried(tmp_path: Path) -> None:
    ledger = ContextLedger(tmp_path)

    ledger.record_sent("run-1", STATS, profile="balanced")

    record = ledger.records()[0]
    assert record.run_id == "run-1"
    assert record.profile == "balanced"
    assert record.memory_characters == 5000
    assert record.document_characters == 6000
    assert record.total_characters == 13000


def test_skipped_resends_accumulate_across_rounds(tmp_path: Path) -> None:
    # Three rounds after the first, each reusing the same CLI session.
    ledger = ContextLedger(tmp_path)
    ledger.record_sent("run-1", STATS)

    for _ in range(3):
        ledger.record_resend_skipped("run-1", 13000)

    record = ledger.records()[0]
    assert record.resend_skipped_rounds == 3
    assert record.resend_skipped_characters == 39000
    # The point of the figure: what a version with no session reuse would have
    # pushed through the CLI.
    assert record.would_have_sent == 52000


def test_nothing_is_recorded_for_a_zero_length_skip(tmp_path: Path) -> None:
    # A run whose project has no indexed documents sends no context, so there is
    # nothing for a later round to skip. Counting that as a saved round would
    # inflate the tally with rounds that never had anything to save.
    ledger = ContextLedger(tmp_path)
    ledger.record_sent("run-1", STATS)

    ledger.record_resend_skipped("run-1", 0)

    assert ledger.records()[0].resend_skipped_rounds == 0


def test_the_summary_ignores_runs_outside_the_window(tmp_path: Path) -> None:
    ledger = ContextLedger(tmp_path)
    ledger.record_sent("old", STATS)
    ledger.record_sent("new", STATS)

    # Age the first record by rewriting its timestamp, which is what actually
    # happens over time - no clock is mocked.
    payload = json.loads(ledger.path.read_text(encoding="utf-8"))
    payload["runs"][0]["at"] = (datetime.now() - timedelta(days=40)).isoformat()
    ledger.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert ledger.summary(days=30)["runs"] == 1
    assert ledger.summary(days=60)["runs"] == 2


def test_an_empty_window_reports_no_runs(tmp_path: Path) -> None:
    assert ContextLedger(tmp_path).summary(days=1)["runs"] == 0


def test_a_damaged_ledger_is_ignored_rather_than_fatal(tmp_path: Path) -> None:
    # Bookkeeping must never take a run down with it.
    ledger = ContextLedger(tmp_path)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text("{ not json", encoding="utf-8")

    assert ledger.records() == []

    ledger.record_sent("run-1", STATS)
    assert ledger.records()[0].total_characters == 13000


def test_a_ledger_from_a_newer_version_still_reads(tmp_path: Path) -> None:
    # Fields this version does not know about are dropped, not raised on.
    ledger = ContextLedger(tmp_path)
    ledger.record_sent("run-1", STATS)
    payload = json.loads(ledger.path.read_text(encoding="utf-8"))
    payload["runs"][0]["some_future_field"] = 42
    ledger.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert ledger.records()[0].total_characters == 13000


def test_old_entries_are_dropped_once_the_cap_is_reached(tmp_path: Path) -> None:
    from app.services.context_ledger import MAX_ENTRIES

    ledger = ContextLedger(tmp_path)
    for index in range(MAX_ENTRIES + 25):
        ledger.record_sent(f"run-{index}", STATS)

    records = ledger.records()
    assert len(records) == MAX_ENTRIES
    assert records[0].run_id == f"run-{MAX_ENTRIES + 24}"
