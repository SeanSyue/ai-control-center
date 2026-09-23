"""改陣容裡的一件事，不准順手弄丟另一件。

2026-08-22 同一個形狀在**四個地方**各發生一次：

| 哪裡 | 丟了什麼 | 症狀 |
|---|---|---|
| `workflow_worker._lineup_for_run` | `identity` 與 `duty` | 畫面上設好了，引擎收到的是預設值 |
| `Lineup.with_session` | `duty` | 指派一段對話，那一格就不再是「執行」了 |
| `Lineup.with_identity` | `duty` | 寫一句「你是⋯」，那一格就不能寫檔案了 |
| `LineupPreset.to_dict` | `duty` | 存好的表格套用之後**沒有任何一格能寫檔案** |

每一次的寫法都一樣：**逐個位置參數重新建一個 `AgentSlot`**。加欄位的時候
只有宣告那裡會被改到，這些地方一個字都不用動就繼續編譯、繼續通過測試——
而它們每一次都少送一格。

**症狀也每一次都一樣**：執行照跑，不報錯，只是跑起來的是別人。

所以這裡不再靠「記得把新欄位加進去」。這一支對每一個「回傳一份改過的陣容」
的方法，**逐欄比對**：改掉的那一格只准變它該變的，其餘每一格每一欄都要一樣。
加新欄位的時候，這條會自己涵蓋它——因為它比的是 `dataclasses.fields()`，
不是一份手寫的清單。
"""
from __future__ import annotations

from dataclasses import fields

import pytest

from app.core.lineup import (
    DUTY_APPROVE,
    DUTY_REVIEW,
    DUTY_WORK,
    WRITES_OFF,
    AgentSlot,
    Lineup,
)


def loaded() -> Lineup:
    """每一欄都填了不一樣的值的陣容。

    留白的欄位測不出「它有沒有被保留」——空字串被丟掉之後還是空字串。
    """
    return Lineup.of(
        [
            AgentSlot(
                "執行",
                "claude",
                session_id="chat-work",
                identity="你是熟悉境外開戶法規的資深顧問。",
                duty=DUTY_WORK,
                # **刻意不是出廠值。**空字串（＝照職責決定）在這一格算出來
                # 的答案是「寫得了」，跟這一格根本沒有這個開關長得一模一樣
                # ——掉了跟沒掉分不出來。填 `off` 才分得出來：這一格是
                # 他親手關掉的，而唯一會出事的方向正是它變回開的。
                writes_files=WRITES_OFF,
            ),
            AgentSlot(
                "複查",
                "codex",
                session_id="chat-review",
                identity="你是合規審查員。",
                duty=DUTY_REVIEW,
                # **刻意明著寫，而且刻意寫的就是「前一格」。**
                #
                # 空的 `parent` 意思是「接前面那一格」，所以在這一格上它算
                # 出來的線跟明著寫的一模一樣——**掉了跟沒掉分不出來**，
                # 而這正是這個檔案開頭那張表上每一筆的形狀。
                #
                # 明著寫下來之後，`_diff()` 比的是欄位本身（`fields()`），
                # 掉了就是掉了。而值選「前一格」是為了不動這一支既有的
                # 每一條斷言：搬位置那一條把「核准」搬到最前面，這條線在
                # 搬完之後照樣成立（`執行` 還是排在 `複查` 前面）。
                parent="執行",
            ),
            AgentSlot(
                "核准",
                "claude",
                session_id="chat-approve",
                identity="你負責看章齊不齊。",
                duty=DUTY_APPROVE,
            ),
        ]
    )


def _diff(before: AgentSlot, after: AgentSlot) -> set[str]:
    """兩格之間有哪幾欄不一樣。用 `fields()` 而不是手寫清單。"""
    return {
        field.name
        for field in fields(AgentSlot)
        if getattr(before, field.name) != getattr(after, field.name)
    }


@pytest.mark.parametrize(
    "change, touched, expected",
    [
        # 刻意動「執行」那一格，不動「複查」。
        #
        # `duty` 沒填的時候會從名字推，而使用者自己取的中文名推不出來，會落到
        # **安全的那一邊：複查**。所以拿一個本來就是複查的格子去測，掉了跟沒掉
        # 長得一模一樣——這條測試會安靜地什麼都沒測到。
        (
            lambda lineup: lineup.with_session("執行", "chat-新的"),
            "執行",
            {"session_id"},
        ),
        (
            lambda lineup: lineup.with_identity("執行", "你是新的工程師。"),
            "執行",
            {"identity"},
        ),
    ],
)
def test_changing_one_thing_changes_only_that_thing(
    change, touched: str, expected: set[str]
) -> None:
    before = loaded()
    after = change(before)

    assert after.roles == before.roles, "順序或名字被動到了"

    for role in before.roles:
        old = before.for_role(role)
        new = after.for_role(role)
        assert old is not None and new is not None
        changed = _diff(old, new)
        if role == touched:
            assert changed == expected, (
                f"【{role}】除了 {expected} 之外還被改了：{changed - expected}"
            )
        else:
            assert changed == set(), (
                f"沒有被指名的【{role}】也被改了：{changed}"
            )


def test_clearing_the_sessions_clears_only_the_sessions() -> None:
    """清掉對話 id 是搬機器時的動作，其餘一個欄位都不准動。

    以前這裡會順手把 `duty` 清掉——於是搬過機器之後，那個專案的執行那一格
    變成不能寫檔案的，而使用者只知道「搬完就做不出東西了」。
    """
    before = loaded()
    after = before.without_sessions()

    for role in before.roles:
        old = before.for_role(role)
        new = after.for_role(role)
        assert old is not None and new is not None
        assert new.session_id == ""
        assert _diff(old, new) == {"session_id"}, (
            f"【{role}】除了對話 id 之外還掉了：{_diff(old, new) - {'session_id'}}"
        )


def test_moving_a_seat_keeps_everything_it_had() -> None:
    """搬位置只改順序。"""
    before = loaded()
    after = before.moved("核准", 0)

    assert after.roles == ("核准", "執行", "複查")
    for role in before.roles:
        old = before.for_role(role)
        new = after.for_role(role)
        assert old is not None and new is not None
        assert _diff(old, new) == set(), f"搬個位置，【{role}】掉了 {_diff(old, new)}"


def test_a_saved_table_comes_back_with_every_field_it_had(tmp_path) -> None:
    """存成表格再讀回來，要是同一批格子。

    掉的那一欄是 `duty`，而它掉了之後會從名字重推——使用者自己取的中文名推
    不出來，會落到安全的那一邊（複查）。於是他存好的表格套用之後**沒有任何
    一格能寫檔案**，而畫面上完全看不出為什麼。
    """
    from app.services.lineup_presets import LineupPreset

    original = LineupPreset(
        name="開戶案專用", description="", lineup=loaded()
    )
    restored = LineupPreset.from_dict(original.to_dict())

    assert restored is not None
    assert restored.lineup.roles == original.lineup.roles
    for role in original.lineup.roles:
        old = original.lineup.for_role(role)
        new = restored.lineup.for_role(role)
        assert old is not None and new is not None
        # 對話 id 是刻意不存的（換專案之後那個 id 沒有意義），其餘都要回來。
        assert _diff(old, new) <= {"session_id"}, (
            f"【{role}】存了再讀回來掉了：{_diff(old, new) - {'session_id'}}"
        )
