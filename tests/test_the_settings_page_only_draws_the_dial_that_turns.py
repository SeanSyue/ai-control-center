"""設定頁只畫「這條序列真的會讀」的那個回合數。

2026-08-27 之前它無條件畫「規劃回合數」，選項 1/2/3。出廠序列同一天換成
開發序列之後，他打開設定頁看到的是：

- 一列**沒有任何選項被選中**的「規劃回合數」（出廠值 0 不在 1/2/3 裡），
  而且**這條序列從頭到尾不會讀它**；
- 而真正會生效的那一個（自動 FIX 幾次）**整列不存在**。

> 一個畫出來的欄位，本身就是一句「動我會有用」。

Qt 面板那邊 2026 年初就做對了（`ai_settings_panel.py:630`——`setEnabled(planning)`
兩行，一個開一個關）。**網頁這一份沒有跟上，而兩份讀的是同一個設定。**

## 為什麼不用 `rowIf` 的隱藏路

`rowIf` 查不到 key 就隱藏並記進 `MISSING`，而 `MISSING` 的意思是
**「Python 忘了推」**（頁面自己第 494 行的字：「這些 key 沒有從 Python
收到值」）。「這條序列不適用」跟「忘了推」是兩件事——混在一起等於在
一個現在真的有人在聽的主控台上（`_PageThatIsHeard`）報一個假警報。

所以 `workflow_mode` 沒推到才算 MISSING，那才是真的忘了推。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from app.services.ai_settings import AIExecutionSettings  # noqa: E402

BUILT = Path(__file__).resolve().parents[1] / "app" / "ui" / "web" / "settings_v1.html"
SOURCE = Path(__file__).resolve().parents[1] / "design-pages" / "settings_v1.html"
PANEL = Path(__file__).resolve().parents[1] / "app" / "ui" / "settings_web_panel.py"

THE_PICKER = "roundsRowForSequence"

DEFINITION = f"function {THE_PICKER}(){{"
THE_CALL = f"    {THE_PICKER}(),"


def _the_function(text: str) -> str:
    """把那支函式的本體挖出來。

    **不要用子字串比對**：`roundsRowForSequenceXX` 也含有
    `roundsRowForSequence`，改個名字照樣通過——D5 就是這樣一開始沒有牙齒的。
    """
    if DEFINITION not in text:
        return ""
    body = text[text.index(DEFINITION) :]
    end = body.find(chr(10) + "}")
    return body[: end + 2] if end >= 0 else body



def test_the_page_picks_the_row_instead_of_always_drawing_planning() -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是它 2026-08-27 之前的樣子：一行無條件的
    `rowIf('planning_rounds', ...)`，不看序列。
    """
    text = BUILT.read_text(encoding="utf-8")

    # **「被定義」不等於「被叫到」。**只比名字的話，把那一列換回
    # 無條件的 rowIf、而函式留在檔案裡沒人呼叫，這條照樣是綠的。
    assert THE_CALL in text, (
        f"回合數那一列不是 {THE_PICKER}() 畫的了——那表示它又變回無條件畫的。"
        "計畫序列讀 planning_rounds、開發序列讀 max_fix_attempts，"
        "畫錯那一個等於給他一顆按了沒作用的旋鈕。"
    )
    unconditional = re.findall(r"^\s+rowIf\('(planning_rounds|max_fix_attempts)'", text, re.M)
    assert not unconditional, (
        f"這幾個回合數又被無條件畫了：{unconditional}——"
        "它們各自只有一條序列讀得到，畫的時候要挑。"
    )

    body = _the_function(text)
    assert body, f"找不到 {DEFINITION}"
    # **要看的是它真的在分支，不是它提到過那個字。**函式裡本來就有一句
    # `has('workflow_mode')` 的前提檢查，只比字的話 `if(true)` 照樣通過。
    assert re.search(r"if\(S\.workflow_mode===", body), (
        "它沒有真的看序列在分支——兩條路畫同一列，"
        "切到另一條序列的人一樣按不到會生效的那顆。"
    )
    assert "planning_rounds" in body and "max_fix_attempts" in body, (
        "兩條序列各自的那一個要都在——只畫一個就是漏掉另一條路的人"
    )


def test_the_engine_pushes_what_the_page_needs_to_decide() -> None:
    """畫面挑得動，前提是引擎有把序列推過去。

    少推 `workflow_mode`，上面那條照樣是綠的（函式還在），而畫面會什麼
    都畫不出來——**一條看不到自己前提的檢查，跟一條恆真的檢查一樣。**
    """
    src = PANEL.read_text(encoding="utf-8")
    for key in ("workflow_mode", "planning_rounds", "max_fix_attempts"):
        assert f'"{key}": settings.' in src, (
            f"`collect_state` 沒有推 {key}，設定頁挑不出要畫哪一列"
        )


def test_the_factory_machine_sees_the_dial_that_actually_turns() -> None:
    """一台什麼都沒設定的新機器，設定頁上那一列是會生效的那一個。

    這條把兩件事綁在一起：出廠序列是什麼（`ai_settings.py`），
    以及那條序列讀哪個回合數。**其中一個被改掉、另一個沒跟上，就會紅。**
    """
    factory = AIExecutionSettings()
    reads = "planning_rounds" if factory.workflow_mode == "planning" else "max_fix_attempts"
    ignores = "max_fix_attempts" if reads == "planning_rounds" else "planning_rounds"
    value = getattr(factory, reads)
    assert value in (0, 1, 2, 3), value
    # 出廠值一定要在畫面畫得出來的選項裡，否則他看到的是一列全空的按鈕
    options = {"planning_rounds": {1, 2, 3}, "max_fix_attempts": {0, 1, 2}}[reads]
    assert value in options, (
        f"出廠序列是 {factory.workflow_mode}，它讀的是 {reads}，出廠值 {value}，"
        f"而設定頁那一列的選項是 {sorted(options)}——"
        "他會看到一列沒有任何選項被選中的按鈕。"
        f"（順帶：這條序列不讀 {ignores}，所以那一列不該畫。）"
    )


def test_the_built_page_matches_the_design_source() -> None:
    """改了設計檔忘記重建，畫面上完全看不出差別。"""
    if not SOURCE.is_file():
        pytest.skip(f"這台機器上沒有 {SOURCE}")
    source = _the_function(SOURCE.read_text(encoding="utf-8"))
    built = _the_function(BUILT.read_text(encoding="utf-8"))
    assert source, (
        f"設計原始檔裡找不到 {DEFINITION}——產物是從那裡建出來的，"
        "下一次 build_pages.py 會把這個修正洗掉，而畫面上看不出差別"
    )
    assert source == built, (
        "設計原始檔跟產物的那支函式對不上——改完設計要跑 "
        "`app/ui/web/build_pages.py`"
    )
