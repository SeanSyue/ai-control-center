"""接手清單有沒有真的走到畫面上（ROADMAP P5）。

`handover.py` 算得出「還缺什麼」，但這個專案最常出的 bug 就是**算出來了沒有
人讀**。所以這裡不驗服務層，驗兩個真的會被看到的地方：

  1. **匯入完成的那個對話框**——使用者唯一必然在看的時刻。
     單講「匯入了 37 個檔案」會被讀成「都好了」。
  2. **儀表板卡片**——會一直留著，直到真的做完。那個對話框關掉就沒了，
     而這幾件事沒有一件會在關掉之後自己完成。

再加一件：清單說得出問題，就要在說得出問題的地方能解決。四項裡只有「重建
索引」是 AICC 自己做得完的（git 要遠端、LINE 憑證只有人能輸入、新對話是在
講一件已經發生的事），所以只有那一項配一個「現在做」。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFileDialog,
    QMessageBox,
)

from app.services.project_knowledge import ProjectKnowledgeStore  # noqa: E402
from app.services.project_registry import ProjectEntry, ProjectRegistry  # noqa: E402
from app.services.workspace_portability import (  # noqa: E402
    WorkspacePortabilityService,
)
from app.ui.dashboard_web_panel import DashboardWebPanel  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    yield application


def _old_machine(tmp_path: Path, *, git: bool = True) -> Path:
    project = tmp_path / "old" / "案子"
    project.mkdir(parents=True)
    ProjectRegistry.initialize_project(project)
    (project / ".aicc" / "task.md").write_text("# Task\n做事。\n", encoding="utf-8")
    (project / "note.md").write_text("# 開戶\n護照。\n", encoding="utf-8")
    if git:
        (project / ".git").mkdir()
    ProjectKnowledgeStore(project).rebuild_project_index()
    return project


def _archive(tmp_path: Path, project: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    exported = WorkspacePortabilityService().export_project(project, out)
    assert exported.success, exported.errors
    return Path(exported.archive_path)


def _import_through_the_window(
    app, tmp_path, monkeypatch, *, answer=QMessageBox.No
) -> tuple[MainWindow, list[str], Path]:
    """Drive the real 匯入 flow and capture what the user is shown."""
    old = _old_machine(tmp_path)
    archive = _archive(tmp_path, old)
    landing = tmp_path / "new"
    landing.mkdir()

    home = tmp_path / "home"
    home.mkdir()
    ProjectRegistry.initialize_project(home)
    window = MainWindow(home)

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(archive), ""))
    )
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *a, **k: str(landing)),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *a, **k: shown.append(a[2])),
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: answer)
    )

    window.import_workspace()
    return window, shown, landing / "案子"


# --- 匯入完成的對話框 --------------------------------------------------------


def test_the_import_dialog_lists_what_is_still_missing(
    app, tmp_path, monkeypatch
) -> None:
    window, shown, _ = _import_through_the_window(app, tmp_path, monkeypatch)

    assert shown, "匯入完成連對話框都沒跳"
    message = shown[0]
    assert "接回版本歷史" in message, message
    assert "第一次執行會開新的對話" in message, message
    window.deleteLater()


def test_the_dialog_does_not_invent_rows_for_a_clean_import(
    app, tmp_path, monkeypatch
) -> None:
    """舊機沒有 git，就不該出現「接回版本歷史」。

    反方向的那一半：一條在不需要的時候也照樣出現的提醒，會把旁邊真的重要的
    那條一起訓練成雜訊。
    """
    old = _old_machine(tmp_path, git=False)
    archive = _archive(tmp_path, old)
    landing = tmp_path / "new"
    landing.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    ProjectRegistry.initialize_project(home)
    window = MainWindow(home)

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(archive), ""))
    )
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *a, **k: str(landing)),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2]))
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No)
    )

    window.import_workspace()

    assert shown
    assert "接回版本歷史" not in shown[0], shown[0]
    window.deleteLater()


def test_the_pending_rows_also_land_in_the_projects_log(
    app, tmp_path, monkeypatch
) -> None:
    # 對話框關掉就沒了。紀錄留著，而這幾件事不會在關掉之後自己完成。
    window, _, imported = _import_through_the_window(app, tmp_path, monkeypatch)

    lines = window._session_for(imported.resolve()).log_lines

    assert any("接回版本歷史" in line for line in lines), lines
    window.deleteLater()


# --- 儀表板卡片 --------------------------------------------------------------


def test_the_dashboard_card_carries_the_outstanding_rows(
    app, tmp_path, monkeypatch
) -> None:
    """卡片上的 `detail` 是「有才畫」的——頁面收到什麼 key 就畫什麼。"""
    window, _, imported = _import_through_the_window(app, tmp_path, monkeypatch)

    panel = DashboardWebPanel()
    panel.refresh([ProjectEntry(name=imported.name, path=str(imported))])
    entry = next(
        item
        for item in panel.collect_state()["projects"]
        if Path(item["id"]).name == imported.name
    )

    row = entry["detail"].get("接手待辦", "")
    assert row, entry["detail"]
    assert "接回版本歷史" in row
    # 列的是**還沒做的**，不是四項全列。做完的還留在上面，這一行就會從
    # 「還要做什麼」慢慢變成「搬家時發生過什麼」——然後沒有人再看它。
    # 索引這次是好的（WAL 修好之後就會跟過來），所以它不該出現。
    assert "重建文件索引" not in row, row
    window.deleteLater()
    panel.deleteLater()


def test_a_project_that_never_moved_has_no_row_on_its_card(
    app, tmp_path
) -> None:
    """絕大多數專案不是搬過來的。一個常駐的「接手待辦：—」會讓真的有待辦的
    那一次消失在背景裡。"""
    project = _old_machine(tmp_path)

    panel = DashboardWebPanel()
    panel.refresh([ProjectEntry(name=project.name, path=str(project))])
    entry = panel.collect_state()["projects"][0]

    assert "接手待辦" not in entry["detail"], entry["detail"]
    panel.deleteLater()


# --- 「現在做」---------------------------------------------------------------


def test_a_missing_index_can_be_rebuilt_from_the_import_dialog(
    app, tmp_path, monkeypatch
) -> None:
    """說得出問題的地方，就要能解決問題。

    四項裡只有這一項是 AICC 自己做得完的，所以只有這一項配「現在做」。
    """
    old = _old_machine(tmp_path)
    archive = _archive(tmp_path, old)
    landing = tmp_path / "new"
    landing.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    ProjectRegistry.initialize_project(home)
    window = MainWindow(home)

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(archive), ""))
    )
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *a, **k: str(landing)),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: (asked.append(a[1]), QMessageBox.Yes)[1]),
    )
    # 讓索引「沒跟過來」：匯入之後、算清單之前，把它清空。
    real_rebuild = ProjectKnowledgeStore.rebuild_project_index
    original_import = WorkspacePortabilityService.import_project

    def import_then_empty_the_index(self, archive_path, destination_dir):
        result = original_import(self, archive_path, destination_dir)
        store = ProjectKnowledgeStore(Path(result.project_path))
        for document in store.list_documents():
            store.remove_document(document.document_id)
        return result

    monkeypatch.setattr(
        WorkspacePortabilityService, "import_project", import_then_empty_the_index
    )
    monkeypatch.setattr(
        ProjectKnowledgeStore, "rebuild_project_index", real_rebuild
    )

    window.import_workspace()

    assert any("索引" in title for title in asked), asked
    imported = landing / "案子"
    assert ProjectKnowledgeStore(imported).list_documents(), "答了「要」卻沒有重建"
    window.deleteLater()


def test_no_rebuild_prompt_when_the_index_arrived_intact(
    app, tmp_path, monkeypatch
) -> None:
    # 索引正常跟過來時還多問一句，會讓這個問句變成匯入流程的固定台詞，
    # 然後真的缺的那一次沒有人會讀它。
    old = _old_machine(tmp_path)
    archive = _archive(tmp_path, old)
    landing = tmp_path / "new"
    landing.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    ProjectRegistry.initialize_project(home)
    window = MainWindow(home)

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(archive), ""))
    )
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *a, **k: str(landing)),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: (asked.append(a[1]), QMessageBox.No)[1]),
    )

    window.import_workspace()

    assert not any("索引" in title for title in asked), asked
    window.deleteLater()
