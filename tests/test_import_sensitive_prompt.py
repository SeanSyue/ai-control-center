"""匯入敏感文件前的提示（ROADMAP P1）。

匯入不只是複製檔案：內容會進入搜尋索引，之後有機會被選進送給 Claude／Codex 的
context。**那是一條從「我硬碟上的一個檔案」到「送給廠商的一段文字」的路**，
而在這之前，那條路上一句話都沒有。

這個檔案的斷言分兩邊，而第二邊比較容易寫錯：
* 有機密的檔案有沒有被攔下來
* **乾淨的檔案有沒有照樣進去**。一次匯入十個檔案、因為其中一個被攔就全部不進，
  會教會使用者直接按「全部匯入」——那正是這個功能想避免的結果。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.project_knowledge import ProjectKnowledgeStore

SECRET = "ANTHROPIC_API_KEY=sk-ant-0123456789abcdefghijklmnop\n"
CLEAN = "這份文件講的是設定頁的儲存列怎麼修。\n"


def make_store(tmp_path: Path) -> ProjectKnowledgeStore:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    return ProjectKnowledgeStore(project)


def outside(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "elsewhere" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ---- 攔下來 ---------------------------------------------------------------


def test_a_file_with_a_secret_is_held_back(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    path = outside(tmp_path, "config.md", SECRET)

    result = store.import_files([path])

    assert result.imported == ()
    assert [item.name for item in result.flagged] == ["config.md"]
    assert result.flagged[0].findings, "要說出是哪一種發現，不能只說有問題"


def test_a_held_back_file_is_not_in_the_index(tmp_path: Path) -> None:
    """The half that actually matters.

    Reporting it as flagged while indexing it anyway would be the worst of
    both: a warning that reads as 「已擋下」 over content that is now one
    relevance match away from a prompt.
    """
    store = make_store(tmp_path)
    store.import_files([outside(tmp_path, "config.md", SECRET)])

    assert store.list_documents() == []
    assert store.search("ANTHROPIC_API_KEY") == []


def test_flagged_is_separate_from_skipped(tmp_path: Path) -> None:
    # Different events. `skipped` means 「這不是一個可以匯入的檔案」; merging
    # them would let a withheld secret read as a broken path.
    store = make_store(tmp_path)

    result = store.import_files(
        [outside(tmp_path, "config.md", SECRET), tmp_path / "nope.md"]
    )

    assert [item.name for item in result.flagged] == ["config.md"]
    assert len(result.skipped) == 1
    assert "nope.md" in result.skipped[0]


# ---- 放行（這邊更容易寫錯） ------------------------------------------------


def test_the_clean_files_in_the_same_batch_still_go_in(tmp_path: Path) -> None:
    """Holding a whole folder hostage over one file teaches people to click
    「全部匯入」 without reading."""
    store = make_store(tmp_path)

    result = store.import_files(
        [
            outside(tmp_path, "notes.md", CLEAN),
            outside(tmp_path, "config.md", SECRET),
            outside(tmp_path, "spec.md", CLEAN),
        ]
    )

    assert sorted(result.imported) == ["notes.md", "spec.md"]
    assert [item.name for item in result.flagged] == ["config.md"]


def test_an_ordinary_document_is_not_flagged(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    result = store.import_files([outside(tmp_path, "notes.md", CLEAN)])

    assert result.imported == ("notes.md",)
    assert result.flagged == ()


def test_the_user_can_say_import_it_anyway(tmp_path: Path) -> None:
    """A project full of client documents will legitimately contain things
    that look like secrets. The decision is the user's; making it silently is
    what this stops."""
    store = make_store(tmp_path)
    path = outside(tmp_path, "config.md", SECRET)

    store.import_files([path], allow_flagged=True)

    # The stored copy carries a content-hash prefix, so match on the suffix -
    # the point is that it got in, not what the copy is called.
    names = [doc.display_name for doc in store.list_documents()]
    assert len(names) == 1 and names[0].endswith("config.md")


def test_the_safe_answer_is_the_one_you_get_by_not_thinking(
    tmp_path: Path,
) -> None:
    # `allow_flagged` defaults to False. A default of True would mean every
    # caller that had not heard of this feature keeps the old behaviour -
    # which is the behaviour the feature exists to change.
    import inspect

    signature = inspect.signature(ProjectKnowledgeStore.import_files)

    assert signature.parameters["allow_flagged"].default is False


# ---- 到得了使用者面前嗎 ---------------------------------------------------


def test_the_dialog_says_why_it_matters_not_just_what_was_found(
    tmp_path: Path, monkeypatch
) -> None:
    """「這個檔案有機密」 is a fact. 「匯入之後它可能被送出去」 is the reason.

    Without the second sentence the user has no basis to decide, and will
    answer yes because they know what is in their own file.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.ui import documents_web_panel as panel_module

    QApplication.instance() or QApplication([])
    store = make_store(tmp_path)
    panel = panel_module.DocumentsWebPanel()
    panel.store = store

    shown: dict[str, str] = {}

    def fake_question(parent, title, text, *args, **kwargs):
        shown["title"] = title
        shown["text"] = text
        return QMessageBox.No

    monkeypatch.setattr(panel_module.QMessageBox, "question", fake_question)
    panel._import([outside(tmp_path, "config.md", SECRET)])

    assert "config.md" in shown["text"]
    assert "送給" in shown["text"], "沒有講出「會被送給模型」這件事"
    assert "搜尋索引" in shown["text"]
    # And answering No leaves it out.
    assert store.list_documents() == []

    panel.deleteLater()


def test_saying_yes_imports_the_held_back_file(
    tmp_path: Path, monkeypatch
) -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.ui import documents_web_panel as panel_module

    QApplication.instance() or QApplication([])
    store = make_store(tmp_path)
    panel = panel_module.DocumentsWebPanel()
    panel.store = store

    monkeypatch.setattr(
        panel_module.QMessageBox,
        "question",
        lambda *a, **k: QMessageBox.Yes,
    )
    panel._import([outside(tmp_path, "config.md", SECRET)])

    names = [doc.display_name for doc in store.list_documents()]
    assert len(names) == 1 and names[0].endswith("config.md")

    panel.deleteLater()
