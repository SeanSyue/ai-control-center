"""設定檔壞掉的專案，**畫面上不准跟他親手選的長得一樣**。

## 這一條擋的是客戶資料外流，不是體驗

每個專案有一個敏感度（個人／客戶／KYC 高敏感），而那個值有**三種來源**：

| | 意思 |
|---|---|
| `chosen` | 他自己選的 |
| `default` | 他還沒選過（新專案）|
| `unreadable` | **這個專案的設定檔讀不到，所以用最嚴的那一級頂著** |

引擎 2026-08-24 就算得出這件事（`ProjectRegistry.sensitivity_source()`），
而**畫面那一半一直沒接**——三種都是一個選好的下拉選單。

於是一個設定檔壞掉的專案，看起來跟他**親手設成 KYC** 的專案一模一樣。
他最合理的反應是：

> 「這個又不是 KYC 啊，改低一點。」

改下去，那個專案的逐字執行紀錄就**開始往外同步**。

## 為什麼只有 `unreadable` 給看得見的記號

`default` 不危險——他還沒選過，改成什麼都是他的意思。
`unreadable` 危險，因為**那個值是機器頂上去的，而它看起來像他的選擇**。

三種都給記號的話，那個記號會出現在幾乎每一個專案上，而**一個永遠都在的
警告等於沒有警告**（同一條判準寫在 `qualitative_acceptance.still_learning`
與 `record_run` 的說明裡）。

## 這一條的另一半在 `tests/test_the_screen_knows_why_a_value_is_what_it_is.py`

那一支釘的是**引擎算不算得出來**；這一支釘的是**推不推得到畫面、畫面畫不畫**。
兩支都要——引擎算對了而沒有人讀它，跟沒算是一樣的。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / "app" / "ui" / "web"


def _shell_project(tmp_path: Path, *, settings: str | None) -> dict:
    """建一個真的視窗，回它推給外框的 `shell.project`。"""
    previous = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(tmp_path / "appdata")
    try:
        QApplication.instance() or QApplication([])
        project = tmp_path / "案子"
        (project / ".aicc").mkdir(parents=True)
        ProjectRegistry.initialize_project(project)
        target = project / ".aicc" / "project.json"
        if settings is None:
            target.unlink()
        else:
            target.write_text(settings, encoding="utf-8")

        from app.ui.main_window import MainWindow

        window = MainWindow(project)
        try:
            return window._shell_state()["shell"]["project"]
        finally:
            window.hide()
            window.setParent(None)
            QApplication.instance().processEvents()
    finally:
        if previous is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = previous


def test_a_broken_settings_file_reaches_the_screen_as_unreadable(
    tmp_path: Path,
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是 2026-08-24 之前的樣子：`shell.project` 只推
    `sensitivity`，於是壞掉的專案在畫面上是一個正常的下拉選單。
    """
    pushed = _shell_project(tmp_path, settings="{ 這不是 JSON")
    assert pushed.get("sensitivity_source") == ProjectRegistry.SOURCE_UNREADABLE, (
        "設定檔壞掉的專案，畫面拿到的來源是 "
        f"{pushed.get('sensitivity_source')!r}——"
        "它看起來會跟他親手選的一模一樣，而他最合理的反應是改低"
    )


def test_a_project_he_chose_says_chosen(tmp_path: Path) -> None:
    """**不准過度警告**：他自己選過的不該掛記號。"""
    pushed = _shell_project(
        tmp_path,
        settings=json.dumps({"name": "案子", "sensitivity": "client"}, ensure_ascii=False),
    )
    assert pushed.get("sensitivity_source") == ProjectRegistry.SOURCE_CHOSEN


def test_a_new_project_says_default_not_chosen(tmp_path: Path) -> None:
    """還沒選過跟選過要分得出來——不然精靈跳過那一題就變成「他選了個人」。"""
    pushed = _shell_project(
        tmp_path, settings=json.dumps({"name": "案子"}, ensure_ascii=False)
    )
    assert pushed.get("sensitivity_source") == ProjectRegistry.SOURCE_DEFAULT


def test_the_label_comes_from_the_engine_not_a_second_copy(tmp_path: Path) -> None:
    """標籤也一起推。

    畫面自己維護一份的下場**已經發生了**：引擎寫「高敏感度／KYC」，
    `shell.js` 寫「KYC／高敏感」——兩邊的字在 2026-08-24 就不一樣了。
    """
    from app.services.project_registry import sensitivity_label

    pushed = _shell_project(
        tmp_path,
        settings=json.dumps(
            {"name": "案子", "sensitivity": "sensitive_kyc"}, ensure_ascii=False
        ),
    )
    assert pushed.get("sensitivity_label") == sensitivity_label("sensitive_kyc")


