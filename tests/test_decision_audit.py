"""The Decision Gate record, and the one claim it has to be able to make.

`PITCH.md` used to say AICC could 「證明 AI 生成的判斷有經過人員審核」. That
sentence was retracted on 2026-08-08 because the record could not be produced.
These tests are what earns it back, so the most important one here is not about
hashing - it is `test_codex_self_approval_is_never_filed_as_human_review`. A log
that quietly files an auto-approval as a human review is worse than no log:
it is a complete-looking audit trail that is false.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.decision_export import (
    DecisionExportBlocked,
    DecisionExportService,
)
from app.services.long_task import DecisionLogEntry, DecisionLogStore
from app.services.project_registry import ProjectRegistry


def make_project(tmp_path: Path, sensitivity: str = "personal") -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)
    if sensitivity != "personal":
        ProjectRegistry.set_sensitivity(project, sensitivity)
    return project


def _entry(action: str, **kwargs) -> DecisionLogEntry:
    return DecisionLogEntry(
        run_id=kwargs.pop("run_id", "run-1"),
        action=action,
        question=kwargs.pop("question", "要覆寫 demo.py 嗎？"),
        **kwargs,
    )


# ---- 誰答的 --------------------------------------------------------------


def test_the_human_answer_is_recorded_at_all(tmp_path: Path) -> None:
    # Before 2026-08-09 the log held 「我們問了」 and never 「他們答了什麼」 -
    # the answer went only into the run's timeline, mixed in with every CLI
    # line. That is exactly the half the audit claim needs.
    store = DecisionLogStore(tmp_path)
    store.append(_entry("ASK_USER"))
    store.append(
        _entry("ANSWERED", answer="不要，改存成 demo_v2.py", answered_by="user")
    )

    answered = [e for e in store.list_entries() if e.action == "ANSWERED"]
    assert len(answered) == 1
    assert answered[0].answer == "不要，改存成 demo_v2.py"
    assert answered[0].answered_by == "user"


def test_codex_self_approval_is_never_filed_as_human_review(tmp_path: Path) -> None:
    """The one that matters most.

    `run_claude_with_user_answer` serves both paths, so a single missing
    argument would file Codex's own approval under the field that means a
    person looked at it.
    """
    store = DecisionLogStore(tmp_path)
    store.append(_entry("CONTINUE", reason="唯讀操作", answered_by="codex"))
    store.append(_entry("ANSWERED", answer="可以", answered_by="user"))

    by_human = [e for e in store.list_entries() if e.answered_by == "user"]
    assert len(by_human) == 1, "Codex 的自動批准被記成人審了"
    assert by_human[0].action == "ANSWERED"


def test_the_engine_refuses_an_unlabelled_answer(tmp_path: Path) -> None:
    # Keyword-only and undefaulted, so a caller added later cannot inherit the
    # wrong one silently. The refusal is loud rather than defaulting to
    # "codex", because guessing conservatively is still guessing.
    from app.core.orchestrator import Orchestrator

    orchestrator = Orchestrator(make_project(tmp_path))

    with pytest.raises(TypeError):
        orchestrator.run_claude_with_user_answer("問題", "答案")

    with pytest.raises(ValueError, match="answered_by"):
        orchestrator.run_claude_with_user_answer(
            "問題", "答案", answered_by="不知道"
        )


# ---- 鏈 ------------------------------------------------------------------


def test_an_untouched_log_verifies(tmp_path: Path) -> None:
    store = DecisionLogStore(tmp_path)
    for index in range(4):
        store.append(_entry("ASK_USER", question=f"問題 {index}"))

    status = store.verify()
    assert status.ok
    assert status.verified == 4
    assert status.unchained == 0


def test_editing_one_line_afterwards_is_detected(tmp_path: Path) -> None:
    store = DecisionLogStore(tmp_path)
    for index in range(4):
        store.append(_entry("ASK_USER", question=f"問題 {index}"))

    lines = store.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["answer"] = "使用者說可以"  # the edit an audit has to catch
    lines[1] = json.dumps(tampered, ensure_ascii=False)
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    status = store.verify()
    assert not status.ok
    assert status.broken_at == 1
    assert "改過" in status.reason


def test_deleting_a_line_afterwards_is_detected(tmp_path: Path) -> None:
    store = DecisionLogStore(tmp_path)
    for index in range(4):
        store.append(_entry("ASK_USER", question=f"問題 {index}"))

    lines = store.path.read_text(encoding="utf-8").splitlines()
    del lines[1]
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    status = store.verify()
    assert not status.ok
    assert status.broken_at == 1
    # The wording is the point, not just the detection. Both checks in
    # `verify()` would catch this one (prev_hash is part of the digest), and
    # they are kept separate so a reader is told *which* kind of tampering it
    # was - 「中間少了一筆」 sends you somewhere different from 「這一筆被改過」.
    assert "刪除或插入" in status.reason


def test_truncating_the_end_is_NOT_detected(tmp_path: Path) -> None:
    """A gap in the claim, pinned so nobody widens the claim by accident.

    Nothing records how many entries there should be, so lopping entries off
    the end leaves an intact chain. The export says this in as many words;
    this test fails if the export ever stops saying it, or if someone
    "fixes" verify() to claim completeness it cannot deliver.
    """
    store = DecisionLogStore(tmp_path)
    for index in range(4):
        store.append(_entry("ASK_USER", question=f"問題 {index}"))

    lines = store.path.read_text(encoding="utf-8").splitlines()
    store.path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")

    status = store.verify()
    assert status.ok, "如果這裡開始會抓，那就是有人加了長度承諾——把說明一起改掉"
    assert status.verified == 2


def test_entries_written_before_chaining_are_not_reported_as_damage(
    tmp_path: Path,
) -> None:
    # Every existing project's log has no hash fields. Counting those as broken
    # would light the tamper warning everywhere on day one, and a warning that
    # is always on is not read.
    path = tmp_path / ".aicc" / "decisions" / "decision-log.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"run_id": "old", "action": "ASK_USER", "question": "舊的一筆"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    store = DecisionLogStore(tmp_path)
    store.append(_entry("ANSWERED", answer="新的", answered_by="user"))

    status = store.verify()
    assert status.ok, "上鏈前的舊紀錄被當成損壞了"
    assert status.unchained == 1
    assert status.verified == 1


# ---- 匯出 ----------------------------------------------------------------


def test_export_writes_both_files_and_the_raw_chain_is_verbatim(
    tmp_path: Path,
) -> None:
    # Re-serialising would risk changing key order and breaking the exported
    # copy's own hashes - the file would fail verification through no fault of
    # the log.
    project = make_project(tmp_path)
    store = DecisionLogStore(project)
    store.append(_entry("ASK_USER"))
    store.append(_entry("ANSWERED", answer="可以", answered_by="user"))

    result = DecisionExportService(project).export(tmp_path / "out")

    assert result.intact and result.entries == 2
    assert result.jsonl_path.read_text(encoding="utf-8") == store.path.read_text(
        encoding="utf-8"
    )
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "決策紀錄" in markdown
    assert "可以" in markdown
    assert "使用者" in markdown


def test_the_export_states_what_the_chain_cannot_prove(tmp_path: Path) -> None:
    # The retraction this whole feature exists to undo was caused by claiming
    # more than was true. The export must not do it again.
    project = make_project(tmp_path)
    DecisionLogStore(project).append(_entry("ASK_USER"))

    markdown = (
        DecisionExportService(project)
        .export(tmp_path / "out")
        .markdown_path.read_text(encoding="utf-8")
    )

    assert "不能證明" in markdown
    assert "重算" in markdown
    assert "無法竄改" not in markdown, "又講得比做得多了"
    assert "包含原始問答內容" in markdown
    # Trailing deletion is undetectable (see test_truncating_the_end_is_NOT_
    # detected). The export has to admit it - this assertion exists because the
    # first draft of that sentence said 「任何一筆被修改或刪除」, which was not true.
    assert "結尾" in markdown and "砍掉" in markdown


def test_a_tampered_log_says_so_at_the_top_of_the_export(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = DecisionLogStore(project)
    store.append(_entry("ASK_USER"))
    store.append(_entry("ANSWERED", answer="可以", answered_by="user"))

    lines = store.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["question"] = "一個比較好看的問題"
    lines[0] = json.dumps(tampered, ensure_ascii=False)
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = DecisionExportService(project).export(tmp_path / "out")

    assert not result.intact
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "完整性檢查未通過" in markdown
    assert "不應作為稽核依據" in markdown


def test_a_sensitive_kyc_project_cannot_export(tmp_path: Path) -> None:
    # The same rule workspace export and Drive sync already enforce. This is a
    # third way out of the machine, and it must not be the one that quietly
    # skips the check - note that it costs us the projects most likely to face
    # a real audit (ROADMAP P5's undesigned controlled exception).
    project = make_project(tmp_path, sensitivity="sensitive_kyc")
    DecisionLogStore(project).append(_entry("ASK_USER"))

    with pytest.raises(DecisionExportBlocked, match="sensitive_kyc"):
        DecisionExportService(project).export(tmp_path / "out")

    assert not (tmp_path / "out").exists(), "擋下來了卻還是寫了檔案"


def test_an_empty_log_exports_without_pretending(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    result = DecisionExportService(project).export(tmp_path / "out")

    assert result.entries == 0
    assert "還沒有任何 Decision Gate 紀錄" in result.markdown_path.read_text(
        encoding="utf-8"
    )


# ---- 生產路徑（不是只有 store） -----------------------------------------


def _orchestrator_with_fake_cli(tmp_path: Path):
    from app.adapters.cli_runner import CliResult
    from app.core.orchestrator import Orchestrator

    orchestrator = Orchestrator(make_project(tmp_path))
    orchestrator.runner.run = lambda *a, **k: CliResult(  # type: ignore[method-assign]
        command="claude", success=True, return_code=0, stdout="{}", stderr=""
    )
    return orchestrator


def test_a_human_answer_reaches_the_log_through_the_real_call(
    tmp_path: Path,
) -> None:
    orchestrator = _orchestrator_with_fake_cli(tmp_path)

    orchestrator.run_claude_with_user_answer(
        "要覆寫嗎？", "不要", answered_by="user"
    )

    entries = DecisionLogStore(orchestrator.project_path).list_entries()
    assert [e.action for e in entries] == ["ANSWERED"]
    assert entries[0].answered_by == "user"
    assert entries[0].answer == "不要"


def test_codex_answering_through_the_same_call_writes_no_human_entry(
    tmp_path: Path,
) -> None:
    # The Decision Gate's auto-approve path calls this same method. Its audit
    # record is the CONTINUE entry written at the gate; writing here as well
    # would both double-count it and file it as a human review.
    orchestrator = _orchestrator_with_fake_cli(tmp_path)

    orchestrator.run_claude_with_user_answer(
        "要跑測試嗎？", "可以，唯讀", answered_by="codex"
    )

    entries = DecisionLogStore(orchestrator.project_path).list_entries()
    assert entries == [], f"Codex 的答覆被寫進稽核紀錄了：{entries}"


# ---- 受控例外 ------------------------------------------------------------
#
# The exception SECURITY_MODEL.md imagined and ROADMAP P5 never designed. Every
# test here is about keeping it narrow: the danger is not that it exists, it is
# that it quietly widens.


def _authorised(reason: str = "客戶要求提供決策紀錄供其法遵單位查閱"):
    from app.services.decision_export import ExportAuthorisation

    return ExportAuthorisation(reason=reason, acknowledged_contents=True)


def test_an_authorised_kyc_export_succeeds(tmp_path: Path) -> None:
    project = make_project(tmp_path, sensitivity="sensitive_kyc")
    DecisionLogStore(project).append(_entry("ASK_USER"))

    result = DecisionExportService(project).export(
        tmp_path / "out", authorisation=_authorised()
    )

    assert result.intact


def test_the_authorisation_appears_inside_the_file_it_authorised(
    tmp_path: Path,
) -> None:
    # Written before the export reads the log, on purpose: whoever reads this
    # export later can see why it left the machine, without a second document.
    project = make_project(tmp_path, sensitivity="sensitive_kyc")
    DecisionLogStore(project).append(_entry("ASK_USER"))

    result = DecisionExportService(project).export(
        tmp_path / "out", authorisation=_authorised("客戶法遵單位索取")
    )

    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "使用者授權匯出" in markdown
    assert "客戶法遵單位索取" in markdown
    assert result.intact, "授權那一筆必須也在鏈上"


def test_the_authorisation_is_not_remembered(tmp_path: Path) -> None:
    # 「這個專案以後都可以」 is not an exception, it is a sensitivity downgrade
    # wearing one. The second export has to ask again.
    project = make_project(tmp_path, sensitivity="sensitive_kyc")
    DecisionLogStore(project).append(_entry("ASK_USER"))
    service = DecisionExportService(project)

    service.export(tmp_path / "out", authorisation=_authorised())

    with pytest.raises(DecisionExportBlocked):
        service.export(tmp_path / "out2")


def test_an_authorisation_without_a_reason_is_refused(tmp_path: Path) -> None:
    from app.services.decision_export import ExportAuthorisation

    project = make_project(tmp_path, sensitivity="sensitive_kyc")

    with pytest.raises(DecisionExportBlocked, match="理由"):
        DecisionExportService(project).export(
            tmp_path / "out",
            authorisation=ExportAuthorisation(
                reason="   ", acknowledged_contents=True
            ),
        )
    assert not (tmp_path / "out").exists()


def test_an_unacknowledged_authorisation_is_refused(tmp_path: Path) -> None:
    # There is no redaction, so 「我知道裡面有什麼」 cannot be assumed by code.
    from app.services.decision_export import ExportAuthorisation

    project = make_project(tmp_path, sensitivity="sensitive_kyc")

    with pytest.raises(DecisionExportBlocked, match="原始問答"):
        DecisionExportService(project).export(
            tmp_path / "out",
            authorisation=ExportAuthorisation(reason="有理由", acknowledged_contents=False),
        )


def test_a_refused_authorisation_writes_nothing_to_the_log(tmp_path: Path) -> None:
    # The authorisation entry is only written once the checks pass. A refused
    # attempt that still logged 「授權匯出」 would put a lie in the audit trail.
    from app.services.decision_export import ExportAuthorisation

    project = make_project(tmp_path, sensitivity="sensitive_kyc")
    store = DecisionLogStore(project)
    store.append(_entry("ASK_USER"))

    with pytest.raises(DecisionExportBlocked):
        DecisionExportService(project).export(
            tmp_path / "out",
            authorisation=ExportAuthorisation(reason="", acknowledged_contents=True),
        )

    assert [e.action for e in store.list_entries()] == ["ASK_USER"]


def test_the_exception_does_not_leak_into_workspace_export(tmp_path: Path) -> None:
    # The narrowness IS the design. This moves a record of decisions; workspace
    # export moves the case files themselves, and stays hard-blocked.
    from app.services.workspace_portability import WorkspacePortabilityService

    project = make_project(tmp_path, sensitivity="sensitive_kyc")
    service = WorkspacePortabilityService()

    # Authorising the audit export first, to make the point sharply: even with
    # a fresh authorisation on record, the workspace path is still shut.
    DecisionExportService(project).export(
        tmp_path / "out", authorisation=_authorised()
    )

    result = service.export_project(project, tmp_path / "leak.zip")

    assert not result.success
    assert any("sensitive" in error.lower() for error in result.errors)
    assert not (tmp_path / "leak.zip").exists()


# ---- 面板接線 ------------------------------------------------------------


def _panel(project: Path):
    from PySide6.QtWidgets import QApplication

    from app.ui.audit_web_panel import AuditWebPanel

    QApplication.instance() or QApplication([])
    return AuditWebPanel(project)


def test_the_panel_reports_who_answered_each_entry(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = DecisionLogStore(project)
    store.append(_entry("ANSWERED", answer="不要", answered_by="user"))
    store.append(_entry("CONTINUE", reason="唯讀", answered_by="codex"))

    state = _panel(project).collect_state()

    assert [e["answered_by"] for e in state["entries"]] == ["user", "codex"]
    assert state["integrity"]["verified"] == 2
    assert state["integrity"]["broken_at"] is None


def test_a_sensitive_project_is_blocked_from_exporting_but_not_from_looking(
    tmp_path: Path,
) -> None:
    # The design session asked which one `blocked` means and assumed export.
    # That is right: reading your own record on your own machine needs no
    # permission - leaving the machine does.
    project = make_project(tmp_path, sensitivity="sensitive_kyc")
    DecisionLogStore(project).append(_entry("ANSWERED", answer="可以", answered_by="user"))

    state = _panel(project).collect_state()

    assert state["sensitive"] is True
    assert state["blocked"] is not None
    assert state["entries"], "擋匯出不該連檢視也擋掉"
    assert "不會被記住" in state["blocked"]["reason"]


def test_the_preview_is_the_document_that_would_be_written(tmp_path: Path) -> None:
    # Built by the export service's own renderer. A preview produced by a
    # second code path is a preview of a different document - and for the
    # sensitive_kyc authorisation, seeing the real thing IS the safeguard.
    project = make_project(tmp_path)
    DecisionLogStore(project).append(
        _entry("ANSWERED", answer="不要，改存成 demo_v2.py", answered_by="user")
    )
    panel = _panel(project)

    panel.handle_action("export_preview", "")
    preview = panel.collect_state()["preview"]

    assert preview is not None
    assert "不要，改存成 demo_v2.py" in preview["md"]
    assert "不能證明" in preview["md"], "預覽必須包含極限說明"


def test_the_sidebar_puts_the_record_last_and_marks_it_apart() -> None:
    from app.ui.shell_panel import PAGE_GROUPS, PAGES

    keys = [key for key, _label in PAGES]
    assert keys[-1] == "audit", "決策紀錄必須在最後——分隔線靠順序，不靠 CSS 猜"
    assert PAGE_GROUPS["audit"] == "proof"
    # The two lists are separate, so this is what stops them drifting.
    assert set(PAGE_GROUPS) <= set(keys)


def test_an_answered_question_is_one_card_not_two(tmp_path: Path) -> None:
    """The bug this merge exists to stop.

    The log stores ASK_USER and ANSWERED separately (two timestamps, both audit
    facts). The page renders an ASK_USER without an answer as 「尚未回答」. Pushed
    unmerged, every answered question appeared twice - once falsely marked as
    outstanding, on the one page whose whole job is to say what was decided.
    """
    from app.ui.audit_web_panel import merge_question_and_answer

    entries = [
        _entry("ASK_USER", question="要覆寫 demo.py 嗎？", reason="有手動修改痕跡"),
        _entry("ANSWERED", question="要覆寫 demo.py 嗎？", answer="不要", answered_by="user"),
    ]

    merged = merge_question_and_answer(entries)

    assert len(merged) == 1
    assert merged[0]["action"] == "ANSWERED"
    assert merged[0]["question"] == "要覆寫 demo.py 嗎？"
    assert merged[0]["answer"] == "不要"
    assert merged[0]["reason"] == "有手動修改痕跡", "合併不能弄丟停下來的原因"


def test_an_unanswered_question_stays_unanswered(tmp_path: Path) -> None:
    # The other half: a question nobody answered is real information, and must
    # not be swallowed by a later answer to a different question.
    from app.ui.audit_web_panel import merge_question_and_answer

    # The answer is to the FIRST question while a later one is still open, so
    # a merge that ignores the question text pairs it onto 第二題 and reports
    # 第一題 as outstanding. (An earlier version of this test answered 第二題,
    # which the pairing got right by luck even with the question check removed
    # - the negative control caught that it proved nothing.)
    merged = merge_question_and_answer(
        [
            _entry("ASK_USER", question="第一題"),
            _entry("ASK_USER", question="第二題"),
            _entry("ANSWERED", question="第一題", answer="好", answered_by="user"),
        ]
    )

    assert [(e["question"], e["answer"]) for e in merged] == [
        ("第一題", "好"),
        ("第二題", ""),
    ]


def test_an_answer_resolves_the_most_recent_open_question(tmp_path: Path) -> None:
    # Two identical questions outstanding, one answer. It resolves the later
    # one - that is what "the run stopped and someone replied" means. Searching
    # oldest-first would mark the 10:00 question answered and leave 10:05 open,
    # which is the same fact recorded against the wrong moment.
    from app.ui.audit_web_panel import merge_question_and_answer

    merged = merge_question_and_answer(
        [
            DecisionLogEntry(run_id="r1", action="ASK_USER", question="要繼續嗎？",
                             created_at="2026-08-09T10:00:00+00:00"),
            DecisionLogEntry(run_id="r1", action="ASK_USER", question="要繼續嗎？",
                             created_at="2026-08-09T10:05:00+00:00"),
            DecisionLogEntry(run_id="r1", action="ANSWERED", question="要繼續嗎？",
                             answer="好", answered_by="user",
                             created_at="2026-08-09T10:06:00+00:00"),
        ]
    )

    answered = [e for e in merged if e["answer"]]
    assert len(answered) == 1
    # `_asked_iso` rather than `created_at`: the latter is the display string
    # the page prints, and it has no year or timezone.
    assert answered[0]["_asked_iso"].endswith("10:05:00+00:00")


def test_an_answer_never_overwrites_one_already_on_the_card(tmp_path: Path) -> None:
    """An audit record must not let a later line replace an earlier decision.

    The case that exercises this is an `ASK_USER` row that already carries an
    answer - which the data model allows and the page renders as a human card.
    (Two consecutive `ANSWERED` rows do *not* exercise it: merging rewrites the
    card's action to `ANSWERED`, so the action check alone already stops the
    second one. The negative control caught that the first version of this test
    proved nothing.)
    """
    from app.ui.audit_web_panel import merge_question_and_answer

    merged = merge_question_and_answer(
        [
            _entry("ASK_USER", question="要繼續嗎？", answer="甲", answered_by="user"),
            _entry("ANSWERED", question="要繼續嗎？", answer="乙", answered_by="user"),
        ]
    )

    answers = [e["answer"] for e in merged if e["answer"]]
    assert answers == ["甲", "乙"], f"有答案被蓋掉了：{answers}"


def test_an_answer_with_nothing_to_pair_to_is_still_shown(tmp_path: Path) -> None:
    # Dropping it would lose a real decision. Showing it without its question
    # is worse-looking but honest.
    from app.ui.audit_web_panel import merge_question_and_answer

    merged = merge_question_and_answer(
        [_entry("ANSWERED", question="孤兒答案", answer="可以", answered_by="user")]
    )

    assert len(merged) == 1
    assert merged[0]["answer"] == "可以"


def test_the_wait_between_question_and_answer_is_not_lost(tmp_path: Path) -> None:
    # The page does not show it yet, but the gap between 「停下來問」 and
    # 「有人回答」 is an audit fact - merging must not flatten the two times
    # into one.
    from app.ui.audit_web_panel import merge_question_and_answer

    merged = merge_question_and_answer(
        [
            DecisionLogEntry(
                run_id="r1", action="ASK_USER", question="Q",
                created_at="2026-08-09T10:00:00+00:00",
            ),
            DecisionLogEntry(
                run_id="r1", action="ANSWERED", question="Q", answer="A",
                answered_by="user", created_at="2026-08-09T10:07:00+00:00",
            ),
        ]
    )

    # Both moments survive the merge as separate display strings, and the raw
    # value is kept alongside so the wait is computed from something that has a
    # year and a timezone.
    assert merged[0]["created_at"] != merged[0]["answered_at"]
    assert merged[0]["_asked_iso"].endswith("10:00:00+00:00")
    assert merged[0]["waited"] == "7 分鐘"


# ---- 等待時長 ------------------------------------------------------------
#
# Formatted in Python at the design session's request: a second formatter in
# the browser is a second set of rules, and the browser's is the one nobody
# runs tests against.


def test_the_wait_is_formatted_for_the_page_not_computed_by_it() -> None:
    from app.ui.audit_web_panel import format_waited

    def waited(minutes: int) -> str:
        return format_waited(
            "2026-08-09T10:00:00+00:00",
            f"2026-08-09T{10 + minutes // 60:02d}:{minutes % 60:02d}:00+00:00",
        )

    assert waited(0) == "不到 1 分鐘"
    assert waited(12) == "12 分鐘"
    assert waited(60) == "1 小時"
    assert waited(221) == "3 小時 41 分"


def test_an_unmeasurable_wait_says_nothing_rather_than_zero(tmp_path: Path) -> None:
    # 「等了 0 分鐘」 reads as a measured fact about a wait that was never
    # measured. The page omits the phrase when this is empty.
    from app.ui.audit_web_panel import format_waited

    assert format_waited("", "2026-08-09T10:00:00+00:00") == ""
    assert format_waited("2026-08-09T10:00:00+00:00", "") == ""
    assert format_waited("八月九號", "2026-08-09T10:00:00+00:00") == ""
    # Answered before asked - a clock change or a hand-edited log. Inventing a
    # duration for it would be worse than saying nothing.
    assert format_waited(
        "2026-08-09T10:05:00+00:00", "2026-08-09T10:00:00+00:00"
    ) == ""


def test_the_merged_card_carries_the_wait(tmp_path: Path) -> None:
    from app.ui.audit_web_panel import merge_question_and_answer

    merged = merge_question_and_answer(
        [
            DecisionLogEntry(run_id="r1", action="ASK_USER", question="Q",
                             created_at="2026-08-09T10:00:00+00:00"),
            DecisionLogEntry(run_id="r1", action="ANSWERED", question="Q", answer="A",
                             answered_by="user",
                             created_at="2026-08-09T10:12:00+00:00"),
        ]
    )

    assert merged[0]["waited"] == "12 分鐘"


def test_the_preview_gives_each_file_its_own_unit(tmp_path: Path) -> None:
    # Not a shared 「大小」 column: size is the one thing these two files should
    # not be compared on. Both numbers are known before anything is written, so
    # neither is an estimate - which is what removed the last placeholder here.
    project = make_project(tmp_path)
    store = DecisionLogStore(project)
    store.append(_entry("ASK_USER"))
    store.append(_entry("ANSWERED", answer="可以", answered_by="user"))
    panel = _panel(project)

    panel.handle_action("export_preview", "")
    files = panel.collect_state()["preview"]["files"]

    assert [f["role"] for f in files] == ["給人看的", "給程式驗的"]
    assert files[0]["amount"].endswith("字")
    assert files[1]["amount"] == "2 筆原始紀錄"
    for unit in ("KB", "MB", "bytes", "位元組"):
        assert not any(unit in f["amount"] for f in files), (
            f"宣稱了位元組大小（{unit}）——檔案還沒寫出來，那個數字沒有來源"
        )


# ---- 給人看的字串（都在 Python 這邊產生）--------------------------------


def test_an_unparseable_gate_reply_does_not_become_the_question() -> None:
    """Found by opening the app and reading the audit page.

    When Codex's Decision Gate reply cannot be parsed, the old fallback put the
    raw CLI text in `question` - and because the caller uses
    `decision.get("question") or question`, the raw text won that fallback. A
    machine marker went into the decision log and out onto the page:
    「ASK_USER: 要覆寫 demo.py 嗎？」, marker and all. The caller still holds the
    real question, so the fallback must not supply one.
    """
    from app.core.orchestrator_parsing import parse_decision_gate

    decision = parse_decision_gate("ASK_USER: 要覆寫 demo.py 嗎？")

    assert decision["decision"] == "ASK_USER"
    assert decision["parsed"] is False
    assert decision["question"] == "", (
        f"原始輸出又被當成問題了：{decision['question']!r}"
    )


def test_the_page_gets_a_readable_time_not_a_raw_timestamp(tmp_path: Path) -> None:
    # The page prints `created_at` verbatim, so an ISO string with microseconds
    # and a UTC offset went straight onto the document a person hands to
    # someone else. Formatted here, like `waited`, so there is one set of rules.
    from app.ui.audit_web_panel import format_moment

    assert format_moment("2026-08-08T15:51:16.827981+00:00").count("-") == 1
    assert ":" in format_moment("2026-08-08T15:51:16.827981+00:00")
    assert "T" not in format_moment("2026-08-08T15:51:16.827981+00:00")
    # Unreadable input is passed through, not blanked: a timestamp we cannot
    # parse is still evidence of when something happened.
    assert format_moment("八月八號") == "八月八號"
    assert format_moment("") == ""


def test_the_wait_survives_the_display_formatting(tmp_path: Path) -> None:
    # The merge must compute the wait from the raw values. 「08-08 23:51」 has no
    # year and no timezone; a wait derived from it would be wrong across a
    # month boundary and unparseable the rest of the time.
    from app.ui.audit_web_panel import merge_question_and_answer

    merged = merge_question_and_answer(
        [
            DecisionLogEntry(run_id="r1", action="ASK_USER", question="Q",
                             created_at="2026-08-31T23:50:00+00:00"),
            DecisionLogEntry(run_id="r1", action="ANSWERED", question="Q", answer="A",
                             answered_by="user",
                             created_at="2026-09-01T00:10:00+00:00"),
        ]
    )

    assert merged[0]["waited"] == "20 分鐘"


# ---- 「回答了沒」只能有一種答案 -------------------------------------------


def _pending_entry(action, question, run_id="r1", answer="", at="2026-08-10T01:00:00"):
    from app.services.long_task import DecisionLogEntry

    return DecisionLogEntry(
        run_id=run_id,
        action=action,
        question=question,
        answer=answer,
        answered_by="user" if action == "ANSWERED" else "",
        created_at=at,
    )


def _pending_log(tmp_path, *entries):
    from app.services.long_task import DecisionLogStore

    project = tmp_path / "p"
    (project / ".aicc").mkdir(parents=True, exist_ok=True)
    store = DecisionLogStore(project)
    for entry in entries:
        store.append(entry)
    return store


def test_unanswered_agrees_with_what_the_audit_page_calls_unanswered(
    tmp_path,
) -> None:
    """Two implementations of one rule, pinned to the same verdict.

    `DecisionLogStore.unanswered()` filters; the audit page merges a question
    with its answer into one card. They need different shapes, so there are
    two - and two rules for 「回答了沒」 disagreeing is the one thing an audit
    trail cannot do. This is what stops them.
    """
    from app.ui.audit_web_panel import merge_question_and_answer

    store = _pending_log(
        tmp_path,
        _pending_entry("ASK_USER", "要不要刪掉舊檔？", at="2026-08-10T01:00:00"),
        _pending_entry("ANSWERED", "要不要刪掉舊檔？", answer="不要。", at="2026-08-10T01:05:00"),
        _pending_entry("ASK_USER", "要不要改資料庫？", at="2026-08-10T02:00:00"),
        _pending_entry("CONTINUE", "要不要加測試？", answer="加。", at="2026-08-10T03:00:00"),
        _pending_entry("ASK_USER", "要不要通知客戶？", run_id="r2", at="2026-08-10T04:00:00"),
    )

    from_store = {e.question for e in store.unanswered()}
    from_page = {
        row["question"]
        for row in merge_question_and_answer(store.list_entries())
        if row["action"] == "ASK_USER"
    }

    assert from_store == from_page
    assert from_store == {"要不要改資料庫？", "要不要通知客戶？"}


def test_the_same_question_twice_needs_two_answers(tmp_path) -> None:
    """One answer clears one asking, not both.

    Otherwise a project that asked the same thing again looks answered while
    it sits there waiting.
    """
    store = _pending_log(
        tmp_path,
        _pending_entry("ASK_USER", "要不要繼續？", at="2026-08-10T01:00:00"),
        _pending_entry("ASK_USER", "要不要繼續？", at="2026-08-10T02:00:00"),
        _pending_entry("ANSWERED", "要不要繼續？", answer="好。", at="2026-08-10T02:30:00"),
    )

    assert len(store.unanswered()) == 1


def test_codex_approving_itself_does_not_count_as_answered(tmp_path) -> None:
    """`CONTINUE` is Codex approving its own question.

    The log records it separately precisely so it is never mistaken for a
    person having replied - the whole 「證明有經過人員審核」 claim rests on
    that distinction.
    """
    store = _pending_log(
        tmp_path,
        _pending_entry("ASK_USER", "要不要刪掉舊檔？", at="2026-08-10T01:00:00"),
        _pending_entry("CONTINUE", "要不要刪掉舊檔？", answer="刪。", at="2026-08-10T01:01:00"),
    )

    assert [e.question for e in store.unanswered()] == ["要不要刪掉舊檔？"]


def test_an_answer_clears_the_question_it_answers_not_the_latest_one(
    tmp_path,
) -> None:
    """The answer is to the FIRST question while a later one is still open.

    Found by negative control: every other case here happened to answer the
    most recent question, so dropping the question-text comparison gave the
    same result and nothing went red. The audit page has the mirror image of
    this test for the same reason - and if these two ever disagree, one screen
    will show a project as answered while the other shows it waiting.
    """
    store = _pending_log(
        tmp_path,
        _pending_entry("ASK_USER", "第一題", at="2026-08-10T01:00:00"),
        _pending_entry("ASK_USER", "第二題", at="2026-08-10T02:00:00"),
        _pending_entry("ANSWERED", "第一題", answer="好", at="2026-08-10T03:00:00"),
    )

    assert [e.question for e in store.unanswered()] == ["第二題"]
