"""權限模式必須真的是使用者選的那一個（ROADMAP P3）。

設定頁上的三個選項是「每次問／可改檔／全自動」，而在這之前**三個都存成
`auto`**——最寬鬆的那一個。兩個錯疊在一起：

1. 對照表寫的是 `{"ask": "ask", "acceptEdits": "edit", "auto": "auto"}`，
   而 `"ask"`、`"edit"` 都不在 `VALID_CLAUDE_PERMISSION_MODES` 裡；
2. 鍵寫成 `"acceptEdits"`，但頁面送的是 `"edits"`，連查都查不到。

於是 `ask` 被翻成 `"ask"`、`edits` 吃到 `.get()` 的預設值也變 `"ask"`，
兩個都不合法，`normalized()` 再把不合法的值一律改成 `"auto"`。

**錯誤沒有被擋下來，是被翻譯成了「全部放行」。** 而同一頁上就印著
「客戶或 KYC 專案請維持『每次問』」。一個做不到自己承諾的權限開關，
比沒有開關更糟，因為它看起來像有人在把關。

`--permission-mode` 是原封不動送進 Claude CLI 的，所以這不是顯示問題。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.settings_web_panel import PERMISSION, SettingsWebPanel  # noqa: E402

# 由寬到嚴。用來斷言「不會比使用者選的更寬」，而不是逐一比對字串——
# 這個檔案要守的就是「別往寬的那邊倒」這件事本身。
PERMISSIVENESS = {"auto": 3, "acceptEdits": 2, "default": 1, "plan": 0}


def _panel(tmp_path: Path) -> SettingsWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "proj"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    return SettingsWebPanel(project)


def _choose(panel: SettingsWebPanel, value: str) -> str:
    panel.handle_action("set:permission", value)
    panel.handle_action("save", "")
    return panel.store.load_project().claude_permission_mode


def test_every_choice_stores_a_mode_the_rest_of_the_app_accepts(
    tmp_path: Path,
) -> None:
    """不合法的值不會停在原地——它會被 `normalized()` 改成別的東西。"""
    panel = _panel(tmp_path)

    for choice in ("ask", "edits", "auto"):
        stored = _choose(panel, choice)
        assert stored in AIExecutionSettings.VALID_CLAUDE_PERMISSION_MODES, (
            f"{choice} 存成了 {stored!r}，不在合法集合裡"
        )


def test_asking_every_time_does_not_become_fully_automatic(
    tmp_path: Path,
) -> None:
    """這個檔案存在的理由，用使用者看得到的話講一次。"""
    panel = _panel(tmp_path)

    assert _choose(panel, "ask") != "auto"
    assert _choose(panel, "edits") != "auto"
    # 而「全自動」還是要真的全自動，不然這就變成硬擋了。
    assert _choose(panel, "auto") == "auto"


def test_the_three_choices_are_three_different_modes(tmp_path: Path) -> None:
    # 上一版三個都一樣，而「三個選項存出三個不同的值」是那時候唯一沒被
    # 任何斷言講出來的事。
    panel = _panel(tmp_path)

    stored = {choice: _choose(panel, choice) for choice in ("ask", "edits", "auto")}

    assert len(set(stored.values())) == 3, stored
    assert (
        PERMISSIVENESS[stored["ask"]]
        < PERMISSIVENESS[stored["edits"]]
        < PERMISSIVENESS[stored["auto"]]
    ), f"寬鬆程度的順序跟選項的順序對不上：{stored}"


def test_a_value_the_page_never_sends_falls_to_the_strictest(
    tmp_path: Path,
) -> None:
    """認不得的值往嚴格的那邊倒。

    跟 `import_files(allow_flagged=False)` 同一條原則：沒想過這件事的呼叫端
    拿到的是安全的那一邊。往寬的那邊倒的意思是**一個打錯的字會靜靜地升權**，
    而那正是上一版真正發生的事。
    """
    panel = _panel(tmp_path)

    stored = _choose(panel, "這不是選項")

    assert stored in AIExecutionSettings.VALID_CLAUDE_PERMISSION_MODES
    # 比「等於 default」講得更清楚一點：它不可以比三個真實選項裡最嚴的那個更寬。
    strictest_offered = min(PERMISSIVENESS[m] for m in PERMISSION.values())
    assert PERMISSIVENESS[stored] <= strictest_offered, stored


def test_an_unknown_stored_value_is_normalised_downwards() -> None:
    """服務層自己也要往嚴格的倒——設定檔是可以手改的。"""
    normalised = AIExecutionSettings(
        claude_permission_mode="whatever"
    ).normalized()

    assert normalised.claude_permission_mode == "default"


def test_what_is_stored_shows_back_as_the_same_choice(tmp_path: Path) -> None:
    # 存進去跟畫出來用的是同一張表的兩個方向，寫反了不會有人發現。
    panel = _panel(tmp_path)

    for choice in ("ask", "edits", "auto"):
        _choose(panel, choice)
        assert panel.collect_state()["permission"] == choice


def test_the_page_and_the_mapping_use_the_same_words() -> None:
    """兩個錯裡的第二個：頁面送 `edits`，表上的鍵是 `acceptEdits`。

    鍵對不上不會報錯，只會安靜地吃 `.get()` 的預設值——所以這條直接去頁面
    把選項的值抓出來比對，而不是在這裡重打一次字串（重打一次就等於用同一個
    假設驗同一個假設）。
    """
    page = Path("app/ui/web/settings_v1.html").read_text(encoding="utf-8")
    segment = re.search(r"seg\('permission',\s*\[(.*?)\]\)", page, re.S)
    assert segment, "頁面上找不到權限選項，這條會空轉"

    values = set(re.findall(r"\['([^']+)',", segment.group(1)))

    assert values, segment.group(1)
    assert values == set(PERMISSION), (
        f"頁面送的是 {sorted(values)}，對照表的鍵是 {sorted(PERMISSION)}"
    )
