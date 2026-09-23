"""首頁上**每一種收場都有人替它決定畫在哪**，不准有預設值。

## 這條在守什麼

2026-08-24 掃描抓到：`_LANE_BY_KEY` 那張表漏了「要你處理」，而
`lane_for()` 查不到就回 `("idle", "idle")`。於是 **16 種真的會發生、
而且需要他動手的收場，全部畫成灰色的「閒置」**——排在標著「閒置」的
那一段底下，跟「從來沒跑過」長得一模一樣。

那個預設值當時還有一段替自己辯護的註解：

> 查不到就落在 idle——**那是刻意的**：一個沒見過的狀態畫成灰色的「待命」，
> 比畫成綠色的「完成」或紅色的「錯誤」誠實。

**那句話是錯的**，而且它正好是被逐字否決過的形狀（`DECISIONS.md`：
「否決：把漏掉的收場補進畫面那條 elif 鏈——補完還是同一個形狀，
第十二種出現時再漏一次」）。掉進去的不是「沒見過的狀態」。

## 兩層，缺一不可

1. **`lane_for` 沒有預設值**——查不到丟例外。
2. **這裡把所有真的會發生的狀態餵一遍**，一種漏了就紅。

第 1 層自己不夠：沒有人餵它的話，它永遠不會被問到那一種。
第 2 層自己也不夠：有預設值的話，餵什麼進去都會回一個答案。

## 為什麼首頁還是要接住那個例外

首頁是使用者看見的第一頁。**一種新收場不該讓整頁掛掉**——所以
`collect_state()` 接住它、畫成最保守的那一種、並且記進 `_unmapped`。

跟原本那個預設值的差別是：**那時候沒有任何東西會講**。
"""

from __future__ import annotations

import pytest

from app.core import outcomes
from app.services.project_dashboard import ProjectDashboardService
from app.ui.dashboard_web_panel import _LANE_BY_KEY, UnmappedLane, lane_for

# `project_dashboard._build_item` 那條 if/elif 會產生的每一個狀態 key。
# **這一份是手寫的，而它下面那條測試會確認它沒有漏**——手寫的清單
# 不會自己跟著程式走，所以要有人問它。
STATUS_KEYS_THE_HOME_PAGE_CAN_PRODUCE = (
    "dash.state.idle",
    "run.status.running",
    "outcome.group.ask",
    "dash.state.done",
    "dash.state.error",
    "dash.needs_you",
    "dash.state.interrupted",
)


@pytest.mark.parametrize("key", STATUS_KEYS_THE_HOME_PAGE_CAN_PRODUCE)
def test_every_status_has_a_lane(key: str) -> None:
    """**這一條是這份檔案存在的理由。**"""
    lane, tone = lane_for(key)
    assert lane in {"gate", "run", "idle"}, f"{key} 落在一條不存在的泳道：{lane}"
    assert tone, f"{key} 沒有色調"


def test_needs_you_is_not_buried_in_the_idle_lane() -> None:
    """「要你處理」不准排在「閒置」底下。

    卡片上的字寫著「要你處理」，而它躺在標著「閒置」的那一段裡——
    **那兩件事同時出現在畫面上，使用者相信的是那一段的標題。**
    """
    lane, tone = lane_for("dash.needs_you")
    assert lane == "gate", f"「要你處理」排在 {lane} 那條泳道"
    assert tone != "gate", (
        "「要你處理」用了金色那一種色調——而金色會跳，"
        "跳動的意思是「AI 還在線上等你回一句話」。這一種沒有人在等。"
    )
    assert tone != "idle"


def test_an_unmapped_status_is_not_quietly_painted_idle() -> None:
    """沒有人替它決定畫法的狀態，**丟例外，不回一個答案**。"""
    with pytest.raises(UnmappedLane):
        lane_for("dash.state.something_nobody_decided_about")


def test_the_hand_written_list_did_not_drift() -> None:
    """上面那份手寫清單，要跟 `project_dashboard` 真的產得出來的對得上。

    這一條是那份清單的看門人。少了它，`_build_item` 多一個分支而這裡沒跟上，
    上面每一條都還是綠的——**而那正是這次要修的那個病**。
    """
    import ast
    import pathlib

    source = pathlib.Path(
        ProjectDashboardService.__module__.replace(".", "/") + ".py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    produced = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_build_item"):
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "status" for t in child.targets
                )
                and isinstance(child.value, ast.Constant)
            ):
                produced.add(child.value.value)
    missing = produced - set(STATUS_KEYS_THE_HOME_PAGE_CAN_PRODUCE)
    assert not missing, (
        f"`_build_item` 產得出這幾種而這份清單沒有：{sorted(missing)}。\n"
        "補進 `STATUS_KEYS_THE_HOME_PAGE_CAN_PRODUCE`，"
        "而補之前先想「它該畫在哪條泳道」。"
    )
    assert produced, "一個 status 賦值都沒抓到——這條檢查是恆真的"


def test_the_page_can_draw_every_tone_this_table_emits() -> None:
    """表上吐得出來的每一個色調，頁面那一側都要畫得出來。

    這一條擋的是「引擎給了一個新色調，而頁面沒有那條 CSS」——
    卡片上的標籤會變成沒有底色的裸字，而那**看起來像壞掉**。
    """
    import pathlib

    page = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "ui" / "web" / "dashboard_v5.html"
    ).read_text(encoding="utf-8-sig")
    missing = [
        tone
        for _key, (_lane, tone) in _LANE_BY_KEY
        if f".tag.{tone}" not in page
    ]
    assert not missing, (
        f"這幾個色調頁面畫不出來：{missing}。\n"
        "`AICC-視覺概念/dashboard_v5.html` 補一條 `.tag.<色調>`，"
        "再跑 `app/ui/web/build_pages.py`——**不要直接改產物**。"
    )


def test_the_lanes_the_table_uses_are_the_lanes_the_page_has() -> None:
    """泳道只有三條，表上不准冒出第四條。"""
    lanes = {lane for _key, (lane, _tone) in _LANE_BY_KEY}
    assert lanes <= {"gate", "run", "idle"}, f"表上有頁面沒有的泳道：{lanes}"


def test_the_ask_group_is_the_only_one_that_pulses() -> None:
    """會跳的只有「AI 還在線上等」那一組——`outcomes` 說了算。

    這一條把畫面那個決定綁回引擎的定義上。少了它，下一個人替
    「要你處理」挑金色的時候不會有東西講話。
    """
    assert outcomes.WAITING_GROUPS == frozenset({outcomes.GROUP_ASK}), (
        "會跳的那一組變了，而首頁的色調是照著它挑的——"
        f"現在是 {outcomes.WAITING_GROUPS}"
    )
