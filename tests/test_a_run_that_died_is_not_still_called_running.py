"""`run.json` 裡的 `RUNNING` 是**一句宣稱，不是一個事實**。

2026-08-27 量到的：這個 repo 自己的 `.aicc/runs/` 七筆紀錄裡有六筆卡在
`RUNNING`，最舊的一筆從 2026-08-21 卡起，六天。

為什麼會這樣：`create_run()` 在開跑那一毫秒寫下 `RUNNING`，而把它改掉的
只有 `finalize_run()`——**寫下它的那個行程死掉的時候，沒有任何人回來改它。**

首頁直接信它，於是：

* 使用者打開 App，他唯一那個專案寫著「執行中」，而它不是。
* 更糟的是**佇列永遠不排它**：`is_running` 為真 → `can_start=False`、
  `summary.can_start_more=False`。那個專案安靜地凍住，而畫面上一切正常。

修法**不是去改硬碟上那幾筆 `run.json`**（那是歷史紀錄，改它就是竄改證據），
是改**讀它的人怎麼解讀**：宣稱要有東西背書才算數，背書的那個東西是鎖。

## 為什麼是鎖

鎖是這個專案**本來就用來擋重複啟動**的東西：`acquire_lock()` 建立、
`release_lock()` 刪掉，而且它裡面寫著是哪一次執行建立的。所以「還在不在
跑」這個問題，鎖答得出來而 `run.json` 答不出來。

而且往保守那一邊倒**不會造成兩個流程同時跑**：擋住那件事的本來就是鎖，
不是這個判斷——`acquire_lock()` 看到鎖還在就直接回 `False`。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.project_dashboard import (
    ProjectDashboardService,
    ProjectDashboardItem,
)
from app.services.project_registry import ProjectEntry, ProjectRegistry
from app.services.timeline_store import TimelineStore


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "demo"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    return project


def _item(project: Path) -> ProjectDashboardItem:
    _summary, items = ProjectDashboardService().build(
        [ProjectEntry(name="demo", path=str(project))]
    )
    return items[0]


def _build(project: Path):
    return ProjectDashboardService().build(
        [ProjectEntry(name="demo", path=str(project))]
    )


def test_a_run_nobody_holds_the_lock_for_is_not_called_running(
    tmp_path: Path,
) -> None:
    """**這一條是這份檔案存在的理由。**

    硬碟上那六筆的形狀：`run.json` 說 `RUNNING`，而沒有任何鎖。
    """
    project = _project(tmp_path)
    TimelineStore(project).create_run("六天前那一次")

    item = _item(project)

    assert item.status != "run.status.running", (
        "沒有任何東西握著鎖，而首頁還是說它「執行中」——"
        "這正是那六筆躺了六天的形狀。"
    )
    assert item.status == "dash.state.interrupted"
    assert item.is_running is False


def test_the_queue_can_reach_a_project_whose_last_run_died(
    tmp_path: Path,
) -> None:
    """**凍住的那一半。**

    「說它在跑」不只是一句錯話，它會讓排程器永遠跳過這個專案。
    """
    project = _project(tmp_path)
    TimelineStore(project).create_run("六天前那一次")
    (project / ".aicc" / "task.md").write_text("做點事\n", encoding="utf-8")

    summary, items = _build(project)

    assert items[0].can_start is True, (
        "這個專案有 task.md、沒有人握著鎖、沒有在佇列裡，"
        "而首頁說它不能啟動——它會一直不能啟動。"
    )
    assert summary.can_start_more is True
    assert summary.running_projects == 0


def test_a_run_that_really_holds_the_lock_is_still_called_running(
    tmp_path: Path,
) -> None:
    """反面：真的在跑的那一種**不准被降級**。

    沒有這一條，把 `is_really_running()` 寫成 `return False` 也是綠的——
    而那會讓每一個正在跑的專案都被畫成中斷。
    """
    project = _project(tmp_path)
    run_id = TimelineStore(project).create_run("現在正在跑")
    assert ProjectDashboardService.acquire_lock(project, run_id) is True

    item = _item(project)

    assert item.status == "run.status.running"
    assert item.is_running is True
    assert item.interrupted_reason == ""


def test_a_lock_held_by_a_different_run_does_not_vouch_for_this_one(
    tmp_path: Path,
) -> None:
    """鎖在，但上面寫的是**別的那一次**。

    「有鎖」不等於「這一次還在跑」——那是**含有那個名字不等於是那個名字**
    的同一種錯。
    """
    project = _project(tmp_path)
    TimelineStore(project).create_run("這一次")
    assert ProjectDashboardService.acquire_lock(project, "另外那一次") is True

    item = _item(project)

    assert item.status == "dash.state.interrupted"
    assert item.is_running is False


def test_a_stale_lock_does_not_vouch_for_a_running_claim(
    tmp_path: Path,
) -> None:
    """鎖過期了就不算背書。

    用的是這個專案**本來就有**的那條定義（`is_stale_lock`），這裡不另外訂
    一個時間門檻——兩套門檻會分岔，而分岔之後畫面說的跟排程器做的不是同
    一件事。
    """
    project = _project(tmp_path)
    run_id = TimelineStore(project).create_run("很久以前開始的那一次")
    lock_path = ProjectDashboardService.lock_path(project)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    long_ago = datetime.now(timezone.utc) - timedelta(
        minutes=ProjectDashboardService.STALE_LOCK_MINUTES + 60
    )
    lock_path.write_text(
        json.dumps({"run_id": run_id, "pid": 1, "created_at": long_ago.isoformat()}),
        encoding="utf-8",
    )

    assert ProjectDashboardService.is_stale_lock(project) is True
    item = _item(project)

    assert item.status == "dash.state.interrupted"
    assert item.is_running is False


def test_a_lock_we_cannot_read_does_not_vouch_for_anything(
    tmp_path: Path,
) -> None:
    """讀不出來的鎖，**不准算成「就是你要問的那一次」**。

    「不知道是誰握著」倒向「是你」的話，一個壞掉的鎖檔就足以讓那個專案
    永遠凍在「執行中」。
    """
    project = _project(tmp_path)
    TimelineStore(project).create_run("這一次")
    lock_path = ProjectDashboardService.lock_path(project)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("{ 這不是 JSON", encoding="utf-8")

    assert ProjectDashboardService.lock_holder(project) == ""
    assert _item(project).status == "dash.state.interrupted"


def test_a_finished_run_is_untouched_by_any_of_this(tmp_path: Path) -> None:
    """收場正常的那幾種一個位元組都不准變。

    這一條擋的是「為了修 `RUNNING` 而把整條 elif 鏈弄歪」。
    """
    project = _project(tmp_path)
    timeline = TimelineStore(project)
    done = timeline.create_run("做完了")
    timeline.finalize_run(
        done,
        {"workflow_status": "COMPLETED", "review_status": "PASS", "fix_attempts": 0},
    )

    item = _item(project)

    assert item.status == "dash.state.done"
    assert item.interrupted_reason == ""


# ---------------------------------------------------------------------------
# 「停在哪」——中斷分兩種，**因為下一步不一樣**
# ---------------------------------------------------------------------------


def test_a_run_that_never_produced_anything_says_so(tmp_path: Path) -> None:
    """8/26 那兩筆的形狀：一份痕跡都沒留下。

    對這一種，「重跑」是安全的答案——沒有東西會被丟掉。
    """
    project = _project(tmp_path)
    run_id = TimelineStore(project).create_run("開了頭就死了")
    run_timeline = project / ".aicc" / "runs" / run_id / "timeline.jsonl"
    assert not run_timeline.exists()

    item = _item(project)

    assert item.status == "dash.state.interrupted"
    assert item.last_run_id == run_id
    assert (
        item.interrupted_reason
        == ProjectDashboardService.INTERRUPTED_NEVER_STARTED
    )


def test_a_run_that_left_a_timeline_behind_says_so(tmp_path: Path) -> None:
    """8/25 那兩筆的形狀：有 `timeline.jsonl`。

    對這一種，直接叫人重跑是錯的——那個資料夾裡有東西可以看。
    """
    project = _project(tmp_path)
    store = TimelineStore(project)
    run_id = store.create_run("跑到一半停住")
    store.append(run_id, "claude", "message", "我開始做了")

    item = _item(project)

    assert item.status == "dash.state.interrupted"
    assert (
        item.interrupted_reason
        == ProjectDashboardService.INTERRUPTED_STOPPED_MIDWAY
    )


def test_a_run_with_only_a_step_log_says_it_got_somewhere(tmp_path: Path) -> None:
    """有 `steps.jsonl` 沒有 `timeline.jsonl` 的那一種。"""
    project = _project(tmp_path)
    run_id = TimelineStore(project).create_run("跑到一半停住")
    (project / ".aicc" / "runs" / run_id / "steps.jsonl").write_text(
        '{"role": "author"}\n', encoding="utf-8"
    )

    item = _item(project)

    assert (
        item.interrupted_reason
        == ProjectDashboardService.INTERRUPTED_STOPPED_MIDWAY
    )


def test_being_updated_a_millisecond_later_is_not_progress(tmp_path: Path) -> None:
    """**`created_at` 跟 `updated_at` 差多少不能當證據。**

    2026-08-28 拿硬碟上的資料量到的：8/26 那兩筆什麼都沒跑，而 `updated_at`
    比 `created_at` 晚 1.5 毫秒。也就是說「被更新過」對「有沒有跑過」
    完全沒有分辨力——用它就是在猜，而猜錯的時候畫面會很有自信地叫人
    去翻一個空資料夾。
    """
    project = _project(tmp_path)
    store = TimelineStore(project)
    run_id = store.create_run("開了頭就死了")
    run_json = project / ".aicc" / "runs" / run_id / "run.json"
    payload = json.loads(run_json.read_text(encoding="utf-8"))
    payload["created_at"] = "2026-08-26T02:34:49.825994+08:00"
    payload["updated_at"] = "2026-08-26T02:34:49.827484+08:00"
    run_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    item = _item(project)

    assert (
        item.interrupted_reason
        == ProjectDashboardService.INTERRUPTED_NEVER_STARTED
    ), (
        "這一筆一個步驟都沒跑過（沒有 timeline.jsonl、沒有 steps.jsonl），"
        "卻因為晚了 1.5 毫秒被更新過就被說成「跑到一半」——"
        "那會叫他去翻一個空資料夾。"
    )


# ---------------------------------------------------------------------------
# 畫面上真的看得出差別嗎
# ---------------------------------------------------------------------------


def test_the_card_does_not_look_like_a_running_one(tmp_path: Path) -> None:
    """**真的在跑**跟**上次跑到一半死掉**，畫面上不准長得一樣。

    這一條測的是送去頁面的那份 payload，不是內部欄位——內部分得清楚、
    送出去的長一樣，使用者看到的還是同一張卡。
    """
    from app.ui.dashboard_web_panel import lane_for
    from app.i18n import t

    running_lane = lane_for("run.status.running")
    interrupted_lane = lane_for("dash.state.interrupted")

    assert interrupted_lane != running_lane, (
        "中斷的跟執行中的畫在同一條泳道、同一個色調——"
        "**那就是這次要修的那個病本身。**"
    )
    assert interrupted_lane[0] != "run", (
        "`run` 那條泳道的卡片會跳，而跳動的意思是「現在正在動」。它沒有在動。"
    )
    assert t("dash.state.interrupted") != t("run.status.running")
    assert t("dash.state.interrupted") != t("dash.state.idle")


def test_the_codex_column_does_not_call_an_interrupted_run_idle() -> None:
    """卡片正面那一欄不准跟狀態字打架。

    「中斷未完成」旁邊寫著「待命」，**兩句話同時在同一張卡上**——
    而使用者相信的是哪一句沒有人說得準。
    """
    from app.i18n import t
    from app.ui.dashboard_web_panel import DashboardWebPanel

    item = ProjectDashboardItem(
        name="demo",
        path="C:/nowhere",
        status="dash.state.interrupted",
        last_task="上一次那件事",
        last_updated="",
        waiting_count=0,
        completed_today=0,
        failed_today=0,
        is_running=False,
        can_start=True,
        interrupted_reason=ProjectDashboardService.INTERRUPTED_NEVER_STARTED,
    )

    text = DashboardWebPanel._codex_text(item, paired=True)

    assert text != t("dash.state.idle"), (
        "中斷的專案在那一欄寫「待命」——那正是這次要修的形狀："
        "上次跑到一半死掉，跟什麼事都沒有，長得一樣。"
    )
    assert text == t("dash.state.interrupted")


def test_the_card_says_what_to_do_next(tmp_path: Path) -> None:
    """卡片背面要**回答下一步**，不是只丟一個狀態字。

    「中斷未完成」自己不回答「所以我現在該做什麼」，而該不該去翻那個
    資料夾這件事，機器已經知道了。
    """
    from app.i18n import STRINGS
    from app.ui.dashboard_web_panel import _INTERRUPTED_TEXT_KEYS

    reasons = {
        ProjectDashboardService.INTERRUPTED_NEVER_STARTED,
        ProjectDashboardService.INTERRUPTED_STOPPED_MIDWAY,
    }
    assert set(_INTERRUPTED_TEXT_KEYS) == reasons, (
        "引擎吐得出一種中斷，而畫面沒有那一句——那一列會整列不見，"
        f"而卡片上只剩一個狀態字。缺的是：{sorted(reasons - set(_INTERRUPTED_TEXT_KEYS))}"
    )
    for key in _INTERRUPTED_TEXT_KEYS.values():
        assert key in STRINGS, f"{key} 不在 app/i18n.py 裡，畫到那一列會當場炸。"
    assert "dash.interrupted_at" in STRINGS
    texts = {STRINGS[key] for key in _INTERRUPTED_TEXT_KEYS.values()}
    assert len(texts) == len(_INTERRUPTED_TEXT_KEYS), (
        "兩種中斷寫著同一句話——那等於沒有分。"
    )
