"""按下兩格中間那顆 ⊕，新的那一格要**落在那兩格中間**（2026-08-27）。

老闆打開 App 之後的原話：

> 那這樣子新增一個往哪裡新增？⋯⋯這個加號應該在格子邊邊，那我先按加號才可以
> 在這兩個 node 之間插入一個新的 node 才對。

引擎早就做得到（`Lineup.with_node(..., at=)` 真的 `slots.insert(index, fresh)`），
頁面也早就畫好了（每一道縫一顆 ⊕，`data-add="${at}"`）。**斷的是中間那一段**，
而它斷得特別安靜：

* `handle_action` 的 `add_slot` 分支寫的是 `self._add_slot()`——`arg` 整個被
  丟掉。使用者按第二道縫的 ⊕，多出一格，**只是出現在最後面**。他不會覺得
  是壞了，他會覺得自己按錯了那道縫。
* `_add_slot` 自己沒有 `at` 參數。
* 頁面那顆 ⊕ 用 `lu.supports_insert_at` 關著，而全 repo 沒有人推過那個旗標
  ——所以那一天一顆都畫不出來。

## 這一份守的四件事

1. **位置真的被用上**（不是加在最後）。
2. **`""` 與 `"0"` 分得開**。空字串是「加在最後」（右上角那顆），`"0"` 是
   「插在最前面」（第一道縫）。兩者在真假值底下長得一樣，而搞錯的結果是
   「插在最前面」靜靜地變成「加在最後」——畫面上都只是多一格。
3. **讀不出來的位置被擋下來，而且那句話到得了他面前**（走完
   `handle_action` → `reject()`，不是只在引擎裡丟例外，也不是安靜 `return`）。
4. **`supports_insert_at` 不是一個寫死的 True**：引擎哪天不再收位置，它要
   自己變 False，⊕ 就不會被畫出來。那顆按鈕存在的理由就是「不要畫一顆說謊
   的按鈕」，而一個寫死的 True 會在說謊的那天繼續說是。

畫出來對不對是 DOM 那一層的事（`AICC-視覺概念/verify_pages.py` 的
`INSERT_PROBE` 真的 render 一次再一顆一顆按過去）。這裡守 Python 這一半。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import Lineup  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402

# 頁面那一側有沒有真的讀 `lu.supports_insert_at`，守在
# `tests/test_lineup_page_is_told_the_rules.py` 的 `READS`——那張表就是為
# 「Python 推了、頁面自己推論」這一族開的，再抄一份到這裡會變成同一件事的
# 第二個答案。


def _panel(tmp_path: Path, monkeypatch) -> LineupWebPanel:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    panel = LineupWebPanel(project)
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    return panel


# --- ① 位置真的被用上 -------------------------------------------------------


def test_a_seat_asked_for_the_middle_lands_in_the_middle(
    tmp_path, monkeypatch
) -> None:
    """按第 1 道縫，新的那一格排在第 1 格——**不是排在最後**。

    走的是 `handle_action`，不是 `_add_slot`：斷掉的那一行就在
    `handle_action` 裡（`self._add_slot()`，`arg` 被丟掉），直接呼叫
    `_add_slot(at)` 的測試**在那一天是綠的**。
    """
    panel = _panel(tmp_path, monkeypatch)
    before = panel._lineup.roles
    assert before == ("author", "reviewer")

    panel.handle_action("add_slot", "1")

    roles = panel._lineup.roles
    assert len(roles) == 3, "沒有加進去"
    assert roles[0] == before[0] and roles[2] == before[1], (
        f"原本那兩格被動到了：{roles}"
    )
    assert roles[1] not in before, (
        f"新的那一格沒有落在第 1 格，整條線是 {roles}——"
        "位置在路上被丟掉了，而畫面上只看得出「多了一格」"
    )


def test_every_gap_is_a_different_place(tmp_path, monkeypatch) -> None:
    """N 格有 N+1 道縫，**每一道縫插出來的順序都不一樣**。

    只驗一道縫的話，「位置被丟掉」與「位置被夾成同一個值」兩種壞法都可能
    漏掉：`Lineup.with_node` 自己是夾的（`max(0, min(at, len))`），所以一個
    永遠送 0 的實作在「插在最前面」那一條上是綠的。
    """
    landed = []
    for gap in range(3):
        panel = _panel(tmp_path / f"gap{gap}", monkeypatch)
        before = panel._lineup.roles
        panel.handle_action("add_slot", str(gap))
        roles = panel._lineup.roles
        fresh = [role for role in roles if role not in before]
        assert len(fresh) == 1, f"第 {gap} 道縫加出來的不是一格：{roles}"
        landed.append(roles.index(fresh[0]))
    assert landed == [0, 1, 2], (
        f"三道縫插出來的位置是 {landed}，應該是 [0, 1, 2]——"
        "位置要嘛沒被用上，要嘛在路上被夾成同一個值"
    )


# --- ② 空字串與 "0" 分得開 --------------------------------------------------


def test_the_end_and_the_front_are_not_the_same_button(
    tmp_path, monkeypatch
) -> None:
    """`data-add=""` 加在最後，`data-add="0"` 插在最前面。

    這一條是這一份最容易被寫成綠燈的：`int(arg or 0)`、`if not arg` 都會把
    這兩件事變成同一件事，而它們在畫面上長得一模一樣（都多一格）。
    """
    panel = _panel(tmp_path, monkeypatch)
    original = panel._lineup.roles

    # 右上角那顆「＋ 加一格」：空字串。
    panel.handle_action("add_slot", "")
    appended = panel._lineup.roles
    assert appended[: len(original)] == original, "空字串不是「加在最後」"
    last = appended[-1]

    # 最前面那道縫的 ⊕：字串 "0"。
    panel.handle_action("add_slot", "0")
    inserted = panel._lineup.roles
    assert inserted[1:] == appended, "「0」把原本那幾格推動了不只一格"
    assert inserted[0] not in appended, (
        f"「0」沒有插在最前面，整條線是 {inserted}——"
        "它被讀成了空字串，也就是「加在最後」"
    )
    assert inserted[0] != last, "兩顆按鈕做了同一件事"


def test_the_parser_itself_tells_them_apart() -> None:
    """同一件事，問解析器一次。

    上面那一條走完整條路（會寫檔、會推狀態），這一條直接問判準本身——
    壞掉的時候它指得出是**哪一段**在說謊。
    """
    assert LineupWebPanel._insert_at("", 2) is None, "空字串要是「加在最後」"
    assert LineupWebPanel._insert_at("0", 2) == 0, "「0」要是「插在最前面」"
    assert LineupWebPanel._insert_at("2", 2) == 2, "最後那一道縫也是合法位置"


# --- ③ 讀不出來的位置：擋下來，而且講出來 -----------------------------------


@pytest.mark.parametrize(
    "bad,why",
    [
        ("9", "超過格數"),
        ("-1", "負數"),
        ("abc", "根本不是數字"),
        ("1.5", "小數"),
        ("0:codex", "被人手改成別的形狀"),
    ],
)
def test_a_position_it_cannot_read_is_refused_out_loud(
    tmp_path, monkeypatch, bad: str, why: str
) -> None:
    """不合法的位置**不准猜**，而且那句話要真的到得了使用者。

    引擎那一側是夾的（`-1` → 0、`99` → 最後），那對引擎是對的：它不能因為
    一個爛索引就爆掉。但夾在這一層是錯的——他按了第三道縫，東西插在最前面，
    畫面上沒有一個字說發生過這件事。

    安靜 `return` 也不行：**按了沒反應跟插好了長得一模一樣**，而他接下來會
    去找那一格、或者再按一次。
    """
    panel = _panel(tmp_path, monkeypatch)
    said: list[str] = []
    panel.action_failed.connect(said.append)
    before = panel._lineup.roles

    panel.handle_action("add_slot", bad)

    assert panel._lineup.roles == before, (
        f"「{bad}」（{why}）被猜成了某一個位置，陣容被動到了"
    )
    assert said, (
        f"「{bad}」（{why}）被擋下來了，但一個字都沒說——"
        "他按了一顆加號，什麼都沒發生，而畫面看起來完全正常"
    )
    assert bad.strip() in said[0] or str(len(before)) in said[0], (
        f"擋下來的那句話沒有指出是什麼值或這條線有幾格：{said[0]}"
    )


def test_the_refusal_is_not_an_exception_out_of_the_bridge(
    tmp_path, monkeypatch
) -> None:
    """擋下來的路是 `reject()`，不是讓例外從頁面橋掉出去。

    掉出去的話使用者拿到的不是一句話，是一個 traceback——而那個例外**擋對
    了**，只是沒有人把它講出來。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("add_slot", "99")  # 不准丟例外
    assert len(panel._lineup) == 2


