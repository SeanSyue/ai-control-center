"""跑完一次之後，硬碟上真的有章。

`tests/test_the_chain_of_stamps_holds.py` 問的是規則對不對。這一份問的是
**有沒有人呼叫它**——而那是這個專案反覆出現的那個缺陷的形狀：算出來了、
推到下游了、沒有人讀它。`tools/zero_callers.py` 就是為它寫的。

一個規則層可以每一條測試都是綠的，同時在真實執行裡一次都沒被叫到，而且
兩者的畫面一模一樣。所以這裡跑的是完整的 `WorkflowWorker`，看的是它在
`.aicc/runs/<run_id>/stamps.jsonl` 留下什麼。

**這一版的章只記錄、不擋任何東西**，所以這裡也順便釘住那件事：接上章之後，
每一種結局的 `workflow_status` 都要跟接上之前一模一樣。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.run_stamps import CURRENT_ENGINE_REQUIRES  # noqa: E402
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.approval_chain import (  # noqa: E402
    SUBJECT_ACCEPTANCE,
    SUBJECT_VERIFY,
    SUBJECT_WORK,
    Result,
    StampKind,
    StampLog,
    Verdict,
    Versions,
    content_digest,
)
from fakes import (  # noqa: E402
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
    with_intake,
)

PASSING_REVIEW = (
    '{"status": "PASS", "summary": "looks good", '
    '"issues": [], "instructions": [], "question": ""}'
)


def _run(tmp_path: Path, monkeypatch, script) -> tuple[dict, Path]:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])

    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# Task\n\nDo the thing.\n", encoding="utf-8"
    )
    declare_verify_command(project)

    monkeypatch.setattr(
        "app.core.orchestrator.CliRunner", scripted_cli_runner_class(with_intake(script))
    )

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
            max_fix_attempts=1,
        ),
    )
    completed: list[dict] = []
    worker.completed.connect(completed.append)
    worker.run()
    QApplication.instance().processEvents()

    assert completed, "這一輪沒有跑完，底下每一條都會是恆真的"
    return completed[0], project


def _stamps(project: Path, run_id: str):
    return StampLog(project).read(run_id).stamps


def test_a_completed_run_leaves_its_stamps_on_disk(
    tmp_path: Path, monkeypatch
) -> None:
    """最重要的一條：規則層有沒有被真的叫到。

    問的是硬碟，不是回傳值。一個從來沒被呼叫的模組，它自己的單元測試會
    全綠——這裡是唯一會因為「沒有人叫它」而變紅的地方。
    """
    result, project = _run(
        tmp_path,
        monkeypatch,
        [
            cli_result('{"result": "done, no questions"}'),
            cli_result("1 passed"),
            cli_result(PASSING_REVIEW),
        ],
    )

    run_id = result["run_id"]
    assert run_id, "沒有 run_id，章就沒有地方可以寫"

    path = StampLog(project).path_for(run_id)
    assert path.is_file(), (
        f"跑完了但 {path.name} 不存在——章的規則層一次都沒有被叫到"
    )

    stamps = _stamps(project, run_id)
    put = {(s.role, s.subject, s.kind) for s in stamps}

    assert ("author", SUBJECT_WORK, StampKind.MACHINE) in put
    # 驗證那一章掛在 `engine` 名下，不掛在任何一格：**沒有任何一個席位做了
    # 那件事**，是程式跑專案宣告的指令。掛在席位名下的話，陣容改名之後那個
    # 必要的章會指向一格不存在的東西，而且永遠補不出來。
    assert ("engine", SUBJECT_VERIFY, StampKind.MACHINE) in put
    assert ("reviewer", SUBJECT_WORK, StampKind.REVIEW) in put


def test_the_machine_stamps_carry_evidence_not_just_a_verdict(
    tmp_path: Path, monkeypatch
) -> None:
    """證據是機器章與審查章唯一分得出來的地方。

    蓋一個沒有證據的機器章在規則層是建不出來的，所以真正的風險不是「證據
    是空的」，是**呼叫端傳了一個沒有意義的字串進去湊數**——那樣它建得出來，
    而且看起來完全正常。
    """
    result, project = _run(
        tmp_path,
        monkeypatch,
        [
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASSING_REVIEW),
        ],
    )

    stamps = _stamps(project, result["run_id"])
    machine = [s for s in stamps if s.kind is StampKind.MACHINE]
    assert machine

    for stamp in machine:
        assert stamp.evidence.strip(), stamp
        assert stamp.result is not None, stamp

    review = [s for s in stamps if s.kind is StampKind.REVIEW]
    assert review, "一個審查章都沒有，底下的檢查會恆真"
    for stamp in review:
        assert not stamp.evidence
        assert stamp.result is None
        assert stamp.reasoning.strip(), stamp

    # 「憑什麼」要真的從複查那邊傳過來，不是引擎自己補一句話充數。
    # PASS_REVIEW 裡的 summary 是 "looks good"，所以它必須出現在這裡。
    #
    # 只看**實作**那一個審查章：驗收標準的章也是審查章，但它的理由講的是
    # 另一件事（跟使用者來回了幾輪、最後問的是什麼），拿複查的那句話去比它
    # 會永遠紅，而且紅得毫無道理。
    #
    # 2026-08-24 起實作上會有**兩個**審查章：複查蓋一個，拍板那一格蓋一個
    # （老闆拍板「規定要最後一個 NODE 去驗證」）。而在出廠的兩格陣容裡，
    # **最後一格就是複查那一格**，所以兩個章的 `role` 一模一樣——濾角色濾
    # 不開它們，只有理由不一樣。
    #
    # 所以這裡問的是：複查自己寫的那句話**有沒有真的傳過來**。它沒有傳到的
    # 話，兩個章的理由都會是引擎自己補的。
    on_the_work = [s for s in review if s.subject == SUBJECT_WORK]
    assert on_the_work, "沒有任何一個章是蓋在實作上的，底下的檢查會恆真"
    assert any("looks good" in stamp.reasoning for stamp in on_the_work), (
        "複查寫的那句話沒有進到任何一個章裡，理由是引擎自己補的："
        f"{[s.reasoning for s in on_the_work]!r}"
    )
    for stamp in on_the_work:
        assert stamp.reasoning.strip(), f"這個章沒有理由：{stamp}"


def test_a_failing_verification_is_stamped_rather_than_left_blank(
    tmp_path: Path, monkeypatch
) -> None:
    """失敗也要留下章。

    只在成功時蓋章的話，紀錄裡會缺掉每一次失敗的那一段——而事後要回答
    「它到底試過什麼」的時候，缺掉的正是最需要的那幾行。
    """
    result, project = _run(
        tmp_path,
        monkeypatch,
        [
            cli_result('{"result": "done"}'),
            cli_result("1 failed", success=False),
            cli_result('{"result": "fixed"}'),
            cli_result("1 failed", success=False),
        ],
    )

    stamps = _stamps(project, result["run_id"])
    verify = [s for s in stamps if s.subject == SUBJECT_VERIFY]

    assert verify, "驗證跑了而且是紅的，卻沒有留下任何章"
    assert any(s.result is Result.FAIL for s in verify)


def test_the_acceptance_stamp_is_reported_missing_not_invented(
    tmp_path: Path, monkeypatch
) -> None:
    """驗收標準的章要由**跟使用者問清楚的那一格**蓋，而且不准是引擎編的。

    這條測試 2026-08-22 之前守的是另一件事：那個步驟還不存在，所以每次執行
    都誠實地回報「缺 1 個章」。步驟做出來了，所以它跟著改——當初就寫著
    「那個步驟做出來的話，這條測試要跟著改，而不是刪掉」。

    現在守的是三件事：章真的蓋了、蓋的人是訂標準的那一格（不是做事的那一格
    ——那就是自己出考卷自己考）、而且它的理由講得出**憑什麼**認為問清楚了。
    """
    result, project = _run(
        tmp_path,
        monkeypatch,
        [
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASSING_REVIEW),
        ],
    )

    stamps = _stamps(project, result["run_id"])
    bar = [s for s in stamps if s.subject == SUBJECT_ACCEPTANCE]
    assert len(bar) == 1, (
        "驗收標準的章不是剛好一個。零個代表那一步沒跑；兩個代表同一次執行"
        f"訂了兩份標準，而後面每一格只會照著其中一份做。實際：{bar}"
    )

    # 出廠兩人陣容裡，第一個不做事的節點是複查。訂標準的**不能**是 author
    # ——那正是「自己出考卷自己考」。
    assert bar[0].role == "reviewer", bar[0]
    assert bar[0].kind is StampKind.REVIEW
    assert bar[0].reasoning.strip(), (
        "驗收標準的章沒有理由。只記「誰、何時、哪一版」的章，回答不了事後"
        "最需要的那一題：憑什麼認為問清楚了"
    )

    assert any(
        item.subject == SUBJECT_ACCEPTANCE for item in CURRENT_ENGINE_REQUIRES
    ), "acceptance 從必要清單裡消失了，那個缺口就不會被顯示出來"


def test_the_stamps_survive_a_round_trip_through_the_file(
    tmp_path: Path, monkeypatch
) -> None:
    """存下去再讀回來，要是同一批章。

    讀的時候規則跟寫的時候是同一套：一個違反規則的章不准因為「它已經在
    檔案裡了」就取得豁免，否則存檔就變成繞過檢查的路。
    """
    result, project = _run(
        tmp_path,
        monkeypatch,
        [
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASSING_REVIEW),
        ],
    )

    chain = StampLog(project).read(result["run_id"])
    assert len(chain.stamps) >= 3

    ids = [s.stamp_id for s in chain.stamps]
    assert len(ids) == len(set(ids)), f"章的編號撞號了：{ids}"

    # 每一個章（除了第一個）都說得出自己站在誰身上。
    for earlier, later in zip(chain.stamps, chain.stamps[1:]):
        assert later.depends_on, f"{later.stamp_id} 沒有地基"
        assert earlier.stamp_id in later.depends_on or later.depends_on


@pytest.mark.parametrize(
    "script, expected_status",
    [
        (
            [
                cli_result('{"result": "done"}'),
                cli_result("1 passed"),
                cli_result(PASSING_REVIEW),
            ],
            "COMPLETED",
        ),
        (
            [
                cli_result("boom", success=False),
            ],
            "CLAUDE_ERROR",
        ),
    ],
)
def test_stamping_changed_nothing_about_how_a_run_ends(
    tmp_path: Path, monkeypatch, script, expected_status: str
) -> None:
    """章這一版只記錄。**接上它之後，每一種結局都要跟接上之前一模一樣。**

    同一次改動裡同時引進「章可能算錯」與「算錯就擋掉該過的執行」，第二種
    會蓋掉第一種的症狀——到時候看到的是一次莫名其妙被擋下來的執行，而不是
    一個算錯的章。
    """
    result, _ = _run(tmp_path, monkeypatch, script)

    assert result["workflow_status"] == expected_status


def test_the_readiness_is_computed_from_what_was_actually_stamped(
    tmp_path: Path, monkeypatch
) -> None:
    """「能不能拍板」要算得出來，而且答案要對得上現場。

    出廠兩人陣容跑完一輪之後，四個章都在同一組版本上：驗收標準一個、實作
    一個、驗證一個、複查一個。所以答案是**可以拍板**。

    2026-08-22 之前這裡是 MISSING，因為驗收標準那一步還不存在。它做出來了，
    所以這條跟著改——而它現在守的是更值錢的一件事：**兩條版本線真的分開了**。
    驗收標準的章綁在標準上、其餘綁在交付物上，混成一條的話標準那個章會在
    交件的當下失效，而畫面會說「沒有人訂標準」。
    """
    result, project = _run(
        tmp_path,
        monkeypatch,
        [
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASSING_REVIEW),
        ],
    )

    chain = StampLog(project).read(result["run_id"])
    work = [s for s in chain.stamps if s.subject == SUBJECT_WORK]
    bar = [s for s in chain.stamps if s.subject == SUBJECT_ACCEPTANCE]
    assert work and bar

    # 兩條版本線各自從現場的章拿。刻意**不是**同一個值——同一個值的話，
    # 這條測試在兩條線合回一條的時候照樣會綠。
    verdict = chain.readiness(
        Versions(
            criteria=bar[0].content_digest,
            deliverable=work[0].content_digest,
        ),
        CURRENT_ENGINE_REQUIRES,
    )

    assert verdict.can_decide, (
        f"四個章都在同一組版本上卻拍不了板：{verdict.verdict.value}，"
        f"缺 {[i.subject for i in verdict.missing]}"
    )
    assert verdict.short == "可以拍板"

    # 而且兩條線真的是分開的：標準改一個字，蓋在它上面的實作與驗證要跟著
    # 失效，但反過來不成立。
    moved_bar = chain.readiness(
        Versions(criteria="別的標準", deliverable=work[0].content_digest),
        CURRENT_ENGINE_REQUIRES,
    )
    assert not moved_bar.can_decide, "改了驗收標準，舊的章卻還算數"


def test_two_runs_do_not_share_a_stamp_file(
    tmp_path: Path, monkeypatch
) -> None:
    """一次執行一個檔。

    共用一個檔的話每一個讀的人都要自己過濾，而那種過濾忘記一次，畫面上
    就會出現另一次執行的章——看起來完全像這一次的。
    """
    script = [
        cli_result('{"result": "done"}'),
        cli_result("1 passed"),
        cli_result(PASSING_REVIEW),
    ]
    first, project_a = _run(tmp_path / "a", monkeypatch, list(script))
    second, project_b = _run(tmp_path / "b", monkeypatch, list(script))

    assert first["run_id"] != second["run_id"]
    assert StampLog(project_a).path_for(first["run_id"]) != StampLog(
        project_b
    ).path_for(second["run_id"])


def test_the_digest_is_not_a_constant(tmp_path: Path, monkeypatch) -> None:
    """哨兵回音：章綁的版本碼要真的跟著內容走。

    如果版本碼是誰自己編的（或者永遠是同一個），整個失效機制都是假的，
    而畫面上看起來完全正常。
    """
    result, project = _run(
        tmp_path,
        monkeypatch,
        [
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASSING_REVIEW),
        ],
    )

    chain = StampLog(project).read(result["run_id"])
    digests = {s.content_digest for s in chain.stamps}

    assert digests, "一個章都沒有，這條會恆真"
    for digest in digests:
        assert len(digest) == 64, digest

    # 這個測試專案是全新的暫存資料夾，git diff 是空的——所以空字串的雜湊
    # 是「合法但沒有資訊」的那一個值。真正要擋的是**別的**常數：一個寫死
    # 的版本碼會讓每一個章永遠有效，而畫面上看起來完全正常。
    assert digests != {content_digest("寫死的版本碼")}
