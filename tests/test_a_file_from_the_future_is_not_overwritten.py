"""看不懂的檔案不准蓋。

老闆 2026-08-23 拍板：**「陣容存進檔案的格式現在要決定」**，含版本號。

定案的形狀是**已經在跑的那一個**（`.aicc/sessions.json` 的 `lineup` 陣列、
`lineup-presets.json` 的 `presets` 陣列），因為它已經長出來了——沒有人拍板
說它就是那個格式而已。這一份把「版本號」從一個寫出去沒有人讀的數字，
變成一條**會擋下動作**的規矩。

## 為什麼是「不准蓋」而不是「盡量讀讀看」

2026-08-23 之前 `VERSION = 1` 存在、也寫進了檔案，而**沒有任何一行讀它**。
所以一份由新版 AICC 寫出來的 `sessions.json`，被舊版打開之後會被
`load()` 正規化成它認得的形狀，然後在下一次存檔原地蓋掉。

> 使用者失去的是他排好的陣容，**而畫面上看起來一切正常。**

三種「體貼」的做法都被否決：

* **自動轉檔**——這一版不知道新版多了什麼，轉出來的東西是猜的。
* **盡量讀讀看**——讀得懂的留下、讀不懂的丟掉，就是無聲的資料損失。
* **回預設值繼續**——那是把他排好的陣容換成出廠的兩格，而且不講。

留下來的只有一條：**停下來，什麼都不動，說清楚。**
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.lineup import AgentSlot, Lineup
from app.services.lineup_presets import (
    FORMAT_VERSION,
    LineupPresetStore,
    PresetFileTooNew,
)
from app.services.session_registry import SessionFileTooNew, SessionRegistry


def _lineup() -> Lineup:
    return Lineup.of(
        [AgentSlot("實作", "claude"), AgentSlot("複查", "codex")]
    )


def test_the_version_actually_reaches_the_file(tmp_path: Path) -> None:
    """先確認它真的寫得出去。

    寫不出去的話，底下每一條都會「全部通過」——一條讀不到東西的檢查，
    讀起來跟一條全過的檢查一模一樣。
    """
    registry = SessionRegistry(tmp_path)
    registry.save_lineup(_lineup())

    stored = json.loads(registry.path.read_text(encoding="utf-8"))
    assert stored["version"] == SessionRegistry.VERSION
    assert registry.stored_version() == SessionRegistry.VERSION


def test_a_newer_sessions_file_is_not_overwritten(tmp_path: Path) -> None:
    """**這一條是這份檔案存在的理由。**"""
    registry = SessionRegistry(tmp_path)
    registry.save_lineup(_lineup())

    from_the_future = {
        "version": SessionRegistry.VERSION + 1,
        "lineup": [
            {
                "role": "發想",
                "provider": "codex",
                "duty": "plan",
                "something_this_build_has_never_heard_of": "要留著",
            }
        ],
    }
    registry.path.write_text(
        json.dumps(from_the_future, ensure_ascii=False), encoding="utf-8"
    )

    assert registry.written_by_a_newer_build()
    with pytest.raises(SessionFileTooNew):
        registry.save_lineup(_lineup())

    # 檔案一個位元都沒有動。
    assert json.loads(registry.path.read_text(encoding="utf-8")) == from_the_future


def test_the_refusal_says_which_version_and_what_to_do(tmp_path: Path) -> None:
    """「存不了」對使用者沒有用。要講出是第幾版、以及他能做什麼。"""
    registry = SessionRegistry(tmp_path)
    registry.path.write_text(
        json.dumps({"version": 99, "lineup": []}), encoding="utf-8"
    )

    with pytest.raises(SessionFileTooNew) as raised:
        registry.save_lineup(_lineup())

    said = str(raised.value)
    assert "99" in said
    assert str(SessionRegistry.VERSION) in said
    assert "更新" in said


def test_an_old_file_without_a_version_still_saves(tmp_path: Path) -> None:
    """反面：舊檔案（沒有版本號）照樣存得下去。

    沒有這一條，上面那條可以靠「什麼都不准存」通過——而那樣每一個既有專案
    都會在升級的當下變成唯讀。
    """
    registry = SessionRegistry(tmp_path)
    registry.path.write_text(
        json.dumps({"selected": {"claude": "abc", "codex": "def"}}),
        encoding="utf-8",
    )

    assert registry.stored_version() == 0
    assert not registry.written_by_a_newer_build()
    registry.save_lineup(_lineup())
    assert [slot.role for slot in registry.load_lineup()] == ["實作", "複查"]


def test_a_newer_preset_file_is_not_overwritten(tmp_path: Path) -> None:
    store = LineupPresetStore(tmp_path)
    store.save("我的", _lineup())

    from_the_future = {"version": FORMAT_VERSION + 1, "presets": []}
    store.path.write_text(
        json.dumps(from_the_future, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(PresetFileTooNew):
        store.save("另一個", _lineup())
    with pytest.raises(PresetFileTooNew):
        store.delete("我的")
    assert json.loads(store.path.read_text(encoding="utf-8")) == from_the_future


def test_the_two_files_agree_on_what_version_one_means() -> None:
    """一個陣容存在專案裡叫 sessions.json，存成模式叫 lineup-presets.json。

    **同一個東西的兩份檔案，版本號不准各走各的**——各走各的那一天，使用者
    會拿到一份存得進模式、卻讀不進專案的陣容。
    """
    assert FORMAT_VERSION == SessionRegistry.VERSION