# --- ④ 旗標：推得出去、有人讀、而且不是寫死的 -------------------------------


def test_the_page_is_told_this_path_works(tmp_path, monkeypatch) -> None:
    panel = _panel(tmp_path, monkeypatch)

    assert panel.collect_state()["lineup"]["supports_insert_at"] is True, (
        "Python 沒有推 `supports_insert_at`——頁面那顆 ⊕ 一顆都不會出現，"
        "而它今天真的插得進去"
    )


def test_the_flag_goes_dark_when_the_engine_stops_taking_a_position(
    tmp_path, monkeypatch
) -> None:
    """**這一條就是「不准寫死 True」。**

    把引擎改成收下 `at` 之後照樣接在最後（那正是 2026-08-27 之前整條路的
    行為），旗標要自己變成 False——⊕ 不會被畫出來，使用者退回右上角那顆
    「＋ 加一格」。功能少一半，但畫面上沒有一句謊話。

    寫死 `True` 的實作在這裡是紅的，而在上面每一條裡都是綠的。
    """
    panel = _panel(tmp_path, monkeypatch)
    real = Lineup.with_node

    def ignores_at(self, role, provider, duty="", at=None):
        return real(self, role, provider, duty)

    monkeypatch.setattr(Lineup, "with_node", ignores_at)

    assert panel.collect_state()["lineup"]["supports_insert_at"] is False, (
        "引擎已經不把位置放在心上了，而畫面還被告知「插得進去」——"
        "那顆 ⊕ 會照畫，按下去東西出現在最後面"
    )


