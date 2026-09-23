"""敏感度答不出來的時候，設定頁不准停在最寬鬆的那一級。

使用者看到的那個壞掉的樣子：設定頁的「這個專案算哪一種」停在**個人**，
跟他親手選「個人」的那個下拉選單**一模一樣**——沒有記號、沒有訊息。
而那一格決定的是這個專案的逐字執行紀錄准不准往雲端同步、准不准匯出。
他不會去改一個看起來已經對了的選項。

`ProjectRegistry.get_sensitivity()` 自己的契約是**倒向最嚴的那一級**
（讀不到、解析不出來、認不得的值一律 `sensitive_kyc`），而設定面板在它
外面又包了一個 `except Exception: profile = "personal"`——同一個事實兩份
判斷，而第二份倒向相反的方向。

今天踩不到（`get_sensitivity` 把 OSError 與 JSONDecodeError 都自己接住了），
所以這一份用 monkeypatch 讓那一格真的丟例外。**要驗的是「答不出來的時候
倒向哪一邊」，不是某一種損壞法的重現**——而下一次有人在 `get_sensitivity`
裡多加一種例外，倒的方向就是這裡決定的。

## 這條檢查擋不住什麼

* 擋不住「畫面有沒有告訴他這是機器頂上去的」。那一半是
  `tests/test_a_broken_settings_file_is_not_dressed_up_as_a_choice.py`
  在守的（`sensitivity_source` → 外框那顆 ⚠ 記號），走的是 shell 那條路，
  不是設定頁這個下拉選單。
* 擋不住別的面板自己再寫一份同樣的預設。這一份只問設定面板。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import (  # noqa: E402
    DEFAULT_SENSITIVITY,
    FAIL_CLOSED_SENSITIVITY,
    ProjectRegistry,
)
from app.ui.settings_web_panel import SettingsWebPanel  # noqa: E402


def _panel(tmp_path: Path) -> SettingsWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir(parents=True)
    ProjectRegistry.initialize_project(project)
    panel = SettingsWebPanel(project)
    panel._page_ready = True
    return panel


def test_a_sensitivity_that_cannot_be_read_is_not_shown_as_personal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """答不出來就推最嚴的那一級。

    改壞的樣子：把那個 `except` 的 `profile` 改回 `"personal"`。畫面上：
    一個設定檔壞掉的客戶專案，敏感度那一格顯示「個人」，而他讀到的是
    「這個專案我設成個人」。
    """
    panel = _panel(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("讀不到這個專案的設定檔")

    monkeypatch.setattr(ProjectRegistry, "get_sensitivity", boom)

    profile = panel.collect_state()["profile"]

    assert profile != DEFAULT_SENSITIVITY, (
        f"敏感度答不出來，設定頁卻推了最寬鬆的 {profile!r} 出去——"
        "畫面上跟他親手選「個人」一模一樣，而那一格決定逐字紀錄准不准外流。"
    )
    assert profile == FAIL_CLOSED_SENSITIVITY, (
        f"答不出來的時候推的是 {profile!r}，而 `get_sensitivity()` 自己的契約"
        f"是倒向 {FAIL_CLOSED_SENSITIVITY!r}。**同一個事實兩份判斷，"
        "而這一份倒向相反的方向。**"
    )


def test_a_project_that_reads_fine_is_still_whatever_he_chose(
    tmp_path: Path,
) -> None:
    """前提檢查：正常的專案不准被這條修法一起拉到最嚴的那一級。

    沒有這一條，把整格寫死成 `sensitive_kyc` 也會讓上面那條變綠——而那會讓
    每一個新專案都掛著最高等級，於是那個等級失去意義（一個永遠都在的
    警告等於沒有警告）。
    """
    panel = _panel(tmp_path)

    assert panel.collect_state()["profile"] == DEFAULT_SENSITIVITY, (
        "一個剛開好、還沒選過敏感度的專案應該是出廠預設，"
        "不是被 fail-closed 頂上去的那一級"
    )

    panel.handle_action("set:profile", "client")

    assert panel.collect_state()["profile"] == "client", (
        "他選了客戶等級，推回來的卻不是——那一格自己彈回去了"
    )
