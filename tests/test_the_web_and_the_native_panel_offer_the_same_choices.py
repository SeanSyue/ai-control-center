"""網頁那一段跟原生面板，提供的必須是**同一組選項**。

2026-08-27 老闆的原話（他打開 App 之後）：

> 把選 claude 和 codex 的地方和 **AI 執行設定的地方**，像是網站一樣和工作線
> 放在一個頁面，然後滾輪往下就可以往下看到其他設定之類的呢

搬家的時候最容易出的事，不是搬壞，是**搬出第二份**。

`app/services/run_settings_options.py` 宣告了網頁那一段要畫的選項，
而原生面板今天仍然自己 `addItem()`。**兩邊漂開的話，畫面會提供一個引擎
不認得的值**——他選了它，送回來落進 `set_run_setting` 的 `else`，
然後什麼都沒發生，而畫面上那一格看起來已經改好了。

> 這一頁已經為「畫面自己拼一份事實」壞過兩次（座位上限、`gate_floor`）。
> 這一條是為了不要有第三次。

## 它怎麼比

**真的把面板開起來**，把每一個下拉的 `(itemData, itemText)` 倒出來——
不是讀原始碼、不是比字串。讀原始碼的話，`addItem` 換一種寫法它就瞎了。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QLabel,
    QSpinBox,
)

from app.i18n import t  # noqa: E402
from app.services import run_settings_options as opts  # noqa: E402
from app.ui.ai_settings_panel import AISettingsPanel  # noqa: E402

# 面板上那個屬性名 → 這份宣告裡的鍵。
# 兩邊命名不一樣是既有的事實（面板用控制項的名字，宣告用 settings 的欄位名），
# 所以對照寫在這裡一份，而不是靠猜。
COMBOS = {
    "workflow_combo": "workflow_mode",
    "claude_model_combo": "claude_model",
    "claude_effort_combo": "claude_effort",
    "claude_speed_combo": "claude_speed",
    "claude_permission_combo": "claude_permission_mode",
    "claude_timeout_combo": "claude_timeout_minutes",
    "codex_model_combo": "codex_model",
    "codex_effort_combo": "codex_effort",
    "codex_speed_combo": "codex_speed",
    "output_length_combo": "claude_verbosity",
    "context_profile_combo": "context_profile",
}
SPINS = {
    "rounds_spin": "planning_rounds",
    "fix_spin": "max_fix_attempts",
    "claude_turns_spin": "claude_max_turns",
}
TOGGLES = {
    "force_new_checkbox": "force_new_sessions",
    "auto_bootstrap_checkbox": "auto_session_bootstrap",
    "auto_memory_checkbox": "auto_memory_maintenance",
}


@pytest.fixture
def panel():
    """**不要用 module 層級。**

    第一版寫的是 `scope="module"`，斷言全部正確，而行程收尾時以
    `0xC0000374` 死掉——負向驗證因此把它判成「測試根本沒跑起來」，
    儘管那一輪的 `F` 就在輸出裡。

    > **全過然後崩潰，跟全過然後乾淨離開，在離開碼上分不出來。**
    > （這一次分得出來了：離開碼 2026-08-26 起有意義。）

    活過整個 module 的 Qt widget 沒有人收，而這個 repo 的收尾走的是
    `app/ui/shutdown.py` ＋ `conftest.pytest_sessionfinish`——
    一個躺在 module 命名空間裡的面板不在那條路上。
    """
    QApplication.instance() or QApplication([])
    made = AISettingsPanel(Path(tempfile.mkdtemp()))
    yield made
    made.setParent(None)
    made.deleteLater()


def test_the_scan_finds_every_control(panel) -> None:
    """前提檢查：抓不到的話底下每一條都是恆真的。

    面板改寫（例如那 22 個控制項換一種建法）的時候這一條會先紅，
    而它紅的訊息說得出**是哪一個屬性不見了**。
    """
    for attr, kind in [
        *((a, QComboBox) for a in COMBOS),
        *((a, QSpinBox) for a in SPINS),
        *((a, QCheckBox) for a in TOGGLES),
    ]:
        w = getattr(panel, attr, None)
        assert isinstance(w, kind), (
            f"面板上找不到 {attr}（或它換成別的控制項了）——"
            "這一份對照表要跟著改，不是刪掉"
        )


@pytest.mark.parametrize("attr,key", sorted(COMBOS.items()))
def test_every_dropdown_offers_exactly_what_the_web_will_offer(
    panel, attr: str, key: str
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：有人在面板上多加一個模型，網頁那一段沒有——
    他在原生那邊選得到、搬到網頁之後選不到，而**沒有任何一個字說它不見了**。
    """
    combo = getattr(panel, attr)
    live = [(combo.itemData(i), combo.itemText(i)) for i in range(combo.count())]
    # **比翻出來的字**——宣告那一份存的是 i18n 的鍵。
    declared = opts.labelled(key)
    assert live == declared, (
        f"{attr} 跟 `run_settings_options.CHOICES[{key!r}]` 對不上：\n"
        f"  面板實際：{live}\n"
        f"  宣告的　：{declared}\n"
        "畫面自己有一份選項清單的那天，它會繼續提供一個引擎已經不認得的選擇。"
    )


