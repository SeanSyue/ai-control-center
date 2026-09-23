"""出廠視窗大小底下，內容欄不准撐出外層捲軸。

頁面設計 2026-08-24 量到的：

> `page_stack` 有 760 的最小高度，而出廠視窗是 980×720，整個內容欄連同
> 決策橫幅住的那個 view 都在一個外層捲軸裡。結果：
>
> 一、出廠視窗大小的使用者，**從來沒看過設定頁那顆「儲存變更」**
>     ——它釘在摺線以下 142px。
> 二、前幾輪一直說決策橫幅不會被蓋住——那是真的，但**「看得到」不成立**。
>     使用者一捲頁面，那條「AI 停下來等你」的橫幅就被捲出畫面上緣。

**「一個看不到的停下來等於沒有停下來」**——那條規矩在這裡有一個現成的破口，
而它不是被誰改壞的，是**一個從來沒有在出廠大小底下量過的數字**。

---

# 這支檔案 2026-08-24 整個重寫過，而原因值得看

原本它掃的是 `main_window.py` 的**原始碼字面**：找有沒有人寫
`setMinimumHeight(超過 640)`。那個 760 被拿掉之後它就一直是綠的。

**而視窗那時候已經塞不下了。**實測 HEAD：內容需要 894，視窗給得起 720。

破口是 `session_panel.py` 四處（150／120／120／120）**加起來**的
——**沒有任何單一數字超標**。一支只會找「有沒有人寫超過 640」的檢查
永遠找不到它。

> **判準**：一條檢查如果量的是**寫出來的數字**，它就只抓得到
> 「某一個數字太大」。而版面塞不下最常見的原因是**加總**。
> 要抓加總只有一個辦法：**把東西真的組起來，量它**。

順帶一提，那支舊檢查的名字是 `test_nothing_demands_more_height...`
——「nothing」，而它只讀一個檔案。頁面設計提的那條規矩
（**檢查的名字不准宣稱得比掃描範圍多**）這是第三個樣本。

---

# 修法是頁面設計選的 A

> 外層捲軸與內層捲軸捲掉的東西不一樣。**A 不是「捲軸搬個位置」，是把
> 捲動的代價從全域的安全訊號換成局部的內容。**

`SessionTimelinePanel` 包進自己的捲軸（`MainWindow._within_budget`），
所以它自己捲，外層一動也不動。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402

# 標題列、選單、邊距吃掉的。抓寬鬆一點——這條要擋的是「塞不下」，
# 不是差幾個像素。
CHROME = 80


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    """一個真的組起來、resize 成出廠大小的視窗。

    **這個 fixture 就是這次重寫的全部**：舊版讀原始碼，所以看不到
    「四個數字加起來」。
    """
    tmp = tmp_path_factory.mktemp("fits")
    previous = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(tmp / "appdata")
    try:
        QApplication.instance() or QApplication([])
        project = tmp / "project"
        (project / ".aicc").mkdir(parents=True)
        ProjectRegistry.initialize_project(project)

        from app.ui.main_window import MainWindow

        made = MainWindow(project)
        width, height = made.FACTORY_SIZE
        made.resize(width, height)
        # **不 show()。**show 之後這個模組在收尾的時候會把整個 pytest 行程
        # 打掉（0xC0000409），而那個當掉會讓負向驗證分不出「測試紅了」跟
        # 「測試沒跑起來」——一條分不出來的破壞驗證等於沒有。
        # resize 加一次 processEvents 就足以讓版面算出來（實測數字一樣）。
        QApplication.instance().processEvents()
        yield made
        made.hide()
        made.setParent(None)
        QApplication.instance().processEvents()
    finally:
        if previous is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = previous


def test_the_window_really_got_built(window) -> None:
    """前提檢查：組不起來或堆疊是空的話，底下每一條都是恆真的。

    舊版的前提檢查問的是「找不找得到 `resize(...)` 這一行」——
    **那個問題就算答對了，也不代表視窗組得起來。**
    """
    assert window.page_scroll is not None
    assert window.page_stack.count() > 0, "堆疊是空的，底下的量測沒有意義"
    assert window.page_scroll.viewport().height() > 0


def test_the_content_column_does_not_force_the_outer_scrollbar(window) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：把 `SessionTimelinePanel` 從
    `MainWindow.PAGES_WITH_THEIR_OWN_SCROLL` 拿掉——它 658 的最小高度會
    直接撐開外層，而決策橫幅一捲就被推出畫面上緣。
    """
    needed = window.page_scroll.widget().minimumSizeHint().height()
    _width, factory_height = window.FACTORY_SIZE
    usable = factory_height - CHROME

    worst = sorted(
        (
            (window.page_stack.widget(i).minimumSizeHint().height(),
             type(window.page_stack.widget(i)).__name__)
            for i in range(window.page_stack.count())
        ),
        reverse=True,
    )[:3]

    assert needed <= usable, (
        f"出廠視窗高 {factory_height}，扣掉外框大約剩 {usable}，"
        f"而內容欄的最小高度是 {needed}。\n"
        f"最高的幾頁：{worst}\n\n"
        "塞不下的結果是整個內容欄進外層捲軸——**而外層一捲，"
        "決策橫幅（「AI 停下來等你」唯一的視覺訊號）會被推出畫面上緣**。\n"
        "太高的那一頁加進 `MainWindow.PAGES_WITH_THEIR_OWN_SCROLL`，"
        "讓它自己捲。"
    )


def test_no_single_page_is_over_budget_on_its_own(window) -> None:
    """每一頁自己也要塞得下。

    上面那條量的是**加起來**（堆疊會取最大值，所以其實也是逐頁），
    這一條把違規的那一頁**點名**——錯誤訊息只說「總和超了」的話，
    下一個人要自己一頁一頁量。
    """
    _width, factory_height = window.FACTORY_SIZE
    usable = factory_height - CHROME
    over = [
        f"{type(window.page_stack.widget(i)).__name__}："
        f"{window.page_stack.widget(i).minimumSizeHint().height()} > {usable}"
        for i in range(window.page_stack.count())
        if window.page_stack.widget(i).minimumSizeHint().height() > usable
    ]
    assert not over, "這幾頁自己就塞不下：\n  " + "\n  ".join(over)


def test_wrapping_a_page_did_not_hide_it_from_navigation(window) -> None:
    """包一層捲軸**不准改變任何人拿得到的東西**。

    `_pages` 存的還是面板本身，而堆疊裡放的是容器。兩者對不起來的話，
    被包起來的那一頁**永遠切不過去**——而 `setCurrentWidget` 對一個不在
    堆疊裡的 widget 是靜靜地什麼都不做，沒有錯誤訊息。
    """
    for key in window._pages:
        window._show_page(key)
        assert window._current_page_key() == key, (
            f"切到「{key}」之後，視窗說現在在「{window._current_page_key()}」"
            "——包一層之後導覽對不起來了"
        )


def test_the_wrapped_page_is_still_the_panel_itself(window) -> None:
    """`_pages` 拿到的要是面板，不是那層容器。

    拿到容器的話，`action_failed` 接不上、每一支拿 `_pages[...]` 的測試
    都會拿到一個沒有那些方法的東西。
    """
    for key, panel in window._pages.items():
        assert type(panel).__name__ != "QScrollArea", (
            f"「{key}」在 `_pages` 裡變成了容器，而不是面板本身"
        )
