"""首頁不准把「認不得的收場」畫成「從來沒跑過」。

## 使用者會看到什麼（沒有這幾條的話）

他按了「結束等待」，AICC 把那一次執行記成 `CANCELLED` 寫進 `run.json`。
回到首頁——那張專案卡是**灰色的點、狀態寫「待命」、副標寫「待命」**，
統計列把它算進「N 個閒置」，而它排在出廠就收起來的那條泳道裡。

跟一個他從來沒開過的專案，畫面上一個像素都不差。

## 為什麼會這樣

`project_dashboard._build_item` 那條 `if/elif` 鏈**沒有 else**。沒有 else
的意思不是「不會發生」，是「發生了就沿用上面那個 `status = "dash.state.idle"`」
——而那正是「從來沒跑過」的那個值。

守在旁邊的 `test_every_ending_knows_what_the_screen_should_say.py` 撈的是
`app/core/workflow_worker.py` 與 `app/core/steps/*.py`，而 `CANCELLED` 是
`app/ui/main_window.py` 寫的。**一條全稱句的檢查，掃的範圍比它說的小。**

## 這幾條擋不住什麼

擋不住「畫成了別的東西，但那個東西也是錯的」——這裡只問「有沒有被畫成
從來沒跑過」，不問「畫成『要你處理』對不對」。真正回答那一題的是卡片上
那一行字，而那一行字現在說的是「要你處理」，不是「我不認得這個」；
更精準的說法要動 `app/i18n.py` 與 `dashboard_web_panel._LANE_BY_KEY`，
見 `docs/WHY.md`。

也擋不住**引擎自己新增一種收場**——那一種由 `outcomes` 那張沒有預設值的表
擋，不由這裡擋。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core import outcomes
from app.services.project_dashboard import ProjectDashboardService
from app.services.project_registry import ProjectEntry, ProjectRegistry
from app.services.timeline_store import TimelineStore

UI = Path(__file__).resolve().parents[1] / "app" / "ui"

# `result["workflow_status"] = "X"` 與 `workflow_status="X"` 兩種寫法。
# 介面那一側今天用的是前者，而它正是被漏掉的那一種。
WRITES_A_STATUS = re.compile(r'workflow_status(?:"\])?\s*=\s*"([A-Z_]+)"')


def _statuses_the_screen_itself_writes() -> set[str]:
    """介面那一側自己寫進 `run.json` 的收場碼。

    **從程式碼裡撈，不是抄一份清單。**抄一份的話，下一個在介面加一種收場的人
    要記得回來改這裡，而「要記得」正是這條檢查要取代的東西。
    """
    found: set[str] = set()
    for path in sorted(UI.rglob("*.py")):
        found |= set(WRITES_A_STATUS.findall(path.read_text(encoding="utf-8")))
    return found


def _project_whose_last_run_ended_as(tmp_path: Path, status: str) -> Path:
    """一個「跑過一次、收場是 `status`」的專案資料夾。

    走的是真的 `TimelineStore`，不是假物件——`finalize_run()` 怎麼把
    `workflow_status` 搬進 `run.json` 的 `status` 欄位，正是這件事的一半，
    假物件組得出來的狀態不算數。
    """
    project = tmp_path / status.lower()
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    timeline = TimelineStore(project)
    run_id = timeline.create_run("跑過一次的任務")
    timeline.finalize_run(
        run_id,
        {"workflow_status": status, "review_status": status, "fix_attempts": 0},
    )
    return project


def test_a_run_the_user_ended_himself_is_not_shown_as_never_run(
    tmp_path: Path,
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞了的樣子：他按過「結束等待」的那個專案，回到首頁是灰點、寫「待命」、
    被算進「N 個閒置」，而且躺在出廠收起來的那條泳道裡——他要展開那一段
    才看得到它，而他沒有理由去展開一段標著「閒置」的東西。
    """
    project = _project_whose_last_run_ended_as(tmp_path, "CANCELLED")

    summary, items = ProjectDashboardService().build(
        [ProjectEntry(name="cancelled", path=str(project))]
    )

    assert items[0].status != "dash.state.idle", (
        "使用者親手結束的那一次執行，首頁說這個專案「待命」——"
        "跟從來沒跑過的專案長得一模一樣"
    )
    assert summary.idle_projects == 0, (
        "統計列把它算進「閒置」了"
    )


