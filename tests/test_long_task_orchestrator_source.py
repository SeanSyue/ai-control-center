from __future__ import annotations

from pathlib import Path


def test_orchestrator_writes_long_task_milestone_artifacts() -> None:
    source = Path("app/core/orchestrator.py").read_text(encoding="utf-8")

    assert "LongTaskPlanner" in source
    assert "DecisionLogStore" in source
    assert "milestones.md" in source
    assert "write_plan" in source
    assert "decision_log.append" in source
