"""Python 現在會推給畫面的所有東西，壓成一個指紋。

`negative_control.py` 用它回答一個它原本答不出來的問題：**這個破壞有沒有改變
使用者看得到的東西？**

為什麼需要這個。負向驗證目前只回答「有測試變紅嗎」，而紅燈有兩種：

1. 改壞了會被看到的行為 → 測試變紅。**這是想要的。**
2. 改壞了**沒有人讀的東西** → 測試也變紅，而畫面一模一樣。

第二種是假警報。它最純粹的例子是 `MainWindow.decision_notification_policy()`
——一個把設計決策當 dict 回傳的函式，唯一的讀者是斷言那個 dict 的測試。
把 `popup_ever` 改成 `True`，測試會紅，而畫面上不會有任何一個像素不同。

**這支工具產生的是觀察，不是判決。** 這一點是刻意的：

* 「畫面沒變」**不等於**那條測試沒有價值。安全掃描器、prompt 組裝、備份路徑
  ——這些東西改壞了都不會出現在 `collect_state()` 裡，而它們比畫面重要。
  那種紅燈是對的紅燈。
* 所以指紋只被印在判決旁邊當附註。判決還是紅／綠／壞三種，規則沒有動。

它涵蓋的範圍就是這句話的字面意思：**七個內容面板的 `collect_state()` 加上
外框的狀態**。看不到的東西：磁碟上寫了什麼、送給 CLI 的 prompt、通知有沒有
發出去、CSS。

用法（`--root` 要固定，指紋才可比）：

    .\\.venv\\Scripts\\python.exe tools\\ui_payload_digest.py --root <一個資料夾>

印出一行 sha256。算不出來就印 `UNAVAILABLE: <原因>` 並以 1 離開——
**算不出來不是「沒有變化」**，那個區別跟這支工具存在的理由是同一個。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _payloads(root: Path) -> dict[str, object]:
    """每一頁與外框現在會收到的狀態。

    固定同一個空專案與同一個 APPDATA，指紋才是可比的。所以這裡刻意**不做
    任何欄位正規化**：每一次「把某個欄位從指紋裡挑掉」都是一次讓真正的改動
    藏起來的機會，而固定輸入已經讓時間戳與路徑穩定下來了。

    釘完要還原。這支工具當指令跑的時候，行程結束就沒事了；但
    `tests/test_negative_control_sees_the_screen.py` 是**在同一個行程裡**
    把它 import 進來呼叫的，所以那一行 `os.environ[...] = ...` 會活到那支
    測試結束之後——之後每一支測試看到的 APPDATA 都是這裡的資料夾，而不是
    `tests/conftest.py` 的沙箱。兩個都是暫存資料夾，所以不會有任何東西變紅
    （2026-08-21 由 `tests/test_your_real_config_stays_yours.py` 抓到）。
    """
    previous_platform = os.environ.get("QT_QPA_PLATFORM")
    previous_appdata = os.environ.get("APPDATA")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["APPDATA"] = str(root / "appdata")
    try:
        return _build_payloads(root)
    finally:
        _restore("QT_QPA_PLATFORM", previous_platform)
        _restore("APPDATA", previous_appdata)


def _restore(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


def _build_payloads(root: Path) -> dict[str, object]:

    from PySide6.QtWidgets import QApplication

    from app.services.project_registry import ProjectRegistry
    from app.ui.audit_web_panel import AuditWebPanel
    from app.ui.dashboard_web_panel import DashboardWebPanel
    from app.ui.documents_web_panel import DocumentsWebPanel
    from app.ui.knowledge_web_panel import KnowledgeWebPanel
    from app.ui.lineup_web_panel import LineupWebPanel
    from app.ui.main_window import MainWindow
    from app.ui.roadmap_web_panel import RoadmapWebPanel
    from app.ui.settings_web_panel import SettingsWebPanel

    QApplication.instance() or QApplication([])
    project = root / "project"
    (project / ".aicc").mkdir(parents=True, exist_ok=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)

    # 專案清單同一個理由：陣容頁最上面那個搜尋框（2026-08-27 改成搜專案）
    # 會讀這台機器上登記過的專案，那會讓指紋跟著「他電腦上有幾個專案」動，
    # 而這裡要量的是**程式**變了沒有。跟下面那段中和本機對話同一件事。
    _real_load = ProjectRegistry.load
    ProjectRegistry.load = lambda self: ([], "")  # type: ignore[method-assign]
    try:
        return _build_with_panels(project, root)
    finally:
        ProjectRegistry.load = _real_load  # type: ignore[method-assign]


def _build_with_panels(project: Path, root: Path) -> dict[str, object]:
    from app.ui.audit_web_panel import AuditWebPanel
    from app.ui.dashboard_web_panel import DashboardWebPanel
    from app.ui.documents_web_panel import DocumentsWebPanel
    from app.ui.knowledge_web_panel import KnowledgeWebPanel
    from app.ui.lineup_web_panel import LineupWebPanel
    from app.ui.main_window import MainWindow
    from app.ui.roadmap_web_panel import RoadmapWebPanel
    from app.ui.settings_web_panel import SettingsWebPanel

    out: dict[str, object] = {}
    panels = [
        AuditWebPanel(project),
        DocumentsWebPanel(project),
        KnowledgeWebPanel(project),
        LineupWebPanel(project),
        SettingsWebPanel(project),
        DashboardWebPanel(),
        RoadmapWebPanel(),
    ]
    for panel in panels:
        # 陣容頁的 collect_state 會掃這台機器上所有本機對話：那會讓指紋跟著
        # 機器上有幾段對話變動，而這裡要量的是**程式**變了沒有。
        if hasattr(panel, "_scanned"):
            panel._scanned = True
            panel._hits = {"claude": [], "codex": []}
        out[panel.PAGE] = panel.collect_state()

    window = MainWindow(project)
    try:
        out["shell"] = window._shell_state()
    finally:
        window.deleteLater()
    return out


def digest(root: Path) -> str:
    payloads = _payloads(root)
    canonical = json.dumps(payloads, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="固定的工作資料夾")
    args = parser.parse_args(argv)
    try:
        print(digest(Path(args.root)))
    except BaseException as exc:  # noqa: BLE001
        # 包含 SystemExit 與匯入期的錯誤：破壞可能讓 app 根本起不來，
        # 而那要回報成「量不到」，不能回報成「沒有變化」。
        print(f"UNAVAILABLE: {type(exc).__name__}: {exc}"[:300])
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # stderr 也要：traceback 走那一條，只設 stdout 的話它會在印出
    # 崩潰訊息的路上再崩潰一次，而使用者拿到一個沒頭沒尾的錯誤。
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
