"""LINE 憑證存得進去，也要拿得出來（掃描「算出來沒人讀」時挖到）。

`LineNotifier.clear_credentials()` 一直都在，**沒有任何地方呼叫它**。設定流程
只有「輸入 token → 輸入 user id → 存起來 → 測試發送」，沒有反方向。

少了那條路的後果不是不方便而已：

* 換掉 token 之後，舊的那把還留在這台機器上，而 AICC 會拿著它每十分鐘失敗一次。
* 不想再用 LINE 的人，沒有辦法把憑證從應用程式裡拿掉。

**一個存得進去、拿不出來的憑證，是使用者對這個 app 少一分信任的地方。**

另一半同樣重要：訊息必須講明刪掉的只有本機這一份。AICC 沒有能力、也不該有
能力去撤銷 LINE 那邊的 token——讓人以為按了就等於註銷，會讓一把還有效的鑰匙
留在外面而當事人以為處理完了。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QInputDialog,
    QMessageBox,
)

from app.services.line_notifier import LineNotifier  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    """沒有任何一條路可以開出真的對話框。

    **這條是負向驗證逼出來的，而且是用最貴的方式**：跑破壞時其中一條讓
    `configure_line` 跳過問句、直接走到沒有被 patch 的 `QInputDialog.getText`，
    於是那一輪卡在一個永遠等不到人按的 modal 上。我把 python 砍掉才停下來，
    而 `taskkill /F` 不會讓 `finally` 有機會執行——**破壞就這樣留在原始碼裡**，
    要手動還原。

    所以預設值放在 autouse fixture 裡，而不是每條測試各自 patch：一條測試
    只要有一個沒 patch 到的出口，就足以讓整輪掛住。個別測試要觀察哪一個
    對話框，再自己覆蓋回去。
    """
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False))
    )
    for name, answer in (
        ("question", QMessageBox.Cancel),
        ("information", QMessageBox.Ok),
        ("warning", QMessageBox.Ok),
        ("critical", QMessageBox.Ok),
    ):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, _r=answer, **k: _r)
        )


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# Task\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)
    made = MainWindow(project)
    yield made
    made.deleteLater()


def _configured(window, monkeypatch) -> None:
    """Put real credentials in place through the service, not by faking state."""
    monkeypatch.setattr(LineNotifier, "_protect", lambda self, data: data)
    monkeypatch.setattr(LineNotifier, "_unprotect", lambda self, data: data)
    window.line_notifier.save_credentials("token-abc", "U123")
    assert window.line_notifier.is_configured(), "前提沒建立起來"


def test_a_configured_machine_is_offered_a_way_out(window, monkeypatch) -> None:
    """設定過之後再進來，要問「改」還是「移除」，不是直接又問 token。"""
    _configured(window, monkeypatch)
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: (asked.append(a[2]), QMessageBox.Cancel)[1]),
    )

    window.configure_line()

    assert asked, "已經設定過卻沒有問要改還是要移除"
    assert "移除" in asked[0], asked[0]


def test_choosing_remove_actually_deletes_it(window, monkeypatch) -> None:
    _configured(window, monkeypatch)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard)
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    window.configure_line()

    assert not window.line_notifier.is_configured()
    assert not window.line_notifier.config_path.exists()


def test_removing_says_it_only_deleted_the_local_copy(window, monkeypatch) -> None:
    """最重要的一句。

    AICC 撤銷不了 LINE 那邊的 token。讓人以為按了就等於註銷，會讓一把還有效
    的鑰匙留在外面而當事人以為處理完了。
    """
    _configured(window, monkeypatch)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard)
    )
    told: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: told.append(a[2]))
    )

    window.configure_line()

    assert told, "刪掉了卻什麼都沒說"
    assert "本機" in told[0], told[0]
    assert "LINE Developers" in told[0], "沒告訴他要去哪裡真的撤銷"


def test_cancelling_keeps_the_credentials(window, monkeypatch) -> None:
    _configured(window, monkeypatch)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Cancel)
    )
    asked_for_token: list[int] = []
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: (asked_for_token.append(1), "", False)[1:]),
    )

    window.configure_line()

    assert window.line_notifier.is_configured(), "取消卻把憑證刪了"
    assert not asked_for_token, "取消卻繼續問 token"


def test_choosing_change_goes_on_to_ask_for_a_new_token(
    window, monkeypatch
) -> None:
    # 反方向：修法不可以是「設定過就只能移除」。
    _configured(window, monkeypatch)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Save)
    )
    asked: list[str] = []
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: (asked.append(a[2]), "", False)[1:]),
    )

    window.configure_line()

    assert asked and "Token" in asked[0], asked


def test_a_fresh_machine_is_not_asked_about_removing(window, monkeypatch) -> None:
    """沒設定過的人不該看到「要移除嗎」。

    一個問你要不要刪除一個不存在的東西的對話框，是使用者學會亂按的方式。
    """
    assert not window.line_notifier.is_configured()
    asked: list[int] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: (asked.append(1), QMessageBox.Cancel)[1]),
    )
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False))
    )

    window.configure_line()

    assert not asked, "還沒設定就問要不要移除"


def test_the_settings_page_stops_saying_it_is_configured(
    window, monkeypatch
) -> None:
    """刪掉之後畫面要跟著變。

    設定頁那一格讀的是「憑證在不在」。刪了卻不重畫，那一格會繼續說已經設定好
    ——而使用者剛做的正是相反的事。
    """
    _configured(window, monkeypatch)
    assert window.settings_web_panel.collect_state()["line_token_set"] is True

    # 兩件不同的事，要分開驗：**值算對了**，以及**畫面被通知去重畫**。
    # 只驗前者的話，把 `push_state()` 拿掉照樣全綠——`collect_state()` 是當場
    # 從磁碟重算的，它看不出有沒有人叫畫面更新。負向驗證就是這樣抓到的。
    repainted: list[int] = []
    monkeypatch.setattr(
        window.settings_web_panel,
        "push_state",
        lambda: repainted.append(1),
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard)
    )
    window.configure_line()

    assert window.settings_web_panel.collect_state()["line_token_set"] is False
    assert repainted, "刪掉了但沒有叫設定頁重畫——那一格會繼續說已經設定好"
