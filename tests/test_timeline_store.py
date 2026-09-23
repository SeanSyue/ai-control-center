from pathlib import Path

from app.services.timeline_store import TimelineStore


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    return project


def test_timeline_creates_run_and_loads_events(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = TimelineStore(project)
    run_id = store.create_run("Fix the parser")

    store.append(run_id, "claude", "assistant_message", "Done")
    store.append(run_id, "aicc", "test_result", "2 passed")

    events = store.load_events(run_id=run_id)
    assert [event.source for event in events] == ["claude", "aicc"]
    assert (project / ".aicc" / "runs" / run_id / "task.md").is_file()
    assert (project / ".aicc" / "timeline" / "timeline.jsonl").is_file()


def test_timeline_finalize_writes_result(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = TimelineStore(project)
    run_id = store.create_run("Task")

    store.finalize_run(
        run_id,
        {
            "workflow_status": "COMPLETED",
            "review_status": "PASS",
            "fix_attempts": 0,
            "claude_session_id": "c1",
            "codex_session_id": "x1",
        },
    )

    run = store.list_runs()[0]
    assert run.status == "COMPLETED"
    assert run.claude_session_id == "c1"
    assert (project / ".aicc" / "runs" / run_id / "result.json").is_file()
