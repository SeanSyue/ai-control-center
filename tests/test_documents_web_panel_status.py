"""A successfully indexed document must not be shown as failed.

Caught by opening the panel: three files the store reported as `indexed` with
no error were all drawn with a red 索引失敗 badge. The cause was a comparison
against `"ok"`, a value this schema never produces - `project_knowledge.py`
defaults the column to `'indexed'`.

Counting tests could not have found this. The right number of documents was
rendered, with the right names and sizes; only the status was wrong, and status
is the one field that tells the user whether their document will be used at
all.

**The same dict then produced the same class of bug a second time**, which is
why the second half of this file exists. There are *two* status axes here and
the original tests only ever exercised one:

  - `documents.status`   - did parsing work (`indexed` / `error` / `missing`)
  - `document_update_status()` - is the index still fresh
    (`current` / `updated` / `source_missing` / `missing`)

`STATUS_LABEL` translates the second one, and it was written with a key
`"changed"` that the store never returns - the real value is `"updated"`. So a
document whose original file had been edited fell through to the dict's default
and was drawn **mint 已索引**: the one state meaning 「送出去的內容是舊的」
looked exactly like 「一切正常」. The 有變更 filter chip could never match
anything either, because no document ever carried that key.

Every test above passed throughout, because they all only ever looked at
freshly-indexed files where the freshness status is `current`. A test that
covers one axis of a two-axis field reads as green for the axis it never
touches.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.documents_web_panel import DocumentsWebPanel  # noqa: E402

# The four badge keys documents_v1.html knows how to draw (its `ST` table).
# Anything else falls through to its own `||ST.indexed` default, so a key that
# is not in here reaches the user as mint 已索引 no matter what Python meant.
PAGE_STATUS_KEYS = {"indexed", "indexing", "changed", "failed"}


def _panel_with_documents(tmp_path: Path) -> DocumentsWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    (project / "note.md").write_text("# 標題\n內容。\n", encoding="utf-8")
    (project / "readme.txt").write_text("純文字內容。\n", encoding="utf-8")
    panel = DocumentsWebPanel(project)
    panel.store.rebuild_project_index()
    return panel


def _touch_source(panel: DocumentsWebPanel, name: str) -> None:
    """Make one document stale the way a user does: edit the original."""
    document = next(
        d for d in panel.store.list_documents() if d.display_name == name
    )
    source = Path(document.source_path)
    source.write_text("內容改過了。\n", encoding="utf-8")
    # Not decoration: the status compares mtime strings, and two writes inside
    # one filesystem tick produce the same one - the test would pass or fail
    # depending on how fast the disk is.
    stamp = source.stat().st_mtime + 120
    os.utime(source, (stamp, stamp))


def test_an_indexed_document_is_not_labelled_failed(tmp_path: Path) -> None:
    panel = _panel_with_documents(tmp_path)

    docs = panel.collect_state()["docs"]

    assert docs, "索引後沒有任何文件 —— 這條測試會空轉"
    for doc in docs:
        assert doc["status"] != "failed", f"{doc['name']} 被標成失敗：{doc['status_label']}"
        assert "失敗" not in doc["status_label"], doc


def test_the_store_and_the_panel_agree(tmp_path: Path) -> None:
    # The underlying mistake was the panel inventing a healthy value the store
    # never writes, so this compares the two directly rather than restating a
    # constant the panel could get wrong in the same way.
    panel = _panel_with_documents(tmp_path)

    stored = {d.display_name: d.status for d in panel.store.list_documents()}
    shown = {d["name"]: d["status"] for d in panel.collect_state()["docs"]}

    for name, status in stored.items():
        if status == "indexed":
            assert shown[name] != "failed", f"{name}: store={status} panel={shown[name]}"


def test_a_genuinely_failed_document_is_still_labelled_failed(tmp_path: Path) -> None:
    # The other direction, so the fix cannot be "never show failure".
    panel = _panel_with_documents(tmp_path)
    documents = panel.store.list_documents()
    assert documents

    import sqlite3

    with sqlite3.connect(panel.store.database_path) as connection:
        connection.execute(
            "UPDATE documents SET status = 'error', error = '解析失敗' WHERE id = ?",
            (documents[0].document_id,),
        )

    shown = {d["name"]: d for d in panel.collect_state()["docs"]}
    assert shown[documents[0].display_name]["status"] == "failed"


# --- the freshness axis ----------------------------------------------------


def test_an_edited_original_is_not_drawn_as_healthy(tmp_path: Path) -> None:
    """The bug this half of the file was written for.

    「原檔已變更」 means the excerpts the prompt carries are the old ones. If
    that renders identically to 已索引, the user has no way to know the answer
    they are about to get was built from a stale copy.
    """
    panel = _panel_with_documents(tmp_path)
    _touch_source(panel, "note.md")

    shown = {d["name"]: d for d in panel.collect_state()["docs"]}
    stale = shown["note.md"]

    assert panel.store.document_update_statuses() and any(
        s == "updated" for s in panel.store.document_update_statuses().values()
    ), "store 沒有回報 updated —— 這條測試會空轉，不是通過"
    assert stale["status"] != "indexed", (
        f"改過原檔的文件仍然畫成「已索引」：{stale['status_label']}"
    )
    # And the untouched one stays healthy, so the fix cannot be "warn on
    # everything".
    assert shown["readme.txt"]["status"] == "indexed"


def test_the_freshness_status_is_a_key_the_page_can_draw(tmp_path: Path) -> None:
    """Both halves of the original bug were 「頁面收到一個它不認得的值」.

    The page falls back to mint 已索引 for any key outside its own table, so
    an unrecognised key is not a missing badge - it is a *wrong* one. Pinning
    the key set here is what makes that unmissable next time.
    """
    panel = _panel_with_documents(tmp_path)
    _touch_source(panel, "note.md")

    for doc in panel.collect_state()["docs"]:
        assert doc["status"] in PAGE_STATUS_KEYS, doc

    shown = {d["name"]: d for d in panel.collect_state()["docs"]}
    # Specifically the key the 有變更 chip filters on - otherwise that chip is
    # a control that is permanently empty.
    assert shown["note.md"]["status"] == "changed"
    assert "變更" in shown["note.md"]["status_label"]


def test_a_status_the_panel_has_never_heard_of_does_not_look_fine(
    tmp_path: Path,
) -> None:
    """The guard against a third round of this.

    Both times, the failure was a store value with no entry in `STATUS_LABEL`,
    and both times the *default* is what turned a missing translation into a
    confident green badge. So the default itself is the thing under test: an
    unknown status has to reach the user as something visibly unresolved.
    """
    panel = _panel_with_documents(tmp_path)
    known = {d.document_id for d in panel.store.list_documents()}
    panel.store.document_update_statuses = lambda: {  # type: ignore[method-assign]
        doc_id: "quarantined" for doc_id in known
    }

    for doc in panel.collect_state()["docs"]:
        assert doc["status"] != "indexed", "沒見過的狀態被畫成「已索引」"
        assert doc["status"] in PAGE_STATUS_KEYS, doc
        # The raw value has to survive into the label: without it the report is
        # 「怪怪的」 with no way to find out what.
        assert "quarantined" in doc["status_label"], doc


def test_the_parse_error_reaches_the_badge(tmp_path: Path) -> None:
    """`status_label` was computed, pushed, and rendered by nobody.

    The page derived its badge text from its own `ST` table, so `doc.error` -
    the only place that says *why* a document failed - was dropped between
    Python and the screen. Every document that failed for any reason showed
    the same 索引失敗.
    """
    panel = _panel_with_documents(tmp_path)
    documents = panel.store.list_documents()

    import sqlite3

    with sqlite3.connect(panel.store.database_path) as connection:
        connection.execute(
            "UPDATE documents SET status = 'error', error = ? WHERE id = ?",
            ("PDF 沒有可擷取的文字層", documents[0].document_id),
        )

    shown = {d["name"]: d for d in panel.collect_state()["docs"]}
    assert shown[documents[0].display_name]["status_label"] == "PDF 沒有可擷取的文字層"

    # And it has to survive the last hop.
    #
    # This assertion took two goes to get teeth, both times for the same
    # reason. Draft 1 searched the whole file and matched the *contract
    # comment* that names the field - the exact thing that was true while
    # nothing rendered it. Draft 2 narrowed to `docHTML` and matched the
    # comment I had just written *inside* `docHTML` explaining the fix.
    # `negative_control.py` reported 綠 ✗ 沒有牙齒 both times: deleting the
    # line that reads the field left every test passing.
    #
    # So the comments come out first. A field is rendered when the *code*
    # mentions it; prose about the field is what a stale assertion feeds on.
    page = Path("app/ui/web/documents_v1.html").read_text(encoding="utf-8")
    assert "function docHTML(" in page, "頁面結構變了，這條的切法要跟著改"
    body = page.split("function docHTML(")[-1].split("\nfunction ")[0]
    code = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", body, flags=re.S))
    assert "status_label" in code, (
        "documents_v1.html 收了 status_label 卻沒有畫出來"
    )
