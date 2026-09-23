"""為什麼這個專案現在不能啟動（ROADMAP P4）。

排程器本來只回答一半：六個 `continue`，每一個都是使用者會想被告知的理由，
全部被丟掉。從畫面上看，那個專案就只是待在佇列裡不動。

**這裡最重要的一條斷言不是任何一個理由的文字，而是「理由跟篩選是同一段程式
跑出來的」**——分成兩份的話，它們只會在其中一份被改到之前保持一致，
之後畫面就會很有自信地解釋一個不是真正原因的原因。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.lineup import shown_name
from app.services.project_dashboard import (
    DashboardQueueStore,
    ProjectDashboardService,
)
from app.services.project_registry import ProjectEntry


def make_project(root: Path, name: str, with_task: bool = True) -> ProjectEntry:
    project = root / name
    (project / ".aicc").mkdir(parents=True)
    if with_task:
        (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    return ProjectEntry(name, str(project))


@pytest.fixture
def service(tmp_path: Path) -> ProjectDashboardService:
    svc = ProjectDashboardService()
    svc.queue_store = DashboardQueueStore(path=tmp_path / "queue.json")
    return svc


def reasons(readiness) -> dict[str, str]:
    return {block.name: block.reason for block in readiness.blocked}


# ---- the reasons ---------------------------------------------------------


def test_a_project_nobody_queued_is_told_so(
    tmp_path: Path, service
) -> None:
    """The most common answer to 「為什麼它沒動」, and the easiest to fix.

    Omitted before, which left the honest answer 「你沒有把它加進佇列」 as the
    one thing the screen could not say.
    """
    projects = [make_project(tmp_path, "a")]

    assert reasons(service.start_readiness(projects)) == {"a": "not_queued"}


def test_a_paused_queue_says_so_on_every_queued_project(
    tmp_path: Path, service
) -> None:
    projects = [make_project(tmp_path, "a"), make_project(tmp_path, "b")]
    for project in projects:
        service.queue_store.enqueue(project.resolved_path)
    service.queue_store.set_paused(True)

    readiness = service.start_readiness(projects)

    assert readiness.candidates == []
    assert reasons(readiness) == {"a": "queue_paused", "b": "queue_paused"}


def test_hitting_the_limit_says_what_the_limit_is(
    tmp_path: Path, service, monkeypatch
) -> None:
    """A number the user set and then forgot. 「上限是 1，已經有 1 個在跑」
    is actionable; 「不能啟動」 is not."""
    projects = [make_project(tmp_path, "a"), make_project(tmp_path, "b")]
    for project in projects:
        service.queue_store.enqueue(project.resolved_path)
    service.queue_store.set_concurrency_limit(1)
    monkeypatch.setattr(
        ProjectDashboardService,
        "_build_item",
        lambda self, project, queued_paths=None: type(
            "Item", (), {"is_running": True}
        )(),
    )

    readiness = service.start_readiness(projects)

    assert readiness.candidates == []
    assert set(reasons(readiness).values()) == {"at_limit"}
    detail = readiness.blocked[0].detail
    assert "上限是 1" in detail
    assert "已經有 2 個在跑" in detail


def test_a_project_with_no_task_file_is_told_so(
    tmp_path: Path, service
) -> None:
    project = make_project(tmp_path, "a", with_task=False)
    service.queue_store.enqueue(project.resolved_path)

    assert reasons(service.start_readiness([project])) == {"a": "no_task"}


def test_a_missing_folder_is_told_apart_from_a_missing_task(
    tmp_path: Path, service
) -> None:
    project = ProjectEntry("走了", str(tmp_path / "gone"))
    service.queue_store.enqueue(project.resolved_path)

    assert reasons(service.start_readiness([project])) == {"走了": "missing"}


def test_a_stale_lock_and_a_live_lock_are_different_answers(
    tmp_path: Path, service
) -> None:
    """The fixes differ: 「清掉就好」 versus 「等它跑完」.

    Reported as one reason before, which made the harmless case look alarming
    and the real case look dismissible.
    """
    live = make_project(tmp_path, "跑著的")
    stale = make_project(tmp_path, "卡住的")
    for project in (live, stale):
        service.queue_store.enqueue(project.resolved_path)
    ProjectDashboardService.acquire_lock(live.resolved_path, "run-live")
    ProjectDashboardService.acquire_lock(stale.resolved_path, "run-stale")

    # Aged through the lock's own `created_at`, not the file's mtime. An
    # earlier draft used `os.utime` and the test passed for the wrong reason:
    # `is_stale_lock` never reads mtime, so the ageing never reached the thing
    # being measured.
    import json
    from datetime import datetime, timedelta, timezone

    lock = ProjectDashboardService.lock_path(stale.resolved_path)
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["created_at"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()
    lock.write_text(json.dumps(payload), encoding="utf-8")

    assert reasons(service.start_readiness([live, stale])) == {
        "跑著的": "locked",
        "卡住的": "stale_lock",
    }


def test_a_ready_project_is_a_candidate_and_not_also_a_blocker(
    tmp_path: Path, service
) -> None:
    project = make_project(tmp_path, "a")
    service.queue_store.enqueue(project.resolved_path)

    readiness = service.start_readiness([project])

    assert [p.name for p in readiness.candidates] == ["a"]
    assert readiness.blocked == []


# ---- the part that matters most -----------------------------------------


def test_the_reasons_and_the_scheduler_cannot_disagree(
    tmp_path: Path, service
) -> None:
    """Every project is in exactly one of the two lists, always.

    This is the assertion the whole design is for. A separate 「為什麼不能跑」
    function would pass every test above and still drift from the scheduler
    the first time either changed - and the failure mode is a screen that
    confidently explains a reason that is not the one holding the project
    back.
    """
    ready = make_project(tmp_path, "可以跑")
    no_task = make_project(tmp_path, "沒任務", with_task=False)
    never_queued = make_project(tmp_path, "沒排隊")
    gone = ProjectEntry("不見了", str(tmp_path / "gone"))
    projects = [ready, no_task, never_queued, gone]
    for project in (ready, no_task, gone):
        service.queue_store.enqueue(project.resolved_path)

    readiness = service.start_readiness(projects)

    named = [p.name for p in readiness.candidates] + [
        b.name for b in readiness.blocked
    ]
    assert sorted(named) == sorted(p.name for p in projects)
    assert len(named) == len(set(named)), "同一個專案不可以又能跑又不能跑"


def test_the_scheduler_still_answers_through_the_old_entry_point(
    tmp_path: Path, service
) -> None:
    # `queued_start_candidates` is what the auto-scheduler calls. Its answer
    # must be exactly the readiness pass's candidates - if the two could
    # differ, the screen would explain one thing and the machine do another.
    ready = make_project(tmp_path, "a")
    blocked = make_project(tmp_path, "b", with_task=False)
    for project in (ready, blocked):
        service.queue_store.enqueue(project.resolved_path)

    projects = [ready, blocked]

    assert [p.name for p in service.queued_start_candidates(projects)] == [
        p.name for p in service.start_readiness(projects).candidates
    ]


# ---- reaching the screen -------------------------------------------------


def test_the_dashboard_puts_the_reason_on_the_card(
    tmp_path: Path, monkeypatch
) -> None:
    """On the card itself, so nobody cross-references two lists.

    A separate list of names would make the user answer one question by
    reading two places, which is what the sidebar already made them do.

    **Asserted on the card, not on a side-channel.** This used to read a
    top-level `blocked` map that the panel also pushed - and that map was
    never rendered by anything, so the test was checking a shape rather than
    a screen. The map is gone (see `dashboard_web_panel`); the card row is
    what the user actually sees, so that is what is pinned here.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.ui.dashboard_web_panel import DashboardWebPanel

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])

    ready = make_project(tmp_path, "可以跑")
    broken = make_project(tmp_path, "沒任務", with_task=False)
    panel = DashboardWebPanel()
    panel.service.queue_store = DashboardQueueStore(path=tmp_path / "queue.json")
    for project in (ready, broken):
        panel.service.queue_store.enqueue(project.resolved_path)
    panel.refresh([ready, broken])

    cards = {
        entry["id"]: entry["detail"] for entry in panel.collect_state()["projects"]
    }

    assert "為什麼沒動" in cards[str(broken.resolved_path)]
    # A project that can start says nothing at all - that is how the card
    # tells 「可以跑」 from 「跑不了但我沒說原因」.
    assert "為什麼沒動" not in cards[str(ready.resolved_path)]
    # And the reason names the actual obstacle rather than a status code.
    assert "任務" in cards[str(broken.resolved_path)]["為什麼沒動"]

    panel.deleteLater()


