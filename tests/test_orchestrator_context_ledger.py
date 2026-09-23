"""The orchestrator has to feed the ledger, or the dashboard has nothing.

The ledger's own tests prove it stores what it is given. These prove it is
given anything at all - and specifically that the two numbers the dashboard
shows come from the run itself: the breakdown of what was sent, and the size of
what a later round chose not to send again.
"""
from __future__ import annotations

from pathlib import Path

from app.core.orchestrator import Orchestrator
from app.services.context_ledger import ContextLedger


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 任務\n量測 context 記錄。\n", encoding="utf-8"
    )
    return project


def _with_context(orchestrator: Orchestrator, text: str, stats: dict) -> None:
    """Stand in for the knowledge store, which needs an indexed project."""
    orchestrator.knowledge.build_context = lambda *a, **k: text
    orchestrator.knowledge.context_stats = lambda *a, **k: stats


STATS = {
    "documents": 3,
    "chunks": 12,
    "contract_characters": 800,
    "memory_characters": 5000,
    "document_characters": 6000,
    "raw_evidence_characters": 1200,
    "total_characters": 13000,
    "estimated_tokens": 3250,
}


def test_a_run_that_sends_context_records_its_breakdown(tmp_path: Path) -> None:
    orchestrator = Orchestrator(make_project(tmp_path))
    _with_context(orchestrator, "專案資料" * 100, STATS)

    orchestrator._knowledge_context("任務")

    record = ContextLedger(orchestrator.project_path).records()[0]
    assert record.run_id == orchestrator.run_id
    assert record.memory_characters == 5000
    assert record.document_characters == 6000
    assert record.estimated_tokens == 3250


def test_a_later_round_records_what_it_did_not_re_send(tmp_path: Path) -> None:
    # The first round sends the context into a fresh CLI session; rounds after
    # that continue that session and skip it. The skipped size is the payload
    # the first round sent, so it is measured rather than estimated.
    orchestrator = Orchestrator(make_project(tmp_path))
    context = "專案資料" * 100
    _with_context(orchestrator, context, STATS)

    first = orchestrator._knowledge_context_for_round("任務", first_round=True)
    second = orchestrator._knowledge_context_for_round("任務", first_round=False)
    third = orchestrator._knowledge_context_for_round("任務", first_round=False)

    assert first == context
    assert second == "" and third == ""

    record = ContextLedger(orchestrator.project_path).records()[0]
    assert record.resend_skipped_rounds == 2
    assert record.resend_skipped_characters == 2 * len(context)


def test_a_project_with_no_context_records_no_saving(tmp_path: Path) -> None:
    # Nothing was sent, so a later round skips nothing. Counting those rounds
    # would report a saving on runs that never had anything to save.
    orchestrator = Orchestrator(make_project(tmp_path))
    _with_context(orchestrator, "", STATS)

    orchestrator._knowledge_context_for_round("任務", first_round=True)
    orchestrator._knowledge_context_for_round("任務", first_round=False)

    assert ContextLedger(orchestrator.project_path).records() == []


def test_a_failing_knowledge_store_does_not_break_the_run(tmp_path: Path) -> None:
    orchestrator = Orchestrator(make_project(tmp_path))

    def explode(*args, **kwargs):
        raise RuntimeError("索引壞了")

    orchestrator.knowledge.build_context = explode

    assert orchestrator._knowledge_context("任務") == ""
    assert ContextLedger(orchestrator.project_path).records() == []
