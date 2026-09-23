"""批次重新解析 / 批次清除（ROADMAP P1）。

「批次刪除」聽起來是一個純粹的方便功能，但這裡有一格是陷阱，而且是最像
主角的那一格。

`document_update_status()` 有兩種「找不到」，畫面上都寫著檔案不見了：

| status | 原檔 | AICC 的副本 |
|---|---|---|
| `missing` | 沒了 | 沒有／也沒了 |
| `source_missing` | 沒了 | **還在** |

`source_missing` 是最像「該掃掉」的那一筆——它的標籤就是「原檔不存在」——
而它偏偏是**副本已經是世上僅存一份**的那一筆。一顆把它一起掃掉的批次鈕，
會安靜地把使用者唯一救得回來的東西從索引裡拿掉。

所以這個檔案大半在測「哪些**不會**被掃到」，而不是「哪些會」。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.documents_web_panel import DocumentsWebPanel  # noqa: E402


def _panel(tmp_path: Path) -> DocumentsWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    for name in ("a.md", "b.md", "c.md"):
        (project / name).write_text(f"# {name}\n內容。\n", encoding="utf-8")
    panel = DocumentsWebPanel(project)
    panel.store.rebuild_project_index()
    return panel


def _by_name(panel: DocumentsWebPanel) -> dict[str, int]:
    return {d.display_name: d.document_id for d in panel.store.list_documents()}


def _edit(panel: DocumentsWebPanel, name: str) -> None:
    source = Path(
        next(
            d.source_path
            for d in panel.store.list_documents()
            if d.display_name == name
        )
    )
    source.write_text("改過了。\n", encoding="utf-8")
    stamp = source.stat().st_mtime + 120
    os.utime(source, (stamp, stamp))


def _delete_source(panel: DocumentsWebPanel, name: str) -> None:
    Path(
        next(
            d.source_path
            for d in panel.store.list_documents()
            if d.display_name == name
        )
    ).unlink()


# --- 哪些會被掃到 -----------------------------------------------------------


def test_stale_is_exactly_the_edited_ones(tmp_path: Path) -> None:
    panel = _panel(tmp_path)
    ids = _by_name(panel)
    _edit(panel, "a.md")

    assert panel.store.stale_document_ids() == (ids["a.md"],)


def test_reparsing_the_stale_ones_makes_them_current_again(tmp_path: Path) -> None:
    """否則這顆按鈕只是把警告消音。"""
    panel = _panel(tmp_path)
    _edit(panel, "a.md")
    assert panel.store.stale_document_ids()

    panel.handle_action("reparse_stale", "")

    assert panel.store.stale_document_ids() == ()
    # 而且真的重讀了內容，不是只更新 mtime。
    assert any(
        "改過了" in chunk.content
        for chunk in panel.store.search("改過了")
    )


def test_dead_entries_are_swept(tmp_path: Path, monkeypatch) -> None:
    panel = _panel(tmp_path)
    ids = _by_name(panel)
    _delete_source(panel, "b.md")
    assert panel.store.dead_document_ids() == (ids["b.md"],)

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )
    panel.handle_action("remove_dead", "")

    assert "b.md" not in _by_name(panel)
    assert set(_by_name(panel)) == {"a.md", "c.md"}


# --- 哪些不會被掃到（這個檔案存在的理由）------------------------------------


def test_a_document_whose_only_surviving_copy_is_ours_is_never_swept(
    tmp_path: Path,
) -> None:
    """`source_missing`：原檔沒了，AICC 的副本還在。

    這是整個功能裡唯一一筆「刪掉就真的沒了」的資料，而它的標籤
    「原檔不存在」讀起來又最像該清掉的。批次動作必須跳過它。
    """
    panel = _panel(tmp_path)
    project = panel.store.project_path
    outside = tmp_path / "外部" / "合約.md"
    outside.parent.mkdir()
    outside.write_text("委任條款。\n", encoding="utf-8")
    panel.store.import_files([outside])

    # 原檔沒了，但匯入時複製的那一份還在 .aicc/imports。
    outside.unlink()

    # 匯入會在檔名前面加一段內容雜湊（同名不同內容才不會互蓋），所以比對結尾。
    imported = next(
        d for d in panel.store.list_documents() if d.display_name.endswith("合約.md")
    )
    assert panel.store.document_update_status(imported) == "source_missing", (
        "情境沒建立起來，這條會空轉"
    )
    assert (project / imported.stored_path).is_file(), "副本不在，測的就不是這件事"

    assert imported.document_id not in panel.store.dead_document_ids()
    assert imported.document_id not in panel.store.stale_document_ids()


def test_removing_in_bulk_deletes_no_files(tmp_path: Path) -> None:
    """`remove_documents` 的 docstring 說它不動檔案，這條讓那句話是可以被否證的。

    今天它只被「原檔和副本都沒了」的那條路徑呼叫，所以就算它偷偷刪檔也沒有
    東西可刪、沒有測試會紅。但它現在是一個公開方法，而 `remove_document` 的
    `delete_copy` 只差一個預設值。

    **這條第一版是用專案掃描來的檔案寫的，負向驗證判它沒有牙齒**：
    `remove_document` 的刪除分支只對 `source_type == "imported"` 而且位置在
    `.aicc/imported-context` 底下的檔案生效，所以拿掃描來的檔案去測，
    `delete_copy=True` 本來就什麼都不會刪——案例太順，破壞活不到量測那一刻。
    所以現在用的是匯入的文件：那才是這個旗標真的會動手的那一種。
    """
    panel = _panel(tmp_path)
    project = panel.store.project_path
    outside = tmp_path / "外部" / "報價.md"
    outside.parent.mkdir()
    outside.write_text("報價內容。\n", encoding="utf-8")
    panel.store.import_files([outside])

    imported = next(
        d for d in panel.store.list_documents() if d.display_name.endswith("報價.md")
    )
    copy = project / imported.stored_path
    assert copy.is_file(), "副本沒建立起來，這條會空轉"

    panel.store.remove_documents([imported.document_id, _by_name(panel)["a.md"]])

    assert not any(
        d.display_name.endswith("報價.md") for d in panel.store.list_documents()
    )
    assert copy.is_file(), "批次移除把 AICC 的副本刪掉了"
    assert outside.is_file(), "批次移除把使用者的原始檔刪掉了"
    assert (project / "a.md").is_file()


def test_a_healthy_document_is_in_neither_set(tmp_path: Path) -> None:
    # 反方向：修法不可以是「什麼都不掃」也不可以是「全部都掃」。
    panel = _panel(tmp_path)
    ids = _by_name(panel)
    _edit(panel, "a.md")
    _delete_source(panel, "b.md")

    assert ids["c.md"] not in panel.store.stale_document_ids()
    assert ids["c.md"] not in panel.store.dead_document_ids()
    # 而且兩個集合彼此不重疊。
    assert not set(panel.store.stale_document_ids()) & set(
        panel.store.dead_document_ids()
    )


def test_declining_the_dialog_removes_nothing(tmp_path: Path, monkeypatch) -> None:
    panel = _panel(tmp_path)
    _delete_source(panel, "b.md")

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No)
    )
    panel.handle_action("remove_dead", "")

    assert "b.md" in _by_name(panel)


def test_the_dialog_names_the_files_it_is_about_to_drop(
    tmp_path: Path, monkeypatch
) -> None:
    """「要刪掉 7 筆嗎」是沒有人回答得了的問題。

    也是這裡唯一能擋住「集合定義變寬」的東西：數量會照樣顯示得很合理，
    名字不會。
    """
    panel = _panel(tmp_path)
    _delete_source(panel, "b.md")
    seen: list[str] = []

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: (seen.append(a[2]), QMessageBox.No)[1]),
    )
    panel.handle_action("remove_dead", "")

    assert seen and "b.md" in seen[0], seen
    assert "不會動到你磁碟上的任何檔案" in seen[0]


def test_nothing_to_sweep_asks_nothing(tmp_path: Path, monkeypatch) -> None:
    # 一個問「要刪掉 0 筆嗎」的對話框，是使用者學會不讀對話框的原因。
    panel = _panel(tmp_path)
    asked: list[int] = []

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: (asked.append(1), QMessageBox.No)[1]),
    )
    panel.handle_action("remove_dead", "")

    assert not asked


# --- 推到畫面的數字 ---------------------------------------------------------


def test_the_counts_reach_the_page(tmp_path: Path) -> None:
    panel = _panel(tmp_path)
    _edit(panel, "a.md")
    _delete_source(panel, "b.md")

    batch = panel.collect_state()["batch"]

    assert batch == {"stale": 1, "dead": 1}


def test_the_counts_are_zero_when_there_is_nothing_to_do(tmp_path: Path) -> None:
    panel = _panel(tmp_path)

    assert panel.collect_state()["batch"] == {"stale": 0, "dead": 0}


def test_the_page_reads_the_counts(tmp_path: Path) -> None:
    """否則兩顆按鈕永遠是「沒有東西可做」的樣子。

    註解先剝掉：這一頁上一個 bug 就是斷言打在提到欄位的註解上，
    測試綠著而畫面什麼都沒接。
    """
    import re

    page = Path("app/ui/web/documents_v1.html").read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", page, flags=re.S))

    assert "DATA.batch" in code, "頁面沒有讀 batch"
    for act in ("reparse_stale", "remove_dead"):
        assert f'data-act="{act}"' in code, act
