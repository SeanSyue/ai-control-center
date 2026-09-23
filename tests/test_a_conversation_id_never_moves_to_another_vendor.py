r"""套用一個模式，那一格**無聲換成 Codex，還帶著 Claude 的對話 id**。

## 改壞的樣子（實機量到的，2026-08-26）

`apply_preset()` 保留這個專案自己的對話綁定，用的是
`current.session_for_role(slot.role)`——**只問名字，不問廠牌**。模式把某一格
從 Claude 換成 Codex 的那一次，那一格就帶著上一個廠牌的對話 id 被寫回
`.aicc/sessions.json`：

    他排的：  複查者 / claude / claude-conversation-abc
    套用之後：複查者 / codex  / claude-conversation-abc   ← 一句話都沒有

換廠牌那顆下拉（`_set_slot_provider`）一直都問這一句（「對話 id 屬於廠牌」），
套用模式這一條沒問。這是同一句話的第二份答案，而**第二份答案的綠燈跟對的
綠燈長得一模一樣**。

## 使用者會看到什麼

看不出來。卡片只看 `session_id` 是不是空的來決定畫不畫成「已指定對話」，
所以那一格看起來排好了；標題那一欄是空的（掃描結果裡沒有這個 id，因為它是
另一家的），而空標題跟「這段對話還沒取名字」長得一樣。上方那句「有 N 格還沒
指定對話，這條線還不能執行」不會出現，執行列那顆按鈕照樣亮著。

他按下開始執行——Codex 續接一個它從來沒有見過的對話 id。**那一步是要花錢的。**

## 反方向也要守

修法不可以變成「套用模式一律清掉對話」。廠牌沒有換的那幾格，綁定是這個專案
自己的東西，清掉等於每套用一次模式他就要重新挑一次對話。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import (  # noqa: E402
    AgentSlot,
    Lineup,
    session_for_new_provider,
)
from app.services.lineup_presets import LineupPreset, apply_preset  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402

# 他自己取的名字。出廠名字會讓「掉了跟沒掉」長得一模一樣（`AgentSlot`
# 會照舊角色名回推職責），而這一份檔案要量的正是「有沒有掉」。
HIS_WRITER = "node1"
HIS_OTHER = "node2"

HIS_CHAT = "claude-conversation-abc"
PRESET = "我存的那一張"


def _panel(tmp_path: Path, monkeypatch) -> LineupWebPanel:
    """兩格：第一格做事（Claude），第二格核准（Codex，出廠的形狀）。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    panel = LineupWebPanel(project)
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    panel.handle_action("set_slot_role", f"0:{HIS_WRITER}")
    panel.handle_action("set_slot_duty", "0:work")
    panel.handle_action("set_slot_role", f"1:{HIS_OTHER}")
    panel.handle_action("set_slot_duty", "1:approve")
    assert [s.provider for s in panel._lineup] == ["claude", "codex"], "前提壞了"
    return panel


def _on_disk(panel: LineupWebPanel) -> list[dict]:
    path = panel._project / ".aicc" / "sessions.json"
    return json.loads(path.read_text(encoding="utf-8"))["lineup"]