def test_the_screen_really_does_write_a_status_of_its_own() -> None:
    """前提檢查：介面那一側**真的**自己寫過收場碼。

    一個都撈不到的話，底下那條全稱檢查會變成恆真——而一條抓不到東西的檢查，
    讀起來跟一條真的守著的檢查一模一樣。
    """
    assert _statuses_the_screen_itself_writes(), (
        "app/ui 底下一個 workflow_status 的指派都撈不到——"
        "底下那條全稱檢查現在是恆真的"
    )


def test_no_ending_this_app_can_write_lands_on_never_run(tmp_path: Path) -> None:
    """全稱句：**這個程式寫得出來的每一種收場**，都不准被畫成「從來沒跑過」。

    兩個來源合起來才是全部：`outcomes` 那張表（引擎的）與 `app/ui` 底下的
    指派（畫面自己的）。只看前者就是 `CANCELLED` 漏出去的那條路。

    改壞的樣子：下一次有人在介面加一種收場（「使用者關掉了視窗」之類的），
    首頁會安靜地把那個專案畫成從來沒開過——而這一條會在他看到之前變紅。
    """
    endings = sorted(outcomes.known_statuses() | _statuses_the_screen_itself_writes())
    service = ProjectDashboardService()

    lying = []
    for ending in endings:
        project = _project_whose_last_run_ended_as(tmp_path, ending)
        _summary, items = service.build(
            [ProjectEntry(name=ending, path=str(project))]
        )
        if items[0].status == "dash.state.idle":
            lying.append(ending)

    assert not lying, (
        "這幾種收場在首頁上跟「從來沒跑過」長得一模一樣：" + "、".join(lying)
    )


def test_a_project_that_really_never_ran_is_still_shown_as_never_run(
    tmp_path: Path,
) -> None:
    """反方向也要守：**「待命」不准被那條 else 吃掉。**

    一條每次都出現的警告等於沒有警告。真的沒跑過的專案要留在「待命」，
    不然「要你處理」那條泳道會塞滿他根本不用處理的東西，而**真正要他處理的
    那一個就藏在裡面**。
    """
    project = tmp_path / "never-ran"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    summary, items = ProjectDashboardService().build(
        [ProjectEntry(name="never-ran", path=str(project))]
    )

    assert items[0].status == "dash.state.idle", (
        "一個從來沒跑過的專案被畫成「要你處理」"
    )
    assert summary.idle_projects == 1


def test_the_status_the_else_produces_can_actually_be_painted(
    tmp_path: Path,
) -> None:
    """認不得的收場，畫面要畫得出來。

    `lane_for()` 沒有預設值，查不到就丟例外——所以那條 else 給的 key
    如果不在 `_LANE_BY_KEY` 上，卡片會炸開（首頁那一側接得住，但那張卡片
    會退成一個沒有泳道的東西）。**分類到一個畫不出來的格子，跟沒有分類
    只差在噪音大小。**
    """
    from app.ui.dashboard_web_panel import lane_for

    project = _project_whose_last_run_ended_as(tmp_path, "CANCELLED")
    _summary, items = ProjectDashboardService().build(
        [ProjectEntry(name="cancelled", path=str(project))]
    )

    lane, tone = lane_for(items[0].status)
    assert lane != "idle", (
        f"認不得的收場排在「閒置」那條泳道（{items[0].status}）——"
        "而那條泳道出廠是收起來的"
    )
    assert tone