@pytest.mark.parametrize("attr,key", sorted(SPINS.items()))
def test_every_number_box_has_the_same_range(panel, attr: str, key: str) -> None:
    """數字框的上下限也要一致。

    網頁那一段自己寫 `max="3"` 而引擎收得下 5 的話，
    他在原生那邊調得到的值，搬過去之後調不到。
    """
    spin = getattr(panel, attr)
    lo, hi, suffix_key = opts.SPINS[key]
    want = (lo, hi, t(suffix_key) if suffix_key else "")
    assert (spin.minimum(), spin.maximum(), spin.suffix()) == want, (
        f"{attr} 的範圍或後綴跟宣告對不上："
        f"面板 {(spin.minimum(), spin.maximum(), spin.suffix())}、"
        f"宣告 {want}"
    )


@pytest.mark.parametrize("attr,key", sorted(TOGGLES.items()))
def test_every_checkbox_keeps_its_whole_sentence(
    panel, attr: str, key: str
) -> None:
    """勾選框那一句**整句**都要在。

    括號裡那半句講的是**代價**（「會增加本次 token」「多用 Codex + Claude
    各一次」），而代價正是他決定勾不勾的依據。搬家的時候把它縮成
    「自動維護共同記憶」，他就看不到那件事要花什麼。
    """
    assert getattr(panel, attr).text() == t(opts.TOGGLES[key]), (
        f"{attr} 的說明跟宣告對不上：\n"
        f"  面板：{getattr(panel, attr).text()!r}\n"
        f"  宣告：{t(opts.TOGGLES[key])!r}"
    )


# --- 每一格有沒有自己的名字 -------------------------------------------------


def test_every_control_has_exactly_one_label_declared() -> None:
    """`LABELS` 跟真正的控制項清單不准漂開。

    畫面那一側只拿得到 `key`，而 `claude_model` 不是一句人話。
    少一筆 → 那一格在網頁上會印原始鍵名；多一筆 → 有人刪了控制項卻留著名字，
    下一個人會照著它去找一個不存在的東西。
    """
    declared = set(opts.LABELS)
    real = set(opts.CHOICES) | set(opts.SPINS)
    assert declared == real, (
        f"少了名字的：{sorted(real - declared)}；"
        f"名字還在但控制項不見了的：{sorted(declared - real)}"
    )


def test_every_label_key_actually_resolves() -> None:
    """`t()` 找不到的鍵會原樣回傳，而那讀起來就是畫面上印出 `ai.claude.model`。"""
    unresolved = [key for key, k in opts.LABELS.items() if t(k) == k]
    assert not unresolved, f"這幾個標籤的 i18n 鍵查不到字：{unresolved}"


def test_two_controls_never_share_a_name() -> None:
    """兩格同名 ＝ 他改了一個，另一個沒動，而畫面上看不出他改的是哪一個。"""
    seen: dict[str, list[str]] = {}
    for key in opts.LABELS:
        seen.setdefault(opts.label_for(key), []).append(key)
    clashes = {name: keys for name, keys in seen.items() if len(keys) > 1}
    assert not clashes, f"這幾組控制項在畫面上同名：{clashes}"


def test_every_control_says_what_it_is(panel) -> None:
    """**面板上每一格旁邊真的有它自己的名字。**

    ⛔ 這一條是 2026-09-01 補的，因為它抓到一個活著的錯：

        grid.addWidget(QLabel(t("ai.context_profile")), 6, 0)   # 上下文範圍
        ...
        grid.addWidget(QLabel(t("ai.max_fix_attempts")), 6, 0)  # ← 同一格

    後加的那個**蓋掉**前面那個。結果是「上下文範圍」那個下拉旁邊寫著
    「自動 FIX」，而真正的自動 FIX 那個數字框**一個標籤都沒有**。

    兩個 widget 都在 grid 上、兩個都畫得出來、`test_the_scan_finds_every_control`
    照樣綠——**它問的是「控制項在不在」，不是「它旁邊那句話對不對」**。

    所以這裡量的是版面：每一個控制項左邊那一格，要有一個 `QLabel`，
    而且上面的字要正好是 `label_for(key)`。

    勾選框不在裡面：它們的名字寫在自己身上（`QCheckBox(t(...))`），
    而那一整句括號裡講的是代價——那是他決定勾不勾的依據，不准縮寫。
    """
    grid = panel.findChild(QGridLayout)
    assert grid is not None, "找不到那個 QGridLayout —— 面板改寫法了，這條要跟著改"

    where: dict[int, tuple[int, int]] = {}
    labels: dict[tuple[int, int], str] = {}
    for i in range(grid.count()):
        item = grid.itemAt(i)
        widget = item.widget()
        if widget is None:
            continue
        row, col, _, _ = grid.getItemPosition(i)
        where[id(widget)] = (row, col)
        if isinstance(widget, QLabel):
            # 同一格被蓋過的話，這裡會留下**後來那一個**——正是使用者看到的。
            labels[(row, col)] = widget.text()

    missing = []
    for attr, key in sorted({**COMBOS, **SPINS}.items()):
        widget = getattr(panel, attr)
        pos = where.get(id(widget))
        if pos is None:
            missing.append(f"{attr}：不在那個 grid 上")
            continue
        row, col = pos
        want = opts.label_for(key)
        got = labels.get((row, col - 1))
        if got != want:
            missing.append(
                f"{attr}（{key}）左邊那一格寫的是 {got!r}，應該是 {want!r}"
            )
    assert not missing, (
        "這幾格在畫面上沒有自己的名字（或名字是別人的）：\n  "
        + "\n  ".join(missing)
    )
