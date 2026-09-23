from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.project_dashboard import (
    DashboardQueueStore,
    ProjectDashboardService,
)
from app.services.project_registry import ProjectEntry, ProjectRegistry
from app.services.timeline_store import TimelineStore


def test_dashboard_summarizes_project_statuses(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    timeline = TimelineStore(project)
    completed = timeline.create_run("完成任務")
    timeline.finalize_run(
        completed,
        {
            "workflow_status": "COMPLETED",
            "review_status": "PASS",
            "fix_attempts": 0,
        },
    )
    waiting = timeline.create_run("等待決策")
    timeline.finalize_run(
        waiting,
        {
            "workflow_status": "ASK_USER",
            "review_status": "ASK_USER",
            "fix_attempts": 0,
        },
    )

    summary, items = ProjectDashboardService().build(
        [ProjectEntry(name="demo", path=str(project))]
    )

    assert summary.total_projects == 1
    assert summary.waiting_projects == 1
    assert summary.completed_today == 1
    # `status` 是 key，`status_label` 才是畫面上那個字。**兩個都測**：
    # 只測 key 的話，key 對到錯的字沒有人會發現；只測字的話，這條測試
    # 在英文版上線那天會紅，而它其實沒有壞。
    assert items[0].status == "outcome.group.ask"
    assert items[0].status_label == "等待決策"
    assert items[0].waiting_count == 1
    assert items[0].can_start is True


def test_dashboard_lock_prevents_duplicate_project_run(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()

    assert ProjectDashboardService.acquire_lock(project, "run-1") is True
    assert ProjectDashboardService.acquire_lock(project, "run-2") is False

    ProjectDashboardService.release_lock(project)

    assert ProjectDashboardService.acquire_lock(project, "run-3") is True


def test_acquire_lock_writes_pid_field(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()

    assert ProjectDashboardService.acquire_lock(project, "run-1") is True

    payload = json.loads(
        ProjectDashboardService.lock_path(project).read_text(encoding="utf-8")
    )
    assert payload["pid"] == os.getpid()
    assert payload["run_id"] == "run-1"


def test_acquire_lock_is_race_safe_under_concurrent_callers(
    tmp_path: Path,
) -> None:
    # Regression test for the Stage 0 hardening: acquire_lock used to be
    # exists()-then-write, which has a TOCTOU window where two callers
    # could both pass the exists() check before either had written the
    # file. Drive many threads at the exact same lock path simultaneously
    # (a threading.Barrier maximizes the chance of genuine overlap) and
    # assert exactly one of them wins, regardless of thread scheduling.
    project = tmp_path / "demo"
    project.mkdir()

    thread_count = 16
    barrier = threading.Barrier(thread_count)
    results: list[bool] = [False] * thread_count

    def attempt(index: int) -> None:
        barrier.wait()
        results[index] = ProjectDashboardService.acquire_lock(
            project, f"run-{index}"
        )

    threads = [
        threading.Thread(target=attempt, args=(i,))
        for i in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(results) == 1


def test_dashboard_detects_stale_lock(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    lock_path = ProjectDashboardService.lock_path(project)
    lock_path.parent.mkdir(parents=True)
    stale_time = datetime.now(timezone.utc) - timedelta(hours=8)
    lock_path.write_text(
        json.dumps({"run_id": "old", "created_at": stale_time.isoformat()}),
        encoding="utf-8",
    )

    assert ProjectDashboardService.is_stale_lock(project) is True


def test_dashboard_queue_store_deduplicates_and_sets_limit(tmp_path: Path) -> None:
    queue = DashboardQueueStore(tmp_path / "queue.json")
    project = tmp_path / "demo"
    project.mkdir()

    queue.enqueue(project)
    queue.enqueue(project)
    queue.set_concurrency_limit(99)
    queue.set_paused(True)
    state = queue.load()

    assert state.queued_paths == (str(project.resolve()),)
    assert state.concurrency_limit == 5
    assert state.paused is True


def test_dashboard_queue_store_save_is_atomic_and_leaves_no_temp_file(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "queue.json"
    queue = DashboardQueueStore(queue_path)
    project = tmp_path / "demo"
    project.mkdir()

    queue.enqueue(project)
    queue.set_concurrency_limit(3)
    queue.set_paused(True)

    assert queue_path.is_file()
    assert not queue_path.with_suffix(".tmp").exists()

    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert payload["queued_paths"] == [str(project.resolve())]
    assert payload["concurrency_limit"] == 3
    assert payload["paused"] is True

    reloaded = queue.load()
    assert reloaded.queued_paths == (str(project.resolve()),)
    assert reloaded.concurrency_limit == 3
    assert reloaded.paused is True


def test_queued_start_candidates_empty_when_paused(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    service = ProjectDashboardService()
    service.queue_store = DashboardQueueStore(tmp_path / "queue.json")
    service.queue_store.enqueue(project)
    service.queue_store.set_paused(True)

    candidates = service.queued_start_candidates(
        [ProjectEntry(name="demo", path=str(project))]
    )

    assert candidates == []


def test_queued_start_candidates_respects_concurrency_limit(
    tmp_path: Path,
) -> None:
    running = tmp_path / "running"
    running.mkdir()
    ProjectRegistry.initialize_project(running)
    running_id = TimelineStore(running).create_run("執行中任務")
    # **鎖也要拿。**2026-08-27 之前這裡只寫 `create_run()`，而那只是在
    # `run.json` 裡留下一句 `RUNNING` 的宣稱——首頁當時直接信那句話，所以
    # 這個案例過得去。現在宣稱要有鎖背書才算「真的在跑」
    #（`is_really_running`），而**這一條要測的是併發上限，不是那個宣稱**，
    # 所以這裡要佈一個真的在跑的專案出來。
    assert ProjectDashboardService.acquire_lock(running, running_id) is True

    queued = tmp_path / "queued"
    queued.mkdir()
    ProjectRegistry.initialize_project(queued)

    service = ProjectDashboardService()
    service.queue_store = DashboardQueueStore(tmp_path / "queue.json")
    service.queue_store.enqueue(queued)
    service.queue_store.set_concurrency_limit(1)

    projects = [
        ProjectEntry(name="running", path=str(running)),
        ProjectEntry(name="queued", path=str(queued)),
    ]

    assert service.queued_start_candidates(projects) == []


def test_queued_start_candidates_skips_missing_task_and_locked_projects(
    tmp_path: Path,
) -> None:
    no_task = tmp_path / "no-task"
    no_task.mkdir()

    locked = tmp_path / "locked"
    locked.mkdir()
    ProjectRegistry.initialize_project(locked)
    ProjectDashboardService.acquire_lock(locked, "run-x")

    ready = tmp_path / "ready"
    ready.mkdir()
    ProjectRegistry.initialize_project(ready)

    service = ProjectDashboardService()
    service.queue_store = DashboardQueueStore(tmp_path / "queue.json")
    service.queue_store.enqueue(no_task)
    service.queue_store.enqueue(locked)
    service.queue_store.enqueue(ready)

    projects = [
        ProjectEntry(name="no-task", path=str(no_task)),
        ProjectEntry(name="locked", path=str(locked)),
        ProjectEntry(name="ready", path=str(ready)),
    ]

    candidates = service.queued_start_candidates(projects)

    assert [item.name for item in candidates] == ["ready"]


def test_queued_start_candidates_preserves_queue_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    first.mkdir()
    ProjectRegistry.initialize_project(first)

    second = tmp_path / "second"
    second.mkdir()
    ProjectRegistry.initialize_project(second)

    service = ProjectDashboardService()
    service.queue_store = DashboardQueueStore(tmp_path / "queue.json")
    service.queue_store.enqueue(second)
    service.queue_store.enqueue(first)

    projects = [
        ProjectEntry(name="first", path=str(first)),
        ProjectEntry(name="second", path=str(second)),
    ]

    candidates = service.queued_start_candidates(projects)

    assert [item.name for item in candidates] == ["second", "first"]


def test_dashboard_marks_queued_project(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    service = ProjectDashboardService()
    service.queue_store = DashboardQueueStore(tmp_path / "queue.json")
    service.queue_store.enqueue(project)

    summary, items = service.build([ProjectEntry(name="demo", path=str(project))])

    assert summary.queued_projects == 1
    assert items[0].is_queued is True
    assert items[0].can_start is False