# ---- 失敗在哪一格（P4 drill-down） ---------------------------------------


def _failed_run(project: Path, run_id: str = "run-1") -> None:
    """A finished run that ended in an error, with a step log behind it."""
    from app.services.step_log import StepLog, StepRecord
    from app.services.timeline_store import TimelineStore

    timeline = TimelineStore(project)
    timeline.create_run(run_id, "把設定頁修好")
    timeline.update_run(run_id, status="CODEX_ERROR")
    log = StepLog(project)
    log.append(
        run_id,
        StepRecord(
            index=0, role="author", provider="claude", event_type="task",
            read_only=False, prompt="p", output="ok", success=True,
            started_at="2026-08-10T01:00:00", finished_at="2026-08-10T01:00:10",
        ),
    )
    log.append(
        run_id,
        StepRecord(
            index=1, role="reviewer", provider="codex", event_type="review",
            read_only=True, prompt="p", output="", success=False,
            started_at="2026-08-10T01:01:00", finished_at="2026-08-10T01:01:05",
        ),
    )


def test_a_failed_project_says_which_seat_died(
    tmp_path: Path, service
) -> None:
    """「錯誤」 alone sends a person to the log to look up something the
    machine already wrote down."""
    project = make_project(tmp_path, "壞掉的")
    _failed_run(project.resolved_path)

    _, items = service.build([project])

    assert items[0].status == "dash.state.error"
    assert items[0].status_label == "錯誤"
    # 卡片上印的是**那一格的名字**（2026-08-25 之前是拿名字去查一張
    # 用出廠名字當鍵的工作表，自訂名字一律查不到）。
    assert items[0].failed_step.startswith(shown_name("reviewer"))
    assert "review" in items[0].failed_step