def test_applying_a_mode_that_changes_the_vendor_leaves_the_conversation_behind(
    tmp_path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    走的是他真的按得到的那條路：存一張模式 → 把那一格改成 Claude 並綁一段
    Claude 的對話 → 再套用同一張模式（模式裡那一格是 Codex）。

    改壞的樣子：那一格變回 Codex，而 Claude 的對話 id 留在上面。
    """
    panel = _panel(tmp_path, monkeypatch)
    # 這張模式裡，第二格是 Codex。
    panel.handle_action("save_preset", PRESET)

    # 他改主意，把第二格交給 Claude，並且綁了一段 Claude 的對話。
    panel.handle_action("set_slot_provider", f"1:claude")
    panel.handle_action("set_slot_session", f"1:{HIS_CHAT}")
    assert list(panel._lineup)[1].session_id == HIS_CHAT, "前提壞了：沒綁上"

    # 再套用同一張模式——那一格會換回 Codex。
    panel.handle_action("apply_preset", PRESET)

    seat = list(panel._lineup)[1]
    assert seat.provider == "codex", "前提壞了：模式沒有把廠牌換回去"
    assert seat.session_id == "", (
        f"「{seat.role}」這一格換成 Codex 了，卻還帶著 Claude 的對話 id："
        f"{seat.session_id!r}。畫面會把它畫成已指定對話、執行列照樣亮著，"
        "而 Codex 續接不到一個它沒見過的 id——他要按下去才會知道"
    )
    # **硬碟也要看。**畫面上對、寫回去的那一份錯的話，下一次開這個專案就是
    # 壞的那一份，而他不會再套用一次模式。
    written = {row.get("role"): row.get("session_id") for row in _on_disk(panel)}
    assert written[HIS_OTHER] == "", f"畫面清掉了，硬碟上還留著：{written}"


def test_applying_a_mode_keeps_the_conversation_when_the_vendor_is_the_same(
    tmp_path, monkeypatch
) -> None:
    """反方向：廠牌沒換的那幾格，綁定要留著。

    改壞的樣子：修成「套用模式一律清掉對話」。使用者會看到的是——每按一次
    模式，每一格都要重新挑一次對話，而他只是想換個隊形。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("set_slot_session", f"0:{HIS_CHAT}")
    panel.handle_action("save_preset", PRESET)
    assert list(panel._lineup)[0].session_id == HIS_CHAT, "前提壞了"

    panel.handle_action("apply_preset", PRESET)

    seat = list(panel._lineup)[0]
    assert seat.provider == "claude", "前提壞了：這一格的廠牌不該換"
    assert seat.session_id == HIS_CHAT, (
        "廠牌沒有換，對話卻被清掉了——他每套用一次模式就要重挑一次對話"
    )


def test_the_vendor_dropdown_and_the_mode_button_answer_this_the_same_way(
    tmp_path, monkeypatch
) -> None:
    """兩個入口問的是同一支 `session_for_new_provider()`，不是兩份各自的答案。

    改壞的樣子：其中一個自己寫一次「廠牌一樣就留著」。兩份判準會各自演化，
    而它們對同一個輸入給不同答案的那一天，沒有任何東西會變紅——這正是
    2026-08-26 之前的狀況（下拉問了，套用模式沒問）。
    """
    # 核准那一格（唯讀，兩個廠牌都坐得），先交給 Claude 並綁一段對話。
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("set_slot_provider", "1:claude")
    panel.handle_action("set_slot_session", f"1:{HIS_CHAT}")
    assert list(panel._lineup)[1].session_id == HIS_CHAT, "前提壞了：沒綁上"

    # 入口一：換廠牌的下拉。
    panel.handle_action("set_slot_provider", "1:codex")
    by_dropdown = list(panel._lineup)[1]
    assert by_dropdown.provider == "codex", "前提壞了"

    # 入口二：套用一張把同一格排成 Codex 的模式。
    seated = AgentSlot(HIS_OTHER, "claude", session_id=HIS_CHAT, duty="approve")
    by_preset = apply_preset(
        LineupPreset(
            PRESET, "", Lineup.of([AgentSlot(HIS_OTHER, "codex", duty="approve")])
        ),
        Lineup.of([seated]),
    )

    assert by_dropdown.session_id == by_preset.session_for_role(HIS_OTHER) == "", (
        "換廠牌的下拉與套用模式對同一件事給了不同答案："
        f"下拉 {by_dropdown.session_id!r}、模式 "
        f"{by_preset.session_for_role(HIS_OTHER)!r}"
    )


def test_a_seat_the_mode_adds_starts_with_no_conversation() -> None:
    """模式加出來的新位子本來就沒有人坐過，沒有東西可以留。

    這一條守的是 `None` 那一支：拿不到「換之前是誰」的時候要回空字串，
    不是去別的地方猜一個 id 出來。
    """
    assert session_for_new_provider(None, "claude") == ""

    applied = apply_preset(
        LineupPreset(PRESET, "", Lineup.of([AgentSlot("node9", "codex")])),
        Lineup.of([AgentSlot(HIS_WRITER, "claude", session_id=HIS_CHAT)]),
    )

    assert applied.session_for_role("node9") == ""
