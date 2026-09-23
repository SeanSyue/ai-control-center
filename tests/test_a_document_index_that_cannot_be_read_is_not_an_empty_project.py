"""索引讀不到的時候，文件面板不准畫成「這個專案還沒有文件」。

使用者看到的那個畫面本來是：文件面板一片空白，「0 份」，
「沒有符合條件的文件。」，兩顆批次按鈕灰掉——**沒有錯誤訊息、沒有紅色徽章、
沒有 toast、沒有任何一行紀錄**。他的合理結論是「我還沒放文件」，然後就不會
去追為什麼每一輪 prompt 都沒有帶到專案資料。

而那個畫面跟一個乾淨的空專案**逐欄位一模一樣**：
`collect_state()` 的 `except` 回的就是 `{"docs": []}`，
`_batch_counts()` 回 `{"stale": 0, "dead": 0}`，`_selection_note()` 回 `""`。
三個「讀不到」被寫成三個「沒有東西」。

這條路真的走得到：面板開著的時候 `index.sqlite` 被刪掉、被還原、或被防毒
軟體隔離，下一次讀就是 `no such table: documents`。這裡用直接 `DROP TABLE`
重現同一個例外——刪檔在 Windows 上會被 sqlite 自己的連線鎖住，而**測試要
重現的是那個例外，不是那個刪除動作**。

## 這條檢查擋不住什麼

擋不住「畫面把這件事畫得夠不夠清楚」。紅色橫幅住在
`AICC-視覺概念/documents_v1.html`（設計端的檔案），這一份守的是引擎這一側：
狀態不准跟健康狀態長得一樣，而且**使用者一定要被告知一次**。
橫幅接上之後，這幾條斷言一條都不用改。

也擋不住 schema 漂移那一種（未來加一個欄位 → `_row_to_document` 丟
`IndexError` → 同一個症狀）。那要的是索引的版本號與 migration，
`project_knowledge.py` 現在兩者都沒有——見 `docs/WHY.md` 同名那一則。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.documents_web_panel import DocumentsWebPanel  # noqa: E402


def _panel(tmp_path: Path, with_documents: bool) -> DocumentsWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir(parents=True)
    ProjectRegistry.initialize_project(project)
    if with_documents:
        (project / "note.md").write_text("# 標題\n費率是 6%。\n", encoding="utf-8")
    panel = DocumentsWebPanel(project)
    panel.store.rebuild_project_index()
    # 面板在真的 App 裡是載完頁面才開始推狀態的；不打開這個旗標，
    # `push_state()` 會直接 return，而這份檔案要驗的正是它。
    panel._page_ready = True
    return panel


def _break_the_index(panel: DocumentsWebPanel) -> None:
    """把索引弄成「讀得到檔案、讀不到資料」——防毒隔離之後的那個形狀。"""
    with sqlite3.connect(panel.store.database_path) as connection:
        connection.execute("DROP TABLE documents")


def test_a_broken_index_does_not_look_like_a_project_with_no_documents(
    tmp_path: Path,
) -> None:
    """壞掉的索引與乾淨的空專案，推給畫面的東西不准一模一樣。

    改壞的樣子：把 `collect_state()` 的 `except` 改回 `return {"docs": []}`，
    或把 `_batch_counts()` 的 `except` 改回 `{"stale": 0, "dead": 0}`。
    """
    empty = _panel(tmp_path / "a", with_documents=False)
    broken = _panel(tmp_path / "b", with_documents=True)
    _break_the_index(broken)

    clean_state = empty.collect_state()
    broken_state = broken.collect_state()

    assert clean_state["batch"] == {"stale": 0, "dead": 0}, (
        "乾淨的空專案應該還是回得出兩個 0——它是真的沒有東西要處理。"
        f"實際上是 {clean_state['batch']!r}"
    )
    dropped = set(clean_state) - set(broken_state)
    assert not dropped, (
        f"讀不到的時候整個欄位不見了：{sorted(dropped)}。\n"
        "沒推的欄位在頁面那一側等於「沒說」——它會沿用上一次的值或頁面自己的"
        "示範值，而使用者分不出那是不是真的。要說「讀不到」就要有一個欄位在"
        "那裡說。"
    )
    assert broken_state != clean_state, (
        "索引讀不到的時候，推給畫面的東西跟一個乾淨的空專案完全一樣：\n"
        f"  {broken_state!r}\n使用者會讀成「這個專案還沒有文件」。"
    )
    assert broken_state["batch"] is None, (
        "讀不到的批次數量被寫成 0 了——那兩顆按鈕會停用得跟「今天真的沒事要做」"
        f"一模一樣。實際上是 {broken_state['batch']!r}"
    )


def test_the_user_is_told_when_the_index_cannot_be_read(tmp_path: Path) -> None:
    """索引讀不到的時候，使用者一定要被告知一次——不是只留在 payload 裡。

    改壞的樣子：把 `DocumentsWebPanel.push_state()` 整支拿掉（回到基底那一支），
    使用者就會回到「一片空白、什麼都沒說」的那個畫面。
    """
    panel = _panel(tmp_path, with_documents=True)
    said: list[str] = []
    panel.action_failed.connect(said.append)

    panel.push_state()
    assert said == [], f"索引好好的時候不該說話，卻說了：{said}"

    _break_the_index(panel)
    panel.push_state()

    assert said, "索引讀不到，而畫面上一個字都沒有出現。"
    assert "no such table" in said[0], (
        f"訊息裡沒有講出到底是什麼讀不到：{said[0]!r}"
    )


def test_the_same_failure_is_not_shouted_on_every_repaint(tmp_path: Path) -> None:
    """同一個失敗只講一次。

    面板每按一個動作就推一次狀態。每一次都跳一個對話框的話，使用者會學會
    直接關掉它——而那正是需要它講話的那一次會被關掉的原因。
    """
    panel = _panel(tmp_path, with_documents=True)
    said: list[str] = []
    panel.action_failed.connect(said.append)

    _break_the_index(panel)
    panel.push_state()
    panel.push_state()
    panel.push_state()

    assert len(said) == 1, f"同一個失敗講了 {len(said)} 次：{said}"


def test_a_healthy_index_stays_quiet_after_it_recovers(tmp_path: Path) -> None:
    """修好之後不准繼續報錯。

    一個修好之後還在報錯的畫面，跟一個沒修好的畫面一樣不能信。
    """
    panel = _panel(tmp_path, with_documents=True)
    _break_the_index(panel)
    panel.push_state()
    assert panel._index_error is not None

    # 使用者把被隔離的檔案還原之後，面板下一次重新開這個專案就會重建 schema。
    # 用 store 這一層而不是 `panel.set_project()`：後者自己會把旗標清成 None，
    # 那樣這條測試就變成在驗它自己。
    panel.store.set_project(panel.store.project_path)
    panel.store.rebuild_project_index()
    panel.push_state()

    assert panel._index_error is None, (
        f"索引已經重建好了，面板還記著上一次的失敗：{panel._index_error!r}"
    )
    assert panel.collect_state()["batch"] is not None, (
        "重建之後這一格應該又是一組真的數字，而不是「讀不到」"
    )


# --- 三個各自會失敗的讀，一個一個問 ------------------------------------------
#
# 上面那幾條走的是「整份清單都讀不到」那條路（`list_documents()` 就炸了），
# 所以底下三個讀根本沒有被執行到。而它們**各自會失敗**：片段搜尋讀的是
# `chunks`，批次數量還要走一趟磁碟看原始檔還在不在。
#
# 用 monkeypatch 讓那一格單獨丟例外，不是去找一種剛好只弄壞那一格的 sqlite
# 損壞法：**要驗的是「這一格讀不到的時候怎麼說」**，不是某一種損壞法的重現。

def _make_it_fail(panel: DocumentsWebPanel, method: str) -> None:
    def boom(*_args, **_kwargs):
        raise sqlite3.DatabaseError(f"no such table: {method}")

    setattr(panel.store, method, boom)


def test_excerpts_that_cannot_be_read_are_not_reported_as_no_excerpts(
    tmp_path: Path,
) -> None:
    """片段搜尋讀不到的時候，那份文件底下不准畫成「沒有相關片段」。

    使用者看到的差別：一份**明明有內容**的文件，證據清單那一區空白。
    他的結論是「這份文件跟這次任務無關」，於是不會去追為什麼 prompt 裡沒有它。

    改壞的樣子：把 `_hits_by_document()` 的 `except` 改回 `return {}`。
    """
    panel = _panel(tmp_path, with_documents=True)
    said: list[str] = []
    panel.action_failed.connect(said.append)
    _make_it_fail(panel, "search")

    panel.push_state()
    state = panel.collect_state()

    assert state["docs"], "這個專案是有文件的，前提就壞了"
    assert all(doc["hits"] is None for doc in state["docs"]), (
        "片段讀不到卻被寫成空清單——畫面上跟「這份文件沒有相關片段」"
        f"一模一樣：{[doc['hits'] for doc in state['docs']]!r}"
    )
    assert said, "片段讀不到，而畫面上一個字都沒有出現。"


def test_batch_counts_that_cannot_be_read_are_not_reported_as_nothing_to_do(
    tmp_path: Path,
) -> None:
    """批次數量讀不到的時候，那兩顆按鈕不准停用得像「今天沒事要做」。

    使用者看到的差別：「重新解析過期的」與「清掉找不到的」兩顆是灰的。
    灰掉在這一頁的意思一直是「沒有東西要處理」——而真相是那個數字根本沒算出來。

    改壞的樣子：把 `_batch_counts()` 的 `except` 改回 `{"stale": 0, "dead": 0}`。
    """
    panel = _panel(tmp_path, with_documents=True)
    said: list[str] = []
    panel.action_failed.connect(said.append)
    _make_it_fail(panel, "stale_document_ids")

    panel.push_state()

    assert panel.collect_state()["batch"] is None, (
        "數不出來被寫成 0 了——那兩顆按鈕會停用得跟「今天真的沒事要做」一樣"
    )
    assert said, "批次數量讀不到，而畫面上一個字都沒有出現。"


def test_a_selection_note_that_cannot_be_read_is_not_silence(tmp_path: Path) -> None:
    """「為什麼是這些片段」讀不到的時候，不准安靜地不講。

    那一行本來就只在有話要說的時候出現，所以**它不見的樣子跟正常的一輪
    一模一樣**——這是這三個裡面最容易被吞掉的一個。

    改壞的樣子：把 `_selection_note()` 的 `except` 改回 `return ""`。
    """
    panel = _panel(tmp_path, with_documents=True)
    said: list[str] = []
    panel.action_failed.connect(said.append)
    _make_it_fail(panel, "selection_note")

    panel.push_state()

    assert panel.collect_state()["note"] is None, (
        "讀不到被寫成空字串了——畫面上跟「這一輪沒有話要說」一模一樣"
    )
    assert said, "選片段的理由讀不到，而畫面上一個字都沒有出現。"
