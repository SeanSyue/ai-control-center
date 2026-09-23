"""首頁不准把「AI 正在等你回答」畫成「閒置」。

2026-08-23 頁面設計那一輪量到的：

> `ProjectDashboardService.WAITING_STATUSES = {"ASK_USER"}`，而需求確認停下來
> 問使用者的時候，收場碼是 `INTAKE_ASKING`。它不在那個集合裡，於是掉到「待命」
> → 泳道 `idle` → **灰點、標籤「閒置」**。
>
> AI 正在等使用者回答，而首頁說那個專案沒事。

而且執行控制列那一側是對的（金色脈動）。**同一件事，兩個畫面講兩件事。**

成因不是誰粗心，是那份集合是**手抄的**。抄一份就會有第二份答案，而第二份
永遠慢一步。現在它從 `app/core/outcomes.py` 算出來。
"""
from __future__ import annotations

from pathlib import Path

from app.core import outcomes
from app.services.project_dashboard import ProjectDashboardService
from app.services.project_registry import ProjectEntry, ProjectRegistry
from app.services.timeline_store import TimelineStore


def _project_whose_last_run_ended_as(tmp_path: Path, result: dict) -> Path:
    project = tmp_path / "demo"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    timeline = TimelineStore(project)
    timeline.finalize_run(timeline.create_run("一次執行"), result)
    return project


def _status_on_the_home_page(project: Path) -> str:
    _summary, items = ProjectDashboardService().build(
        [ProjectEntry(name="demo", path=str(project))]
    )
    # **`status_label` 不是 `status`。**這份檔案守的是「使用者在首頁上
    # 讀到哪個字」，而 `status` 現在是 key（`dash.state.idle`）。比 key 的話
    # 這裡就只證明分支對，證明不了那個 key 對到的字沒有被接錯。
    return items[0].status_label


def test_a_project_whose_ai_is_asking_is_not_idle(tmp_path: Path) -> None:
    """這一條是這份檔案存在的理由。"""
    project = _project_whose_last_run_ended_as(
        tmp_path,
        {
            "workflow_status": "INTAKE_ASKING",
            "review_status": "ASK_USER",
            "fix_attempts": 0,
        },
    )
    assert _status_on_the_home_page(project) == "等待決策"


def test_every_ending_that_waits_for_a_person_counts_as_waiting(
    tmp_path: Path,
) -> None:
    """新增一種「停下來問」的收場時，首頁**不用改**也要跟著對。

    改壞的樣子：有人把集合寫死回 `{"ASK_USER"}`。
    """
    waiting = {
        status
        for status in outcomes.known_statuses()
        if status != outcomes.UNDECIDABLE_STATUS
        and outcomes.describe({"workflow_status": status}).waiting
    }
    assert waiting, "撈不到任何一種等待的收場——這條檢查現在什麼都沒在守"
    assert waiting <= set(ProjectDashboardService.WAITING_STATUSES)


def test_a_run_that_needs_a_person_is_not_filed_as_idle(tmp_path: Path) -> None:
    """跑完了、沒人在等你回話、但有一件事在等你動手——那不是「閒置」。

    以前這十幾種收場全部掉進 `待命`，在首頁上跟「從來沒跑過」長得一模一樣。
    """
    project = _project_whose_last_run_ended_as(
        tmp_path,
        {
            "workflow_status": "APPROVAL_REJECTED",
            "review_status": "NOT_RUN",
            "fix_attempts": 0,
        },
    )
    assert _status_on_the_home_page(project) == "要你處理"


def test_a_project_that_never_ran_is_still_idle(tmp_path: Path) -> None:
    """反面：真的沒事的那一種還是要留在「待命」。

    沒有這一條，上面那條可以靠「把所有東西都畫成要你處理」通過——而那樣
    首頁上就永遠有事，等於永遠沒事。
    """
    project = tmp_path / "demo"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    assert _status_on_the_home_page(project) == "待命"


def test_the_home_page_does_not_keep_its_own_list(tmp_path: Path) -> None:
    """那四份集合不准再變成手抄的。

    改壞的樣子就是它原本的樣子：`WAITING_STATUSES = {"ASK_USER"}`。
    """
    source = Path("app/services/project_dashboard.py").read_text(
        encoding="utf-8"
    )
    for field in (
        "WAITING_STATUSES",
        "COMPLETED_STATUSES",
        "NEEDS_YOU_STATUSES",
    ):
        line = next(
            row for row in source.splitlines() if row.strip().startswith(field)
        )
        assert "_statuses_in(" in line, f"{field} 又變成手抄的了：{line}"


def test_half_done_is_not_counted_as_a_failure(tmp_path: Path) -> None:
    """「今日錯誤」只數真的沒跑起來的那幾種。

    把「做了一半」數成錯誤，使用者會去修一個沒壞的東西——那是這整條路線
    要消滅的假紅燈。
    """
    assert "FIX_LIMIT_REACHED" not in ProjectDashboardService.ERROR_STATUSES
    assert "VERIFY_NOT_CONFIGURED" not in ProjectDashboardService.ERROR_STATUSES
    assert "CLAUDE_ERROR" in ProjectDashboardService.ERROR_STATUSES
