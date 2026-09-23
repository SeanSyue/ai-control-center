"""匯出決策紀錄的時候，完整性檢查的結果要說出來（ROADMAP P8）。

`verify()` 在每一次匯出都會跑，結果也寫進了匯出的 markdown。但**匯出完成的
對話框只講檔名和位置**——於是一份雜湊鏈斷掉的紀錄，匯出當下看到的是一個打勾的
「已匯出」，要自己去打開檔案才會發現。

會在意這件事的那一刻，正是「我要把這個檔案交給別人」的那一刻：稽核、客戶、
律師。**雜湊鏈存在的唯一理由就是證明沒有被動過手腳，那個結論必須出現在交出去
之前，不是藏在交出去的東西裡面。**

`DecisionExportResult` 一直帶著 `verified` / `unchained` / `broken_at` 三個
數字，只是沒有人讀——跟這個專案今天挖到的其他幾個是同一種。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.services.decision_export import DecisionExportService  # noqa: E402
from app.services.long_task import DecisionLogEntry, DecisionLogStore  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.audit_web_panel import AuditWebPanel  # noqa: E402


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)
    return project


def _log(project: Path, count: int = 2) -> DecisionLogStore:
    store = DecisionLogStore(project)
    for index in range(count):
        store.append(
            DecisionLogEntry(
                run_id=f"run-{index}",
                action="ASK_USER",
                question=f"要覆寫 demo{index}.py 嗎？",
            )
        )
    return store


def _tamper(store: DecisionLogStore) -> None:
    lines = store.path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["question"] = "一個比較好看的問題"
    lines[0] = json.dumps(first, ensure_ascii=False)
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _shown(monkeypatch) -> dict[str, list[tuple[str, str]]]:
    """Capture which dialog was used, not just what it said.

    綠色打勾旁邊寫著「第 12 筆對不上」，兩個訊號互相抵消，而人記得的是那個
    打勾。所以「用哪一種對話框」本身就是要驗的東西。
    """
    seen: dict[str, list[tuple[str, str]]] = {"information": [], "warning": []}
    for kind in seen:
        monkeypatch.setattr(
            QMessageBox,
            kind,
            staticmethod(
                lambda *a, _k=kind, **kw: seen[_k].append((a[1], a[2]))
            ),
        )
    return seen


def _export(app, tmp_path, project, monkeypatch):
    panel = AuditWebPanel(project)
    result = DecisionExportService(project).export(tmp_path / "out")
    panel._report_export(result)
    return panel, result


def test_the_real_export_action_reports_the_chain(app, tmp_path, monkeypatch) -> None:
    """走完整條路：`handle_action("export_confirm")` → 對話框。

    上面每一條都直接呼叫 `_report_export()`，所以把呼叫點換回舊訊息，
    **它們全部照樣綠**——負向驗證判它沒有牙齒。今天第三次犯同一種：
    驗了方法，沒驗呼叫它的那一行。
    """
    from PySide6.QtWidgets import QFileDialog

    project = _project(tmp_path)
    _tamper(_log(project, 2))
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out))
    )
    seen = _shown(monkeypatch)

    AuditWebPanel(project).handle_action("export_confirm", "交給稽核")

    assert seen["warning"], f"真正的匯出路徑沒有回報雜湊鏈：{seen}"
    assert "指紋對不上" in seen["warning"][0][1]


# --- 好的那條路 --------------------------------------------------------------


def test_a_clean_export_says_how_many_verified(app, tmp_path, monkeypatch) -> None:
    project = _project(tmp_path)
    _log(project, 3)
    seen = _shown(monkeypatch)

    _export(app, tmp_path, project, monkeypatch)

    assert seen["information"], seen
    assert not seen["warning"], seen
    title, body = seen["information"][0]
    assert "已匯出" in title
    assert "3 筆" in body, body
    assert "通過完整性驗證" in body, body


def test_the_filenames_are_still_there(app, tmp_path, monkeypatch) -> None:
    # 反方向：加了驗證結果不可以把原本有用的東西擠掉。
    project = _project(tmp_path)
    _log(project)
    seen = _shown(monkeypatch)

    _, result = _export(app, tmp_path, project, monkeypatch)

    body = seen["information"][0][1]
    assert result.markdown_path.name in body
    assert result.jsonl_path.name in body


# --- 斷掉的那條路 ------------------------------------------------------------


def test_a_broken_chain_is_a_warning_not_a_tick(app, tmp_path, monkeypatch) -> None:
    """這個檔案存在的理由。"""
    project = _project(tmp_path)
    _tamper(_log(project, 3))
    seen = _shown(monkeypatch)

    _, result = _export(app, tmp_path, project, monkeypatch)

    assert result.broken_at is not None, "情境沒建立起來，這條會空轉"
    assert not seen["information"], "雜湊鏈斷掉卻給了一個打勾的『已匯出』"
    assert seen["warning"], seen
    title, body = seen["warning"][0]
    assert "沒有通過" in title, title
    # 第幾筆要講出來——「有問題」而不說哪裡，接下來沒有人知道要看哪。
    assert f"第 {result.broken_at + 1} 筆" in body, body
    assert "交出去之前" in body, body


def test_the_broken_message_still_says_where_the_files_are(
    app, tmp_path, monkeypatch
) -> None:
    # 檔案還是寫出來了（刻意的：擋住匯出會讓人沒有東西可以拿去查原因）。
    project = _project(tmp_path)
    _tamper(_log(project, 2))
    seen = _shown(monkeypatch)

    _, result = _export(app, tmp_path, project, monkeypatch)

    body = seen["warning"][0][1]
    assert result.markdown_path.name in body


# --- 「無法驗證」不是「驗證失敗」---------------------------------------------


def test_entries_from_before_the_chain_are_not_reported_as_damage(
    app, tmp_path, monkeypatch
) -> None:
    """指紋機制上線前寫的紀錄本來就沒有指紋。

    把它算成問題，會讓每一次匯出都帶著一個永遠不會消失的警告——而一個永遠
    在的警告，就是使用者學會忽略警告的方式。
    """
    project = _project(tmp_path)
    store = _log(project, 1)
    # 手動補一筆沒有指紋的（機制上線前的形狀）。
    legacy = {"run_id": "old", "action": "ASK_USER", "question": "舊的"}
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(legacy, ensure_ascii=False) + "\n")
    seen = _shown(monkeypatch)

    _, result = _export(app, tmp_path, project, monkeypatch)

    assert result.unchained >= 1, "情境沒建立起來"
    assert result.broken_at is None
    assert seen["information"] and not seen["warning"], seen
    body = seen["information"][0][1]
    assert "無法驗證" in body, body
    assert "指紋機制上線前" in body, body


def test_a_fully_chained_export_does_not_mention_unverifiable_entries(
    app, tmp_path, monkeypatch
) -> None:
    # 每次都出現的句子會被讀成裝飾，然後在真的有舊資料的那次沒有人看見。
    project = _project(tmp_path)
    _log(project, 2)
    seen = _shown(monkeypatch)

    _export(app, tmp_path, project, monkeypatch)

    assert "無法驗證" not in seen["information"][0][1]