def test_a_healthy_project_has_no_failure_row(tmp_path: Path, service) -> None:
    # Empty means 「不知道」, and the card omits the row entirely - an
    # always-present 「失敗在哪一格：—」 would put a dash on every good card.
    project = make_project(tmp_path, "好的")

    _, items = service.build([project])

    assert items[0].failed_step == ""


def test_a_run_from_before_the_step_log_says_nothing_rather_than_guessing(
    tmp_path: Path, service
) -> None:
    from app.services.timeline_store import TimelineStore

    project = make_project(tmp_path, "老的")
    timeline = TimelineStore(project.resolved_path)
    timeline.create_run("old-run", "任務")
    timeline.update_run("old-run", status="CLAUDE_ERROR")

    _, items = service.build([project])

    assert items[0].status == "dash.state.error"
    assert items[0].status_label == "錯誤"
    assert items[0].failed_step == ""


def test_the_failure_row_actually_reaches_the_card(
    tmp_path: Path, monkeypatch
) -> None:
    """The whole point of this one is that it lands on screen with no page
    change - the card renders whatever keys arrive in `detail`.

    Found by negative control: every assertion above was about the service
    layer, so deleting the row from the panel left them all green while the
    feature reached nobody.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.ui.dashboard_web_panel import DashboardWebPanel

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])

    broken = make_project(tmp_path, "壞掉的")
    healthy = make_project(tmp_path, "好的")
    _failed_run(broken.resolved_path)

    panel = DashboardWebPanel()
    panel.service.queue_store = DashboardQueueStore(path=tmp_path / "queue.json")
    panel.refresh([broken, healthy])

    cards = {c["name"]: c["detail"] for c in panel.collect_state()["projects"]}

    assert "失敗在哪一格" in cards["壞掉的"]
    assert cards["壞掉的"]["失敗在哪一格"].startswith(shown_name("reviewer"))
    # And nothing on the healthy one - a dash on every good card is noise.
    assert "失敗在哪一格" not in cards["好的"]

    panel.deleteLater()


def test_a_waiting_project_says_what_it_is_waiting_on(
    tmp_path: Path, service
) -> None:
    """「等待決策」 alone is the same non-answer 「錯誤」 was."""
    from app.services.long_task import DecisionLogEntry, DecisionLogStore
    from app.services.timeline_store import TimelineStore

    project = make_project(tmp_path, "等著的")
    timeline = TimelineStore(project.resolved_path)
    timeline.create_run("run-1", "任務")
    timeline.update_run("run-1", status="ASK_USER")
    DecisionLogStore(project.resolved_path).append(
        DecisionLogEntry(
            run_id="run-1",
            action="ASK_USER",
            question="要不要把舊的匯出檔一起刪掉？",
            created_at="2026-08-10T01:00:00",
        )
    )

    _, items = service.build([project])

    assert items[0].status_label == "等待決策"
    assert items[0].waiting_question == "要不要把舊的匯出檔一起刪掉？"


def test_a_very_long_question_is_trimmed_to_one_line(
    tmp_path: Path, service
) -> None:
    # It sits on a card next to four other rows. A paragraph there pushes
    # everything else off; the full text is on the banner and the audit page,
    # both of which have room.
    from app.services.long_task import DecisionLogEntry, DecisionLogStore
    from app.services.project_dashboard import ProjectDashboardService
    from app.services.timeline_store import TimelineStore

    project = make_project(tmp_path, "話很多")
    timeline = TimelineStore(project.resolved_path)
    timeline.create_run("run-1", "任務")
    timeline.update_run("run-1", status="ASK_USER")
    DecisionLogStore(project.resolved_path).append(
        DecisionLogEntry(
            run_id="run-1",
            action="ASK_USER",
            question="要\n不\n要 " + "很長" * 200,
            created_at="2026-08-10T01:00:00",
        )
    )

    _, items = service.build([project])

    assert len(items[0].waiting_question) <= (
        ProjectDashboardService.WAITING_QUESTION_MAX
    )
    assert "\n" not in items[0].waiting_question
    assert items[0].waiting_question.endswith("…")


def test_the_waiting_row_reaches_the_card(tmp_path: Path, monkeypatch) -> None:
    # Same gap negative control found on 「失敗在哪一格」: every assertion in
    # the service layer stays green while the row reaches nobody.
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.services.long_task import DecisionLogEntry, DecisionLogStore
    from app.services.timeline_store import TimelineStore
    from app.ui.dashboard_web_panel import DashboardWebPanel

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])

    project = make_project(tmp_path, "等著的")
    timeline = TimelineStore(project.resolved_path)
    timeline.create_run("run-1", "任務")
    timeline.update_run("run-1", status="ASK_USER")
    DecisionLogStore(project.resolved_path).append(
        DecisionLogEntry(
            run_id="run-1",
            action="ASK_USER",
            question="要不要通知客戶？",
            created_at="2026-08-10T01:00:00",
        )
    )

    panel = DashboardWebPanel()
    panel.service.queue_store = DashboardQueueStore(path=tmp_path / "queue.json")
    panel.refresh([project])

    detail = panel.collect_state()["projects"][0]["detail"]

    assert detail.get("等你決定") == "要不要通知客戶？"

    panel.deleteLater()


def test_a_queued_but_stuck_project_says_why_on_the_card(
    tmp_path: Path, monkeypatch
) -> None:
    """The actionable case: 「你排了它，它卻沒動」."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.ui.dashboard_web_panel import DashboardWebPanel

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])

    stuck = make_project(tmp_path, "卡住的", with_task=False)
    idle = make_project(tmp_path, "沒排隊的")

    panel = DashboardWebPanel()
    panel.service.queue_store = DashboardQueueStore(path=tmp_path / "queue.json")
    panel.service.queue_store.enqueue(stuck.resolved_path)
    panel.refresh([stuck, idle])

    cards = {c["name"]: c["detail"] for c in panel.collect_state()["projects"]}

    assert "沒有 .aicc/task.md" in cards["卡住的"]["為什麼沒動"]
    # `not_queued` is true of most projects most of the time - a row saying it
    # would be on nearly every card and mean nothing.
    assert "為什麼沒動" not in cards["沒排隊的"]

    panel.deleteLater()
