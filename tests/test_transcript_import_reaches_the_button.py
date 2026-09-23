"""「匯入舊對話」這顆按鈕真的有走到對話匯入器（ROADMAP P1）。

`CloudTranscriptImporter` 一直都在，也一直是對的：它把 ChatGPT／Claude 的
匯出轉成看得懂的 markdown、用內容雜湊去重、遇到不認得的格式會拒絕而不是
把 JSON 倒進索引。

**問題是沒有人呼叫它。** 它唯一的呼叫端是 `KnowledgePanel`——被網頁版取代掉的
原生面板，現在只留在 `main_window` 的 import 行上（帶著 `noqa`）當回退路徑，
從來沒有被建立。而網頁版的「匯入舊對話」跟「匯入檔案」共用同一條
`_import`，所以一份 ChatGPT 匯出檔會被當成一般檔案索引進去，長這樣：

    [ { "title": "境外開戶討論", "mapping": { "1": { "message": { "author": ...

對話內容散在 JSON 骨架裡，而按鈕照樣回報成功。

所以這裡每一條都從**面板**打進去，不從匯入器打。匯入器的測試從頭到尾都是綠的
——那正是問題：它驗的是一段沒有人會執行到的程式碼。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.documents_web_panel import DocumentsWebPanel  # noqa: E402

CHATGPT_EXPORT = [
    {
        "title": "境外開戶討論",
        "mapping": {
            "1": {
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["請問開戶需要哪些文件？"]},
                }
            },
            "2": {
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": ["需要護照與地址證明。"]},
                }
            },
        },
    }
]


def _panel(tmp_path: Path) -> DocumentsWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    panel = DocumentsWebPanel(project)
    panel.store.rebuild_project_index()
    return panel


def _export(tmp_path: Path, payload=CHATGPT_EXPORT) -> Path:
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _texts(panel: DocumentsWebPanel) -> str:
    return "\n".join(
        panel.store.document_preview(d.document_id)
        for d in panel.store.list_documents()
    )


def test_a_chatgpt_export_is_not_indexed_as_raw_json(tmp_path: Path) -> None:
    """The bug, stated as the thing the user would notice."""
    panel = _panel(tmp_path)

    panel._import_transcripts([_export(tmp_path)])

    body = _texts(panel)
    assert body.strip(), "什麼都沒有匯入，這條會空轉"
    assert '"mapping"' not in body, f"索引到的是原始 JSON：{body[:120]}"
    assert '"author"' not in body
    assert "請問開戶需要哪些文件？" in body
    assert "需要護照與地址證明。" in body


def test_the_button_is_wired_to_the_transcript_importer(
    tmp_path: Path, monkeypatch
) -> None:
    """走完整條路：檔案對話框 → handle_action → 匯入器。

    上一版的錯誤不在匯入器裡，而在**按鈕接到哪裡**，所以只驗
    `_import_transcripts` 會漏掉同一種錯再犯一次。
    """
    panel = _panel(tmp_path)
    export = _export(tmp_path)

    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(export)], "")),
    )
    panel.handle_action("import_transcript", "")

    body = _texts(panel)
    assert "請問開戶需要哪些文件？" in body
    assert '"mapping"' not in body


def test_importing_the_same_export_twice_adds_nothing(tmp_path: Path) -> None:
    # 內容雜湊去重也是那段沒人呼叫的程式碼的一部分。
    panel = _panel(tmp_path)
    export = _export(tmp_path)

    panel._import_transcripts([export])
    first = {d.display_name for d in panel.store.list_documents()}
    panel._import_transcripts([export])
    second = {d.display_name for d in panel.store.list_documents()}

    assert first == second, f"重複匯入多出了 {second - first}"


def test_a_plain_file_import_still_goes_the_ordinary_way(
    tmp_path: Path, monkeypatch
) -> None:
    """反方向：修法不可以是「所有匯入都走對話匯入器」。

    一份 .md 筆記走對話匯入器會被包成一則「對話」，那是另一種安靜的錯。

    **這條第一版是直接呼叫 `panel._import(...)`，負向驗證判它沒有牙齒**：
    要驗的錯在 `_run_action` 的分派，而直接呼叫剛好繞過那一行——破壞活不到
    量測那一刻。所以現在跟上面那條走同一條路，從 `handle_action` 進去。
    """
    panel = _panel(tmp_path)
    note = tmp_path / "筆記.md"
    note.write_text("# 標題\n這是一般筆記。\n", encoding="utf-8")

    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([str(note)], ""))
    )
    panel.handle_action("import_files", "")

    documents = panel.store.list_documents()
    assert any(d.display_name.endswith("筆記.md") for d in documents), [
        d.display_name for d in documents
    ]
    # 對話匯入器會把它改寫成一則有 provider／標題的對話並放進 transcripts/，
    # 所以「副本不在 transcripts 底下」才是這條真正在守的東西。
    assert not any(
        "transcripts" in (d.stored_path or "") for d in documents
    ), "一般檔案被當成對話匯入了"
    assert "這是一般筆記。" in _texts(panel)


def test_a_transcript_with_secrets_asks_before_indexing(
    tmp_path: Path, monkeypatch
) -> None:
    """兩顆匯入鈕必須共用同一個「機密」的定義。

    對話匯出檔是整台機器上最可能夾帶客戶資料的一種檔案——它就是使用者跟
    模型講過的話。如果它反而是唯一跳過掃描的那條路，那個提示等於白做。
    """
    panel = _panel(tmp_path)
    secret = _export(
        tmp_path,
        [
            {
                "title": "金鑰",
                "mapping": {
                    "1": {
                        "message": {
                            "author": {"role": "user"},
                            # 貼一把金鑰請模型幫忙看設定，是對話匯出檔裡
                            # 非常真實的一種內容。
                            "content": {
                                "parts": ["這是我的金鑰 sk-proj-A1b2C3d4E5f6G7h8"]
                            },
                        }
                    }
                },
            }
        ],
    )
    assert panel.store.inspect_import([secret]), "掃描器沒認出來，這條會空轉"

    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: (asked.append(a[2]), QMessageBox.No)[1]),
    )
    panel._import_transcripts([secret])

    assert asked, "夾帶機密的對話匯出檔沒有問就直接索引了"
    assert "送給 Claude／Codex 的 context" in asked[0]
    assert not panel.store.list_documents(), "答「不要」卻還是索引了"


def test_saying_yes_imports_the_flagged_transcript(
    tmp_path: Path, monkeypatch
) -> None:
    # 另一方向：擋下來之後要真的還能匯入，否則那是硬擋不是提示。
    panel = _panel(tmp_path)
    secret = _export(
        tmp_path,
        [
            {
                "title": "金鑰",
                "mapping": {
                    "1": {
                        "message": {
                            "author": {"role": "user"},
                            "content": {"parts": ["這是我的金鑰 sk-proj-A1b2C3d4E5f6G7h8"]},
                        }
                    }
                },
            }
        ],
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )
    panel._import_transcripts([secret])

    assert panel.store.list_documents()


def test_every_service_a_page_imports_is_actually_used_by_it() -> None:
    """守的是這個 bug 的**類別**，不是這一次。

    原本這條打在退役的 `KnowledgePanel` 上：它已經被網頁版取代、從來沒有被
    建立過，卻還 import 著服務層的東西——所以那些東西在 grep 裡看起來有人用。
    `CloudTranscriptImporter` 就是這樣躲了一段時間：**有呼叫端、有測試、全綠，
    而使用者按下按鈕永遠走不到它。**

    2026-08-23 那個面板砍掉了，所以這條改成打在**活著的那幾頁**上：
    一頁 import 進來的服務，它自己要真的用到。import 了卻沒用到的那一個，
    就是下一個「看起來有人用」的東西。

    用 AST 不用 grep，而這不是講究：第一版是正規表示式，負向驗證判它沒有
    牙齒——因為 `_import_transcripts` 的 docstring 裡就寫著那個名字，
    **把 import 拔掉之後，那段解釋 bug 的文字自己撐住了檢查**。
    同一種洞這個專案犯過三次，共通點都是「提到某個東西」被當成
    「用了某個東西」。AST 只看得到真正的引用。
    """
    import ast

    problems = []
    for page in sorted(Path("app/ui").glob("*_web_panel.py")):
        tree = ast.parse(page.read_text(encoding="utf-8-sig"), str(page))
        imported = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("app.services")
            for alias in node.names
        }
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
        orphans = sorted(imported - used)
        if orphans:
            problems.append(f"{page.name}：{'、'.join(orphans)}")

    assert not problems, (
        "這幾頁 import 了服務卻沒有真的用到——那個服務在 grep 裡看起來有人用，"
        "而使用者按不到：\n  " + "\n  ".join(problems)
    )


def test_importing_transcripts_is_reachable_from_a_live_page() -> None:
    """而且那個具體的服務，今天真的接在一顆按得到的按鈕上。

    上一條守的是「不要有假的呼叫端」；這一條守的是「真的呼叫端還在」。
    兩條都要：只有前一條的話，把整段功能刪掉也會全綠。
    """
    import ast

    page = Path("app/ui/documents_web_panel.py")
    tree = ast.parse(page.read_text(encoding="utf-8-sig"), str(page))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "CloudTranscriptImporter" in called, (
        "沒有人建立 CloudTranscriptImporter——匯入對話紀錄那顆按鈕按下去走不到"
    )
    code = Path("app/ui/web/documents_v1.html").read_text(encoding="utf-8")
    assert 'data-act="import_transcript"' in code, "頁面上那顆按鈕不見了"

def test_the_result_goes_to_the_channel_that_is_not_titled_failed(
    tmp_path: Path,
) -> None:
    """`action_failed` 開的是一個標題「操作沒有完成」的警告框。

    成功的摘要送進那條，使用者會被告知匯入失敗了。兩條 signal 不能互換。
    """
    panel = _panel(tmp_path)
    reports: list[str] = []
    failures: list[str] = []
    panel.report_ready.connect(reports.append)
    panel.action_failed.connect(failures.append)

    panel._import_transcripts([_export(tmp_path)])

    assert reports, "匯入完成沒有任何回饋——按鈕看起來跟沒反應一樣"
    assert "context-only" in reports[0]
    assert not failures, failures
