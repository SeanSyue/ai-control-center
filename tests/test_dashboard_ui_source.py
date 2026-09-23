from __future__ import annotations

from pathlib import Path


def test_main_window_includes_dashboard_tab_first() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    # 2026-08-23 砍掉原生面板之後，這裡不再問「是哪一個類別」——
    # 只剩一個。守的是**這一頁存在，而且排在對話面板前面**。
    assert "self.dashboard_panel = " in source
    assert "_select_project_from_dashboard" in source
    assert source.index("self.dashboard_panel = ") < source.index(
        "self.session_panel = SessionTimelinePanel"
    )


def test_main_window_wires_queue_scheduler_to_dashboard() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert "queue_capacity_available.connect" in source
    assert "_maybe_start_next_queued_project" in source
    assert "queued_start_candidates" in source
    assert "_try_acquire_project_lock" in source
    assert "ProjectDashboardService.release_lock(self.project_path)" in source


def test_the_queue_capacity_signal_is_still_wired_to_the_scheduler() -> None:
    """佇列有空位 → 排程器要被叫醒。這條線不准斷。

    **今天沒有任何人 emit 它**，而那不是這條檢查放水，是一個查到的缺口：
    加入佇列／移出佇列／暫停／繼續／同時執行上限這五個控制項，只存在於
    2026-08-23 砍掉的那個原生面板上——而那個面板**從來沒有被建立過**
    （`self.dashboard_panel = DashboardWebPanel()`）。所以那五顆按鈕在
    使用者的畫面上一直都不存在，不是被這次刪除弄丟的。

    佇列本身還會動：一次執行結束之後 `on_completed` 會叫一次排程器。
    差的是「使用者主動把上限調高」那條路——**調了不會有事發生**。

    記在 `docs/還沒做完的-2026-08-22.md`。這條檢查守的是：那一天補上控制項
    的時候，這根接線還在。
    """
    panel = Path("app/ui/dashboard_web_panel.py").read_text(encoding="utf-8")
    window = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert "queue_capacity_available = Signal()" in panel
    assert "queue_capacity_available.connect" in window
    assert "_maybe_start_next_queued_project" in window
