"""「這一次要怎麼跑」那一段，畫面拿得到、也送得回來。

老闆 2026-08-27 打開 App 之後：

> 把選 claude 和 codex 的地方和 **AI 執行設定的地方**，像是網站一樣和工作線
> 放在一個頁面，然後滾輪往下就可以往下看到其他設定之類的呢

「選 claude 和 codex」那半頁面設計做完了；**這一半是引擎的**——
那 22 個控制項今天長在一個原生 Qt 面板裡，畫面碰不到。

## 這一支守的四件

1. 現值、可選項、那一行摘要**都推得到畫面**
2. 認不得的鍵或值**擋下來而且說得出話**（不准掉進 `else`）
3. 任務跟設定**分開送**（它們節奏差很多）
4. 展開狀態跟原生面板**同一個鍵**

## 它不守什麼

畫面那一段長什麼樣——那是頁面設計那一側，這一輪還沒送出去。
`tests/test_no_field_is_pushed_into_the_void.py` 的待接清單負責記著那筆債。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.i18n import t  # noqa: E402
from app.services import run_settings_options as opts  # noqa: E402
from app.services.ai_settings import AISettingsStore  # noqa: E402
from app.services.run_summary import summary_line  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402


@pytest.fixture
def panel():
    QApplication.instance() or QApplication([])
    p = LineupWebPanel(Path(tempfile.mkdtemp()))
    p._said: list[str] = []          # type: ignore[attr-defined]
    p.reject = p._said.append        # type: ignore[method-assign]
    return p


def _run(panel) -> dict:
    return panel.collect_state()["lineup"]["run_settings"]


def test_the_page_gets_the_values_and_the_choices(panel) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：只推現值不推可選項。畫面只好自己寫一份清單，
    而引擎哪天多一個模型，畫面會繼續提供舊的那幾個——
    這一頁已經為同一個形狀壞過兩次（座位上限、`gate_floor`）。
    """
    rs = _run(panel)
    by_key = {c["key"]: c for c in rs["controls"]}
    assert by_key, "一個控制項都沒推——畫面畫不出任何東西"

    for key in opts.CHOICES:
        c = by_key.get(key)
        assert c and c["kind"] == "choice", f"沒有推 {key} 這一格"
        assert c["options"], f"{key} 的可選項是空的"
        assert c["now"] is not None, f"{key} 沒有現值——畫不出「現在是什麼」"
    for key in opts.SPINS:
        c = by_key.get(key)
        assert c and c["kind"] == "number", f"沒有推 {key} 這一格"
        lo, hi, _suffix = opts.SPINS[key]
        assert (c["min"], c["max"]) == (lo, hi), f"{key} 的上下限對不上"
    for key, label_key in opts.TOGGLES.items():
        c = by_key.get(key)
        assert c and c["kind"] == "toggle", f"沒有推 {key} 這一格"
        # 宣告那一份存的是 i18n 的鍵，推出去的是**翻好的整句**。
        assert c["note"] == t(label_key), f"{key} 那一句沒有原樣推過去"

    # **每一筆的鍵要一樣。**那張守衛只取陣列第一筆當樣本，
    # 形狀不一致的話後面幾種獨有的欄位它一輩子看不到。
    shapes = {tuple(sorted(c)) for c in rs["controls"]}
    assert len(shapes) == 1, f"控制項的形狀不一致：{shapes}"


def test_the_summary_line_is_not_rebuilt_on_the_page(panel) -> None:
    """那一行「本次：⋯」**照原樣推過去**。

    畫面自己拼的話，那句話會在引擎多一個欄位的那天少講一件事，
    而**它看起來完全正常**。所以兩邊讀的必須是同一支
    （`app/services/run_summary.py`）。
    """
    rs = _run(panel)
    settings = AISettingsStore(panel._project).load_project()
    assert rs["summary"] == summary_line(settings), (
        "推出去的摘要跟 `summary_line()` 算的不一樣——有人在中間重拼了一次"
    )
    assert rs["summary"].startswith("本次："), rs["summary"][:40]


def test_the_task_is_pushed_apart_from_the_settings(panel) -> None:
    """任務跟設定**分開送**。

    合成一包的話，畫面那一側只能把它們放在一起——而那正是它們今天
    在原生面板裡的問題：**他每次都要用的東西，跟他偶爾調一次的東西，
    在同一個抽屜裡，而那個抽屜預設是關的。**

    檔名也要送：計畫序列寫 `planning-request.md`、開發序列寫 `task.md`，
    畫面上看不出現在會寫哪一個的話，他會以為自己改的是同一份。
    """
    lu = panel.collect_state()["lineup"]
    assert "task" in lu, "任務沒有自己一份——畫面只能把它跟設定放在一起"
    assert lu["task"]["file"] in ("task.md", "planning-request.md")
    assert "task" not in lu["run_settings"], "任務被塞進設定那一包了"


def test_a_choice_the_engine_does_not_know_is_refused_out_loud(panel) -> None:
    """認不得的值**擋下來而且說得出話**。

    改壞的樣子：掉進 `else` 靜靜 return——他改了一個東西，
    **然後什麼都沒發生**，而畫面上那一格看起來已經改好了。
    """
    panel.handle_action("set_run_setting", "claude_model:這個模型不存在")
    assert panel._said, "認不得的值被安靜吞掉了"
    assert "claude_model" in panel._said[-1], panel._said[-1]

    panel.handle_action("set_run_setting", "沒有這個鍵:1")
    assert "認不得" in panel._said[-1], panel._said[-1]


def test_a_number_outside_the_range_is_refused(panel) -> None:
    """數字框的上下限**引擎這一端也要守**。

    畫面自己寫 `max` 是給人看的，擋不住手改過的請求。
    夾住也不行——他調到 9，東西變成 2，而畫面上一個字都不會說。
    """
    panel.handle_action("set_run_setting", "max_fix_attempts:9")
    assert panel._said and "0～2" in panel._said[-1], panel._said
    now = {c["key"]: c["now"] for c in _run(panel)["controls"]}
    assert now["max_fix_attempts"] != 9


def test_an_empty_task_does_not_reach_the_disk(panel) -> None:
    """空的任務擋下來。

    空的任務送出去，AI 拿到的是一份沒有題目的考卷——
    而那一輪跑出來的東西沒有人驗得了。
    """
    panel.handle_action("set_task", "   ")
    assert panel._said, "空任務被寫進去了"
    assert not (panel._project / ".aicc" / "task.md").is_file()


def test_a_real_task_lands_on_disk(panel) -> None:
    """存下去的要真的在硬碟上。

    只改記憶體的話，他關掉 App 再開，那段字就沒了——
    而「存過了」跟「沒存過」在畫面上長得一模一樣。
    """
    panel.handle_action("set_task", "把陣容頁那條線畫出來")
    name = panel.collect_state()["lineup"]["task"]["file"]
    saved = (panel._project / ".aicc" / name).read_text(encoding="utf-8")
    assert saved.strip() == "把陣容頁那條線畫出來"


def test_the_expanded_state_shares_one_key_with_the_native_panel(panel) -> None:
    """展開狀態**跟原生面板同一個鍵**。

    分兩個鍵的話，他在原生那邊收起來、切到網頁又是開的——
    同一個設定的第二個答案，而兩個都「對」。
    """
    from app.services import ui_preferences

    panel.handle_action("toggle_run_settings", "1")
    assert _run(panel)["expanded"] is True
    assert ui_preferences.store().value(
        "ai_settings_panel/expanded", False, type=bool
    ) is True
    panel.handle_action("toggle_run_settings", "")
    assert _run(panel)["expanded"] is False
