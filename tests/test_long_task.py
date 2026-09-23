from __future__ import annotations

from pathlib import Path

from app.services.long_task import (
    DecisionLogEntry,
    DecisionLogStore,
    LongTaskPlanner,
)


def test_long_task_planner_splits_numbered_task() -> None:
    planner = LongTaskPlanner()
    milestones = planner.split_task(
        """
        # 任務
        1. 掃描專案
        2. 修正權限流程
        3. 執行測試
        """,
        max_milestones=3,
    )

    assert [item.title for item in milestones] == [
        "掃描專案",
        "修正權限流程",
        "執行測試",
    ]
    assert milestones[0].status == "pending"


def test_long_task_planner_writes_milestones(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = LongTaskPlanner().write_plan(
        project,
        "run-1",
        "1. First step\n2. Second step",
    )

    assert path == project / ".aicc" / "runs" / "run-1" / "milestones.json"
    assert "First step" in path.read_text(encoding="utf-8")


def test_decision_log_store_round_trip(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = DecisionLogStore(project)

    store.append(
        DecisionLogEntry(
            run_id="run-1",
            action="ASK_USER",
            question="Change schema?",
            reason="Database migration",
            source="claude",
        )
    )

    entries = store.list_entries()
    assert len(entries) == 1
    assert entries[0].action == "ASK_USER"
    assert entries[0].question == "Change schema?"
