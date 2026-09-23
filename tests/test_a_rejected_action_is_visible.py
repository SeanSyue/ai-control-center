"""被拒絕的動作**要看得出來被拒絕了**。

頁面設計 2026-08-23 的原話：

> **按了沒反應跟改好了長得一模一樣。**

網頁那一側是樂觀更新的——使用者按下去，格子當場就變了。被拒絕的時候
如果只彈一個警告框，那個格子**還停在他以為的新值上**：他關掉警告，看到
的是一個已經改好的畫面，而引擎裡什麼都沒變。

## 而寫這條的時候量到一件更糟的

`lineup_panel.action_failed` **從來沒有被接到任何地方**。

三個面板各自宣告一份 `action_failed = Signal(str)`，而 `main_window` 的
`_wire_panel_feedback()` 只有兩個面板叫得到（共享記憶、文件）。陣容頁那
六個「失敗了要跟使用者說」——**包括改名字與選職責失敗的那兩個，也就是
擋著五節點測試的那兩顆控制項**——從頭到尾掉進虛空。

**一個沒有人接的訊號，跟一個沒有發的訊號，在畫面上一模一樣。**
"""
from __future__ import annotations

import ast
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from app.services.project_registry import ProjectRegistry
from app.ui.main_window import MainWindow
from app.ui.web_panel import WebViewPanel

UI = Path("app/ui")


def test_the_base_class_owns_the_signal() -> None:
    """**一個宣告，不是三個。**

    各自宣告的結果就是各自被接——而「各自被接」的實際樣子是「有兩個被接、
    一個沒有」，然後沒人發現。
    """
    assert hasattr(WebViewPanel, "action_failed")
    assert hasattr(WebViewPanel, "reject")

    tree = ast.parse((UI / "web_panel.py").read_text(encoding="utf-8-sig"))
    declared_elsewhere = [
        path.name
        for path in UI.rglob("*.py")
        if path.name != "web_panel.py"
        and "action_failed = Signal" in path.read_text(encoding="utf-8-sig")
    ]
    assert not declared_elsewhere, (
        f"這幾個面板又自己宣告了一份 action_failed：{declared_elsewhere}。"
        "各自宣告的結果就是各自被接，而漏掉一個的時候畫面上看不出來。"
    )
    assert tree is not None


def test_nobody_emits_it_directly() -> None:
    """**發訊號跟推狀態要綁在一起。**

    分成兩個呼叫寫在九個地方的話，第十個地方會忘記推——而忘記推的樣子
    就是頁面設計講的那句：按了沒反應跟改好了長得一模一樣。
    """
    offenders: list[str] = []
    for path in UI.rglob("*.py"):
        if path.name == "web_panel.py":
            continue          # 定義它的地方
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "emit":
                continue
            inner = func.value
            if isinstance(inner, ast.Attribute) and inner.attr == "action_failed":
                offenders.append(f"{path.as_posix()}:{node.lineno}")
    assert not offenders, (
        "這幾處直接發了 action_failed，沒有推狀態：\n  "
        + "\n  ".join(offenders)
        + "\n改叫 `self.reject(訊息)`——它會做兩件事。"
    )


def test_reject_pushes_the_real_state_back(tmp_path, monkeypatch) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：`reject()` 只發訊號不推狀態。畫面上那個格子留在他以為的
    新值，而引擎裡什麼都沒變。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])

    pushed: list[str] = []
    said: list[str] = []

    class Panel(WebViewPanel):
        PAGE = "shell.html"

        def collect_state(self) -> dict:
            return {}

        def push_state(self) -> None:
            pushed.append("推了")

    panel = Panel()
    panel.action_failed.connect(said.append)
    panel.reject("不行")

    assert said == ["不行"], "沒有跟使用者說"
    assert pushed == ["推了"], "說了但沒有把畫面上的值退回去"


def _panels_whose_failures_vanish(window, monkeypatch) -> list[str]:
    """真的發一次，看使用者收不收得到。

    不用 `receivers()`：它在 PySide6 只吃 `SIGNAL()` 字串，而且「有一條連線」
    跟「訊息真的到得了使用者眼前」不是同一件事——中間可以接到一個什麼都
    不做的 lambda 上。**這裡量的是後者。**
    """
    seen: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: seen.append(args[-1])
    )
    vanished: list[str] = []
    for key, widget in window._pages.items():
        signal = getattr(widget, "action_failed", None)
        if signal is None:
            continue
        before = len(seen)
        signal.emit(f"測試訊息-{key}")
        if len(seen) == before:
            vanished.append(key)
    return vanished


def test_every_panel_in_the_window_has_somewhere_for_failures_to_go(
    tmp_path, monkeypatch
) -> None:
    """**每一個面板的失敗都要有地方去。**

    改壞的樣子就是它 2026-08-24 之前的樣子：陣容頁的六個 `action_failed`
    一個都沒接上。使用者按了「改名字」失敗，畫面上什麼都不會發生。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    window = MainWindow(project)

    try:
        with_signal = [
            key
            for key, widget in window._pages.items()
            if getattr(widget, "action_failed", None) is not None
        ]
        # 前提檢查：一頁都撈不到的話下面那條是恆真的。
        assert len(with_signal) >= 3, f"只找到 {with_signal}，這條檢查等於空轉"

        vanished = _panels_whose_failures_vanish(window, monkeypatch)
        assert not vanished, (
            f"這幾頁的失敗訊息到不了使用者眼前：{vanished}。"
            "一個沒有人接的訊號，跟一個沒有發的訊號，在畫面上一模一樣。"
        )
    finally:
        window.deleteLater()
        application.processEvents()


def test_the_lineup_page_is_one_of_them(tmp_path, monkeypatch) -> None:
    """點名陣容頁。

    上面那條是通則，這條咬的是**實際出過事的那一頁**——而且它正是擋著
    五節點測試的那兩顆控制項（改名字、選職責）所在的頁。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    window = MainWindow(project)

    try:
        seen: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *args, **kwargs: seen.append(args[-1])
        )
        window.lineup_panel.action_failed.emit("改名字失敗了")
        assert seen == ["改名字失敗了"], "陣容頁的失敗訊息又掉進虛空了"
    finally:
        window.deleteLater()
        application.processEvents()
