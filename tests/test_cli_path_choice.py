"""第四層：「找不到」要能變成「那你指給我看」（ROADMAP P12）。

`cli_discovery` 的文件把第四層寫成整個模組的重點——上面三層都是猜測，都可能在
沒人預料到的機器上失效，所以死路必須有出口。

而實際上它只是 `discover()` 的一個 `chosen` 參數，**沒有任何地方存它，也沒有
任何地方問使用者**。兩件事都缺：

* 沒有存 → 「記住」那一半不存在。每次啟動都要重新指路的逃生口，第二次就沒有
  人會用。
* 沒有入口 → 第四層等於不存在，探索功能只是把死路換了個地方。

這個檔案驗的是那條路真的走得通，以及**指完之後會立刻重驗**——存起來卻讓那一列
繼續寫著「找不到指令」，會讓人以為自己指錯了，然後再指一次。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from app.services import cli_discovery, startup_diagnostics as sd  # noqa: E402
from app.ui import diagnostics_dialog as dlg  # noqa: E402
from app.ui.diagnostics_dialog import DiagnosticsDialog  # noqa: E402


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    """每條測試自己一份 %APPDATA%。

    真正的那份底下有使用者實際的選擇，測試不該讀它也不該寫它。
    """
    root = tmp_path / "appdata"
    root.mkdir()
    monkeypatch.setenv("APPDATA", str(root))
    return root


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _fake_cli(tmp_path: Path, name: str = "claude.cmd") -> Path:
    path = tmp_path / "自己裝的地方" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@echo off\n", encoding="utf-8")
    return path


# --- 記住 -------------------------------------------------------------------


def test_nothing_is_remembered_to_begin_with(appdata) -> None:
    assert cli_discovery.chosen_path("claude") is None


def test_a_chosen_path_survives(appdata, tmp_path) -> None:
    target = _fake_cli(tmp_path)

    cli_discovery.remember_chosen_path("claude", target)

    assert cli_discovery.chosen_path("claude") == target


def test_forgetting_it_works(appdata, tmp_path) -> None:
    cli_discovery.remember_chosen_path("claude", _fake_cli(tmp_path))

    cli_discovery.clear_chosen_path("claude")

    assert cli_discovery.chosen_path("claude") is None


def test_one_provider_does_not_overwrite_the_other(appdata, tmp_path) -> None:
    claude = _fake_cli(tmp_path, "claude.cmd")
    codex = _fake_cli(tmp_path, "codex.cmd")

    cli_discovery.remember_chosen_path("claude", claude)
    cli_discovery.remember_chosen_path("codex", codex)

    assert cli_discovery.chosen_path("claude") == claude
    assert cli_discovery.chosen_path("codex") == codex


def test_a_path_that_has_since_been_uninstalled_is_not_offered(
    appdata, tmp_path
) -> None:
    """使用者要看到的是「你指的那個不見了」，不是靜靜地退回「找不到」。

    原封不動傳給 `discover()` 只會多一次注定失敗的探測，所以存在性在讀取的
    時候就檢查掉。
    """
    target = _fake_cli(tmp_path)
    cli_discovery.remember_chosen_path("claude", target)
    target.unlink()

    assert cli_discovery.chosen_path("claude") is None


def test_the_choice_is_not_written_inside_a_project(appdata, tmp_path) -> None:
    """ROADMAP P12 的規則：不得放進任何專案資料夾。

    專案底下的東西 Git、匯出、同步都碰得到。這裡存的是路徑不是憑證，
    但**路徑本身就會洩漏使用者的帳號名稱**，規則一樣適用。
    """
    cli_discovery.remember_chosen_path("claude", _fake_cli(tmp_path))

    written = list(appdata.rglob("cli-paths.json"))
    assert written, "檔案沒寫出來"
    for path in written:
        assert ".aicc" not in path.parts, path


def test_a_corrupt_file_is_treated_as_no_choice(appdata, tmp_path) -> None:
    # 壞掉的 JSON 不可以讓啟動檢查整個掛掉。
    (appdata / "AIControlCenter").mkdir(parents=True, exist_ok=True)
    (appdata / "AIControlCenter" / "cli-paths.json").write_text(
        "{ 這不是 json", encoding="utf-8"
    )

    assert cli_discovery.chosen_path("claude") is None


# --- 接到啟動檢查上 ----------------------------------------------------------


def test_the_check_passes_the_remembered_path_to_discovery(
    appdata, tmp_path, monkeypatch
) -> None:
    """存起來卻不用，是這個專案最常見的那種 bug。"""
    target = _fake_cli(tmp_path)
    cli_discovery.remember_chosen_path("claude", target)
    monkeypatch.setattr(sd.shutil, "which", lambda name: None)

    seen: list = []

    def fake_discover(provider, chosen=None):
        seen.append(chosen)
        return []

    monkeypatch.setattr(cli_discovery, "discover", fake_discover)
    sd.check_claude()

    assert seen == [target], seen


def test_a_check_that_cannot_find_it_offers_the_escape_hatch(
    appdata, monkeypatch
) -> None:
    monkeypatch.setattr(sd.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_discovery, "discover", lambda provider, chosen=None: [])

    check = sd.check_claude()

    assert check.pick_provider == "claude", "「找不到」還是一個句點"


def test_a_check_that_is_fine_offers_nothing(appdata, monkeypatch) -> None:
    """反方向：一切正常的那一列不該有按鈕。

    每一列都掛一顆「選擇位置」，等於把它變成裝飾。
    """
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\ok\claude.cmd")
    monkeypatch.setattr(sd, "_tool_version", lambda executable: "2.1.226")
    monkeypatch.setattr(sd, "_signed_in", lambda executable, provider="": True)

    check = sd.check_claude()

    assert check.level is sd.Level.OK
    assert check.pick_provider == ""


# --- 對話框 ------------------------------------------------------------------


def _blocked_claude() -> list:
    return [
        sd.Check(
            "claude", "Claude Code", sd.Level.BLOCKED, "找不到指令", "去裝",
            pick_provider="claude",
        )
    ]


def test_the_dialog_draws_the_button_only_where_there_is_one(app, appdata) -> None:
    from PySide6.QtWidgets import QPushButton

    with_hatch = DiagnosticsDialog(_blocked_claude())
    without = DiagnosticsDialog(
        [sd.Check("git", "Git", sd.Level.OK, "2.45.0")]
    )

    def buttons(dialog) -> list[str]:
        return [
            b.text()
            for b in dialog._rows.findChildren(QPushButton)
        ]

    assert any("選擇位置" in text for text in buttons(with_hatch)), buttons(with_hatch)
    assert not buttons(without), buttons(without)
    with_hatch.deleteLater()
    without.deleteLater()


def test_picking_a_file_remembers_it_and_rechecks(
    app, appdata, tmp_path, monkeypatch
) -> None:
    target = _fake_cli(tmp_path)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(target), "")),
    )
    rechecked: list[int] = []
    monkeypatch.setattr(
        dlg, "run_diagnostics", lambda: (rechecked.append(1), _blocked_claude())[1]
    )

    dialog = DiagnosticsDialog(_blocked_claude())
    dialog._pick(dialog._checks[0])

    assert cli_discovery.chosen_path("claude") == target
    assert rechecked, "指完之後沒有重驗——那一列還會寫著「找不到指令」"
    dialog.deleteLater()


def test_cancelling_the_file_dialog_changes_nothing(
    app, appdata, monkeypatch
) -> None:
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    rechecked: list[int] = []
    monkeypatch.setattr(
        dlg, "run_diagnostics", lambda: (rechecked.append(1), _blocked_claude())[1]
    )

    dialog = DiagnosticsDialog(_blocked_claude())
    dialog._pick(dialog._checks[0])

    assert cli_discovery.chosen_path("claude") is None
    assert not rechecked
    dialog.deleteLater()


def test_forgetting_from_the_dialog_clears_it(
    app, appdata, tmp_path, monkeypatch
) -> None:
    cli_discovery.remember_chosen_path("claude", _fake_cli(tmp_path))
    monkeypatch.setattr(dlg, "run_diagnostics", _blocked_claude)

    dialog = DiagnosticsDialog(_blocked_claude())
    dialog._forget(dialog._checks[0])

    assert cli_discovery.chosen_path("claude") is None
    dialog.deleteLater()


def _candidate(provider: str, path: str, *, on_path=False, signed=True, version="2.1"):
    return cli_discovery.CliCandidate(
        provider=provider,
        path=Path(path),
        version=version,
        signed_in=signed,
        on_path=on_path,
    )


def test_the_scan_offers_the_copies_that_are_not_on_path(
    app, appdata, monkeypatch
) -> None:
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(
        cli_discovery,
        "discover_all",
        lambda: {
            "claude": [_candidate("claude", r"C:\bundled\claude.exe")],
            "codex": [],
        },
    )
    offered: list[list[str]] = []
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        staticmethod(lambda *a, **k: (offered.append(a[3]), a[3][0], True)[1:]),
    )
    monkeypatch.setattr(dlg, "run_diagnostics", _blocked_claude)

    dialog = DiagnosticsDialog(_blocked_claude())
    dialog._scan()

    assert offered, "掃描完全沒有問使用者要哪一個"
    assert len(offered[0]) == 1, offered
    assert r"C:\bundled\claude.exe" in offered[0][0], offered[0]
    dialog.deleteLater()


def test_the_scan_button_is_actually_connected(app, appdata, monkeypatch) -> None:
    """按下真正那顆按鈕，不是直接呼叫 `_scan()`。

    上面每一條都是直接呼叫，所以把 `clicked.connect` 那一行拿掉，它們**全部
    照樣綠**——負向驗證判它沒有牙齒。今天第二次犯同一種：驗了方法，沒驗接線。
    """
    scanned: list[int] = []
    monkeypatch.setattr(
        cli_discovery,
        "discover_all",
        lambda: (scanned.append(1), {"claude": [], "codex": []})[1],
    )
    from PySide6.QtWidgets import QMessageBox as _Box

    monkeypatch.setattr(_Box, "information", staticmethod(lambda *a, **k: None))

    dialog = DiagnosticsDialog(_blocked_claude())
    dialog._scan_button.click()

    assert scanned, "按鈕沒有接上任何東西"
    dialog.deleteLater()


def test_the_scan_label_says_enough_to_tell_two_copies_apart(
    app, appdata, monkeypatch
) -> None:
    """兩份 2.1.226 放在不同資料夾，只給版本號等於叫人猜。

    路徑、版本、登入狀態三個都要在——「找到了但登出」跟「找到了可以用」是
    完全不同的選擇。
    """
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(
        cli_discovery,
        "discover_all",
        lambda: {
            "claude": [
                _candidate("claude", r"C:\a\claude.exe", signed=True, version="2.1.226"),
                _candidate("claude", r"C:\b\claude.exe", signed=False, version="2.1.226"),
            ],
            "codex": [],
        },
    )
    offered: list[list[str]] = []
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        staticmethod(lambda *a, **k: (offered.append(a[3]), a[3][0], False)[1:]),
    )

    dialog = DiagnosticsDialog(_blocked_claude())
    dialog._scan()

    labels = offered[0]
    assert len(labels) == 2, labels
    assert any("已登入" in text for text in labels), labels
    assert any("尚未登入" in text for text in labels), labels
    assert all("claude.exe" in text for text in labels), labels
    dialog.deleteLater()


def test_a_copy_already_on_path_is_not_offered(app, appdata, monkeypatch) -> None:
    """在 PATH 上的那些不是使用者需要挑的東西，列出來只會擠掉有用的幾行。

    這台開發機兩支都在 PATH 上，所以掃描的正確結果就是「沒有別的」。
    """
    from PySide6.QtWidgets import QInputDialog, QMessageBox as _Box

    monkeypatch.setattr(
        cli_discovery,
        "discover_all",
        lambda: {
            "claude": [_candidate("claude", r"C:\path\claude.cmd", on_path=True)],
            "codex": [_candidate("codex", r"C:\path\codex.cmd", on_path=True)],
        },
    )
    asked: list[int] = []
    monkeypatch.setattr(
        QInputDialog, "getItem", staticmethod(lambda *a, **k: (asked.append(1), "", False)[1:])
    )
    told: list[str] = []
    monkeypatch.setattr(
        _Box, "information", staticmethod(lambda *a, **k: told.append(a[2]))
    )

    dialog = DiagnosticsDialog(_blocked_claude())
    dialog._scan()

    assert not asked, "把 PATH 上已經有的也拿來問了"
    # 而且不可以什麼都不說：按下去沒反應跟真的沒有別的副本，畫面上一樣。
    assert told and "沒有" in told[0], told
    assert "選擇位置" in told[0], "沒告訴他還有指路那條路"
    dialog.deleteLater()


def test_cancelling_the_chooser_remembers_nothing(app, appdata, monkeypatch) -> None:
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(
        cli_discovery,
        "discover_all",
        lambda: {"claude": [_candidate("claude", r"C:\b\claude.exe")], "codex": []},
    )
    monkeypatch.setattr(
        QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False))
    )
    rechecked: list[int] = []
    monkeypatch.setattr(
        dlg, "run_diagnostics", lambda: (rechecked.append(1), _blocked_claude())[1]
    )

    dialog = DiagnosticsDialog(_blocked_claude())
    dialog._scan()

    assert cli_discovery.chosen_path("claude") is None
    assert not rechecked
    dialog.deleteLater()


def test_choosing_from_the_scan_remembers_and_rechecks(
    app, appdata, tmp_path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QInputDialog

    target = _fake_cli(tmp_path)
    monkeypatch.setattr(
        cli_discovery,
        "discover_all",
        lambda: {"claude": [_candidate("claude", str(target))], "codex": []},
    )
    monkeypatch.setattr(
        QInputDialog, "getItem", staticmethod(lambda *a, **k: (a[3][0], True))
    )
    rechecked: list[int] = []
    monkeypatch.setattr(
        dlg, "run_diagnostics", lambda: (rechecked.append(1), _blocked_claude())[1]
    )

    dialog = DiagnosticsDialog(_blocked_claude())
    dialog._scan()

    assert cli_discovery.chosen_path("claude") == target
    assert rechecked, "挑完之後沒有重驗"
    dialog.deleteLater()


def test_rebuilding_the_rows_does_not_leave_the_old_ones_behind(
    app, appdata, monkeypatch
) -> None:
    """`takeAt` 會把嵌套的 layout 交回來，但它的 widget 還掛在對話框上。

    少了那一步，重驗一次就多一份，舊的那份疊在新的後面——而它寫的是重驗之前
    的狀態。
    """
    from PySide6.QtWidgets import QLabel

    monkeypatch.setattr(dlg, "run_diagnostics", _blocked_claude)
    dialog = DiagnosticsDialog(_blocked_claude())
    before = len(dialog._rows.findChildren(QLabel))

    dialog._recheck()
    dialog._recheck()

    assert len(dialog._rows.findChildren(QLabel)) == before
    dialog.deleteLater()
