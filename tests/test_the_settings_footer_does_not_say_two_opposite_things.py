"""設定頁底部那一行不准同時寫「已儲存」跟「尚未變更」。

使用者看到的那個畫面：每一次重新打開 App，設定頁捲到底，那一行寫著

    已儲存 · 尚未變更

兩句話互相否定，而**兩句都是假的**——他的設定早就在磁碟上而且正在生效，
「尚未變更」只是「這一次開啟之後還沒有人動過」。他讀到的是「我上次調的東西
沒有存到」，於是再調一次；或者更糟，他相信「已儲存」而其實那一格從來沒有寫進去
（那是另一件事，`test_a_setting_you_picked_comes_back_the_same.py` 在守）。

怎麼變成這樣的：**頁面那一側本來就寫對了**——`${S.saved_at?`已儲存 · ${S.saved_at}`:''}`，
沒有時間就整行不印。是 Python 這一側在還沒存過的時候塞了一句
`t("settings.never_changed")`（「尚未變更」）進那個**時間戳**欄位，
把頁面的守衛繞過去了。

> **「沒有值」要用 `None` 講，不要用一句散文講。**散文在下游會被當成資料用，
> 而那個下游通常是一個字串模板。

所以這一份守兩件事，兩件都要：**推的是時間不是話**，以及**頁面沒有值就不印**。

## 這條檢查擋不住什麼

* 擋不住「那個時間對不對」。`_saved_at` 是這一次開起來之後存過的時間，
  重開 App 就回到 None——那是誠實的（沒有值就不印），但它講不出
  「上一次是昨天下午存的」。要講那句話得從設定檔的 mtime 讀，而 mtime 說的是
  「檔案被寫過」不是「他改過」（專案初始化就會寫一次），**那會是一句新的假話**，
  所以沒有做，見 `docs/WHY.md` 同名那一則。
* 擋不住頁面上別的地方冒出第二句「已儲存」。這一份只讀 `<div class="foot">`
  那一塊。
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.i18n import STRINGS  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.settings_web_panel import SettingsWebPanel  # noqa: E402

PAGE = Path(__file__).resolve().parents[1] / "app" / "ui" / "web" / "settings_v1.html"
A_TIME = re.compile(r"^\d{1,2}:\d{2}$")


def _panel(tmp_path: Path) -> SettingsWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir(parents=True)
    ProjectRegistry.initialize_project(project)
    panel = SettingsWebPanel(project)
    panel._page_ready = True
    return panel


def _footer() -> str:
    text = PAGE.read_text(encoding="utf-8")
    start = text.index('<div class="foot">')
    return text[start : text.index("</div>", start) + len("</div>")]


def test_a_settings_page_that_was_never_saved_pushes_no_time_at_all(
    tmp_path: Path,
) -> None:
    """還沒存過的時候，這一格是 None——不是一句話。

    改壞的樣子：把 `collect_state()` 的 `saved_at` 改回
    `... if self._saved_at else t("settings.never_changed")`。
    畫面上：每次重開 App，底部寫著「已儲存 · 尚未變更」。
    """
    saved_at = _panel(tmp_path).collect_state()["saved_at"]

    assert saved_at is None, (
        f"還沒存過，這一格卻推了 {saved_at!r} 出去。\n"
        "頁面把它包進「已儲存 · ${S.saved_at}」的模板裡——任何非空的東西"
        "都會讓那一行印出來，包括一句說「還沒存過」的話。"
    )


def test_the_time_it_pushes_is_a_time_and_not_a_sentence(tmp_path: Path) -> None:
    """存過之後推的是 `HH:MM`，而且**只有**時間。

    這條跟上面那條分開寫，是因為「不准是散文」跟「不准是空的」是兩件事，
    而修錯方向（例如把 None 改成空字串再由頁面判斷）只會過其中一條。
    """
    panel = _panel(tmp_path)

    panel.handle_action("set:effort", "low")
    saved_at = panel.collect_state()["saved_at"]

    assert isinstance(saved_at, str) and A_TIME.match(saved_at), (
        f"存過之後這一格是 {saved_at!r}——它要是一個時間，"
        "因為頁面會把它接在「已儲存 · 」後面直接印出來。"
    )


def test_the_footer_prints_nothing_when_there_is_no_time() -> None:
    """頁面那一側的守衛還在，而且底下沒有第二句話跟它打架。

    改壞的樣子：把 `<div class="foot">` 那一行改回
    `${S.dirty?'有未儲存的變更':(S.saved_at?...)}`——那句「有未儲存的變更」
    今天一定是假的（2026-08-24 之後每一次改動當場落地，沒有「未儲存」這回事）。
    """
    footer = _footer()

    assert "S.saved_at?" in footer, (
        f"底部那一行不再由「有沒有時間」決定要不要印：\n{footer}\n"
        "沒有這個守衛，Python 那一側推什麼過來都會被印出來。"
    )
    never_changed = STRINGS["settings.never_changed"]
    for lie in (never_changed, "有未儲存的變更"):
        assert lie not in footer, (
            f"底部那一行還寫著「{lie}」：\n{footer}\n"
            "這一頁按下去就存了，沒有「未儲存」這個狀態——一句永遠是假的話，"
            "比沒有那句話更糟。"
        )
    assert "<button" not in footer, (
        f"底部又長出按鈕了：\n{footer}\n"
        "「儲存變更」與「還原」2026-08-24 拍板拿掉：這一頁按下去就存了，"
        "而一顆對一半控制項灰掉的按鈕，本身就是那個問題的第二個版本。"
    )


def test_nobody_pushes_that_sentence_into_the_page_any_more() -> None:
    """「尚未變更」那句話不准再出現在設定面板的程式碼裡。

    它現在只剩 `app/i18n.py` 裡一列沒有人叫的字串（拿掉它要動的檔案不在
    這一組手上，記在 `docs/WHY.md`）。這條守的是**不要有人把它接回去**——
    上面那兩條問的是「今天推了什麼」，這條問的是「那條路還在不在」。

    用 AST 不用字面比對：那個 key **應該**留在註解裡（那一段註解正是在講
    這個 bug 怎麼發生的），而**一條會把註解讀成違規的檢查，會逼人刪掉註解**。
    要擋的是呼叫，不是提到。
    """
    source = Path(__file__).resolve().parents[1] / "app" / "ui" / "settings_web_panel.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "t"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "settings.never_changed"
    ]

    assert not calls, (
        f"`settings_web_panel.py:{calls}` 又在叫 "
        '`t("settings.never_changed")` 了。'
        "那句話會被塞進一個時間戳欄位，然後印成「已儲存 · 尚未變更」。"
    )
