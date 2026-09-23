"""搬過來之後還剩什麼要做（ROADMAP P5「新電腦接手 wizard」）。

沒做完的那幾件事**沒有一件會自己出聲**：沒有 git 歷史、LINE 不會通知、
第一次執行是全新的對話。都是安靜地少了一塊。

這個檔案大半在測**哪些項目不該出現**，而不是哪些該出現。理由是那條會先壞：

  一個專案本來就不是 git repo，卻每次都被提醒「歷史沒跟過來」——那條提醒會
  連帶把旁邊真的重要的那條一起訓練成雜訊。

所以「這個專案本來就沒有」和「東西沒跟過來」一定要分得開，而分得開的唯一
辦法是**在舊機上就把事實記下來**（`had_git`、`line_notify_configured` 寫進
壓縮檔的 manifest），不是在新機上猜。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services.handover import (  # noqa: E402
    handover_marker,
    handover_steps,
    outstanding,
)
from app.services.line_notifier import LineNotifier  # noqa: E402
from app.services.project_knowledge import ProjectKnowledgeStore  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from app.services.workspace_portability import (  # noqa: E402
    WorkspacePortabilityService,
)


def _old_machine(tmp_path: Path, *, documents=1, git=False) -> Path:
    project = tmp_path / "old" / "案子"
    project.mkdir(parents=True)
    ProjectRegistry.initialize_project(project)
    for index in range(documents):
        (project / f"note{index}.md").write_text(
            f"# 開戶 {index}\n護照。\n", encoding="utf-8"
        )
    if git:
        (project / ".git").mkdir()
    ProjectKnowledgeStore(project).rebuild_project_index()
    return project


def _move(tmp_path: Path, project: Path) -> Path:
    service = WorkspacePortabilityService()
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    exported = service.export_project(project, out)
    assert exported.success, exported.errors
    landing = tmp_path / "new"
    landing.mkdir(exist_ok=True)
    imported = service.import_project(Path(exported.archive_path), landing)
    assert imported.success, imported.errors
    return Path(imported.project_path)


def _keys(project: Path) -> set[str]:
    return {step.key for step in handover_steps(project)}


# --- 什麼時候完全不該有清單 --------------------------------------------------


def test_a_project_that_was_never_moved_has_no_checklist(tmp_path: Path) -> None:
    """對每個專案都跳一份接手指南，等於沒有指南。"""
    project = _old_machine(tmp_path)

    assert handover_steps(project) == ()
    assert handover_marker(project) is None


def test_a_moved_project_has_one(tmp_path: Path) -> None:
    new = _move(tmp_path, _old_machine(tmp_path))

    assert handover_steps(new), "搬過來了卻沒有清單"
    assert handover_marker(new) is not None


def test_the_marker_does_not_ride_along_to_the_next_machine(
    tmp_path: Path,
) -> None:
    """否則第三台機器會看到第二台的搬家紀錄。"""
    new = _move(tmp_path, _old_machine(tmp_path))
    assert (new / ".aicc" / "handover.json").is_file()

    service = WorkspacePortabilityService()
    out = tmp_path / "out2"
    out.mkdir()
    exported = service.export_project(new, out)
    assert exported.success, exported.errors

    assert not any(
        "handover.json" in name for name in exported.included_files
    ), exported.included_files


# --- 哪些項目不該出現 --------------------------------------------------------


def test_no_git_row_when_the_old_machine_had_no_git(tmp_path: Path) -> None:
    new = _move(tmp_path, _old_machine(tmp_path, git=False))

    assert "git" not in _keys(new)


def test_a_git_row_when_it_did(tmp_path: Path) -> None:
    # 另一方向，不然「永遠不顯示」也會通過上面那條。
    new = _move(tmp_path, _old_machine(tmp_path, git=True))

    assert "git" in _keys(new)
    step = next(s for s in handover_steps(new) if s.key == "git")
    assert step.done is False


def test_no_line_row_when_the_old_machine_never_set_it_up(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(LineNotifier, "is_configured", lambda self: False)

    new = _move(tmp_path, _old_machine(tmp_path))

    assert "line" not in _keys(new)


def test_a_line_row_when_it_did_and_this_machine_has_not(
    tmp_path: Path, monkeypatch
) -> None:
    """憑證是 DPAPI 加密的，綁機器綁帳號，複製過來也解不開。

    所以這一條不是「記得複製設定檔」，是「只能重新輸入」——匯出當下的那個
    布林值是新機唯一能知道舊機用過 LINE 的方式。
    """
    monkeypatch.setattr(LineNotifier, "is_configured", lambda self: True)
    old = _old_machine(tmp_path)
    service = WorkspacePortabilityService()
    out = tmp_path / "out"
    out.mkdir()
    exported = service.export_project(old, out)

    # 新機上沒設定。
    monkeypatch.setattr(LineNotifier, "is_configured", lambda self: False)
    landing = tmp_path / "new"
    landing.mkdir()
    new = Path(
        service.import_project(Path(exported.archive_path), landing).project_path
    )

    assert "line" in _keys(new)
    step = next(s for s in handover_steps(new) if s.key == "line")
    assert step.done is False
    assert "重新輸入" in step.detail


def test_no_documents_row_when_there_were_none_to_carry(tmp_path: Path) -> None:
    """「這個專案本來就沒有文件」不是待辦事項。"""
    new = _move(tmp_path, _old_machine(tmp_path, documents=0))

    assert "documents" not in _keys(new)


# --- 算出來的勾 --------------------------------------------------------------


def test_documents_are_ticked_when_they_all_arrived(tmp_path: Path) -> None:
    new = _move(tmp_path, _old_machine(tmp_path))

    step = next(s for s in handover_steps(new) if s.key == "documents")
    assert step.done is True


def test_documents_are_flagged_when_the_index_arrived_empty(
    tmp_path: Path,
) -> None:
    """WAL 那個 bug 修好之前，每一次搬家都是這個狀態。

    留著這一條是因為舊版 AICC 匯出的壓縮檔還在外面，而**那時候的畫面跟
    「這個專案沒有文件」長得一模一樣**。
    """
    new = _move(tmp_path, _old_machine(tmp_path))
    store = ProjectKnowledgeStore(new)
    for document in store.list_documents():
        store.remove_document(document.document_id)

    step = next(s for s in handover_steps(new) if s.key == "documents")
    assert step.done is False
    assert "1 份" in step.detail and "0 份" in step.detail, step.detail


def test_a_partial_arrival_is_still_flagged(tmp_path: Path) -> None:
    """全有或全無的案例，分不出「有幾份」和「有沒有」。

    **負向驗證就是這樣抓到上面兩條的**：把 `actual >= expected` 換成
    `actual > 0`，那兩條照樣全綠——因為它們都只用一份文件，兩個式子在
    「1 對 1」和「0 對 1」上的答案剛好一樣。少一份文件才是真實情況裡最容易
    發生、也最難察覺的那一種。
    """
    new = _move(tmp_path, _old_machine(tmp_path, documents=3))
    store = ProjectKnowledgeStore(new)
    documents = store.list_documents()
    assert len(documents) == 3, documents
    for document in documents[:2]:
        store.remove_document(document.document_id)

    step = next(s for s in handover_steps(new) if s.key == "documents")
    assert step.done is False, "少了兩份卻算成搬完了"
    assert "3 份" in step.detail and "1 份" in step.detail, step.detail


def test_the_first_run_note_ticks_once_this_machine_has_run(
    tmp_path: Path,
) -> None:
    new = _move(tmp_path, _old_machine(tmp_path))
    assert not next(s for s in handover_steps(new) if s.key == "sessions").done

    registry = SessionRegistry(new)
    registry.save_lineup(registry.load_lineup().with_session("author", "sess-NEW"))

    assert next(s for s in handover_steps(new) if s.key == "sessions").done


def test_outstanding_drops_the_finished_rows(tmp_path: Path) -> None:
    new = _move(tmp_path, _old_machine(tmp_path, git=True))
    steps = handover_steps(new)

    assert {s.key for s in outstanding(steps)} == {"git", "sessions"}
    assert len(steps) > len(outstanding(steps)), "沒有任何一項是完成的，分不出差別"


def test_the_marker_records_where_it_came_from(tmp_path: Path) -> None:
    old = _old_machine(tmp_path)
    new = _move(tmp_path, old)

    marker = handover_marker(new)
    assert marker is not None
    assert str(old) in marker["from"]
    assert marker["imported_at"]


def test_a_corrupt_marker_is_treated_as_no_marker(tmp_path: Path) -> None:
    # 壞掉的 JSON 不可以讓整頁炸掉——但也不可以假裝清單全都做完了。
    new = _move(tmp_path, _old_machine(tmp_path))
    (new / ".aicc" / "handover.json").write_text("{ 不是 json", encoding="utf-8")

    assert handover_marker(new) is None
    assert handover_steps(new) == ()


def test_the_manifest_records_the_two_facts_only_the_old_machine_knows(
    tmp_path: Path, monkeypatch
) -> None:
    """直接看壓縮檔，因為這兩個值是整條路的起點。

    在新機上算不出來，所以漏掉的話不會報錯，只會讓兩條提醒安靜地永遠不出現。
    """
    import zipfile

    monkeypatch.setattr(LineNotifier, "is_configured", lambda self: True)
    old = _old_machine(tmp_path, git=True)
    out = tmp_path / "out"
    out.mkdir()
    exported = WorkspacePortabilityService().export_project(old, out)

    with zipfile.ZipFile(exported.archive_path) as archive:
        manifest = json.loads(
            archive.read("AICC_EXPORT_MANIFEST.json").decode("utf-8")
        )

    assert manifest["had_git"] is True
    assert manifest["line_notify_configured"] is True