def test_the_page_actually_reads_the_source(tmp_path: Path) -> None:
    """推出去還要有人讀。

    這一條擋的是「引擎算對了、推出去了、而畫面一個字都沒讀」——
    這個專案反覆出現的那一類，而它**看起來完全正常**。
    """
    page = (WEB / "shell.js").read_text(encoding="utf-8-sig")
    # **比的是那個取值，不是那個名字。**比名字的話，我自己寫在上面那段
    # 註解裡的「Python 推 `sensitivity_source`」就足以讓它過——
    # 而那正是今天在 `test_no_field_is_pushed_into_the_void` 修掉的同一個病
    # （三個 `group` 收成一個名字）。它在同一天又犯了一次。
    assert "SENS_WHY[p.sensitivity_source]" in page, (
        "`shell.js` 沒有讀 `sensitivity_source`——"
        "改 `AICC-視覺概念/shell.js` 再跑 `app/ui/web/build_pages.py`，"
        "**不要直接改產物**"
    )


def _sens_why_block() -> str:
    page = (WEB / "shell.js").read_text(encoding="utf-8-sig")
    start = page.index("const SENS_WHY")
    return page[start : page.index("};", start)]


def _entry(name: str, nxt: str) -> str:
    block = _sens_why_block()
    return block[block.index(name + ":") : block.index(nxt + ":")]


def test_the_dangerous_one_still_gets_the_warning() -> None:
    """設定檔讀不到的那一種，要有一個看得見的警告記號。

    它擋的是「隨手把敏感度改低」那個動作——而那一改，客戶專案的逐字紀錄
    就開始往外同步。
    """
    assert "mark: '⚠'" in _entry("unreadable", "default"), (
        "設定檔讀不到的那一種沒有看得見的記號"
    )


def test_the_one_he_chose_himself_never_gets_a_mark() -> None:
    """**他自己選過的，一個記號都不准有。**

    這一條是「一個永遠都在的警告等於沒有警告」剩下的那一半，
    而它是三種裡唯一不會再動的一種：`chosen` 的定義就是「他給的值」，
    在一個他給的值旁邊掛記號，那個記號會出現在**每一個**專案上。
    """
    chosen = _sens_why_block()
    chosen = chosen[chosen.index("chosen:"):]
    assert "mark: ''" in chosen, (
        "「他自己選的」也掛了記號——那個記號會出現在每一個設好的專案上，"
        "而一個永遠都在的記號等於沒有記號"
    )
    assert "cls: ''" in chosen, "「他自己選的」拿到了一個樣式，它應該什麼都沒有"


def test_the_two_visible_ones_do_not_look_the_same() -> None:
    """**這一條 2026-08-25 換過形狀，理由要留著。**

    本來是「`default` 不准有記號」，理由是三種都給記號的話那個記號會出現在
    幾乎每一個新專案上。頁面那一側 2026-08-25 反過來立論，而那個立論站得住：

    > 他開一個客戶專案，畫面顯示「個人」（最寬鬆的那一級），他以為那是設好的，
    > **而那個值是 AICC 填的，不是他給的**。`unreadable` 那一端還要他動手改低；
    > 這一端**連手都不用動**。

    所以 `default` 現在有一個記號。**但它必須跟危險的那一個長得不一樣**
    ——這才是原本那條規則真正要保住的東西：如果兩種記號一模一樣，
    「這個值不是你給的」跟「這個專案的設定檔壞了」就分不出來，
    而後者要他停下來，前者不用。

    改壞的樣子：有人把 `default` 也給 `'⚠'`，或給它
    `sens-unreadable` 那個玫瑰色 class。
    """
    default = _entry("default", "chosen")
    assert "mark: ''" not in default, (
        "`default` 的記號被拿掉了。拿掉是一個合法的決定（那是它 2026-08-25 "
        "之前的樣子），但要連同上面那段立論一起撤——**不要只改一行**"
    )
    assert "'⚠'" not in default, (
        "「他還沒選過」用了跟「設定檔讀不到」同一個警告記號——"
        "兩者一個要他停下來、一個不用，長得一樣就分不出來"
    )
    assert "sens-unreadable" not in default, (
        "「他還沒選過」用了危險那一種的樣式（玫瑰色＋光暈）"
    )


def test_every_mark_has_somewhere_to_get_its_colour_from() -> None:
    """**每一個**記號的樣式都要真的存在，不然它是一個沒有顏色的字元。

    掃的是 `SENS_WHY` 裡實際出現的每一個 class，不是寫死那一個
    ——寫死一個的話，新加的第三種記號沒有樣式也不會有人發現。
    """
    css = (WEB / "aicc.css").read_text(encoding="utf-8-sig")
    classes = re.findall(r"cls: ' (sens-[a-z-]+)'", _sens_why_block())
    assert classes, "`SENS_WHY` 裡一個 class 都掃不到，這條檢查什麼都沒檢查到"
    for name in classes:
        # 整條選擇器一起比。只比 `.sens-unreadable` 的話，
        # `.sens-unreadable-disabled` 也含著它——同一個病，第三次。
        assert f".sens-why.{name}{{" in css, (
            f"`aicc.css` 少了 `.{name}` 的樣式——那個記號會變成一個沒有顏色的字元"
        )
