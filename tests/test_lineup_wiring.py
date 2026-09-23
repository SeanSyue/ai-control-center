"""The two things P10 exists to allow, checked where they actually have to work.

Stage 1 gave the shape. These cover the two layers that had the vendor baked in
beside it: the registry (what a project is bound to) and the orchestrator (who
a step actually runs as).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.cli_runner import CliResult
from app.core.lineup import (
    CLASSIC_ROLES,
    PLANNED_ROLES,
    ROLE_AUTHOR,
    ROLE_PLANNER,
    ROLE_REVIEWER,
    AgentSlot,
    Lineup,
)
from app.i18n import t  # noqa: E402
from app.core.orchestrator import Orchestrator
from app.services.session_registry import SessionRegistry


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n測試。\n", encoding="utf-8")
    return project


# --- Stage 2: the registry ---------------------------------------------------


def test_registry_round_trips_a_same_vendor_lineup(tmp_path: Path) -> None:
    registry = SessionRegistry(make_project(tmp_path))
    registry.save_lineup(
        Lineup.of(
            [
                AgentSlot(ROLE_AUTHOR, "claude", "systems"),
                AgentSlot(ROLE_REVIEWER, "claude", "design"),
            ]
        )
    )

    loaded = SessionRegistry(registry.project_path).load_lineup()

    assert [(s.role, s.provider, s.session_id) for s in loaded] == [
        ("author", "claude", "systems"),
        ("reviewer", "claude", "design"),
    ]


def test_a_sessions_json_written_before_p10_keeps_its_bindings(
    tmp_path: Path,
) -> None:
    # These files hold bindings a user made by hand. Reading them as "no
    # line-up" would silently unbind every existing project.
    project = make_project(tmp_path)
    (project / ".aicc" / "sessions.json").write_text(
        json.dumps(
            {
                "version": 1,
                "selected": {"claude": "old-c", "codex": "old-x"},
                "sessions": {"claude": [], "codex": []},
            }
        ),
        encoding="utf-8",
    )

    registry = SessionRegistry(project)

    assert registry.selected_session_id("claude") == "old-c"
    assert registry.selected_session_id("codex") == "old-x"
    assert [(s.role, s.provider) for s in registry.load_lineup()] == [
        ("author", "claude"),
        ("reviewer", "codex"),
    ]


def test_selected_is_still_written_for_older_builds(tmp_path: Path) -> None:
    registry = SessionRegistry(make_project(tmp_path))
    registry.save_lineup(Lineup.classic("c-1", "x-1"))

    payload = json.loads(registry.path.read_text(encoding="utf-8"))

    assert payload["selected"] == {"claude": "c-1", "codex": "x-1"}
    assert payload["lineup"][0]["role"] == "author"


def test_binding_a_session_goes_through_the_lineup(tmp_path: Path) -> None:
    # Regression: `selected` became a derived view, and upsert(select=True) was
    # still writing it directly - so the binding was computed, then thrown away
    # on save with nothing to indicate it. The library's own binding test is
    # what caught it.
    from app.services.session_registry import SessionEntry

    registry = SessionRegistry(make_project(tmp_path))
    registry.upsert(
        SessionEntry(provider="claude", session_id="bound-1", title="t", project_path=""),
        select=True,
    )

    assert registry.selected_session_id("claude") == "bound-1"
    assert registry.load_lineup().session_for_role(ROLE_AUTHOR) == "bound-1"


# --- Stage 3: the orchestrator -----------------------------------------------


def _record_runs(orchestrator: Orchestrator, session_ids: list[str]) -> list[list[str]]:
    """Capture each CLI invocation, handing back a different session each time."""
    calls: list[list[str]] = []
    remaining = list(session_ids)

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        calls.append(list(arguments))
        return CliResult(
            command=arguments[0],
            success=True,
            return_code=0,
            stdout=json.dumps(
                {"result": "PASS", "session_id": remaining.pop(0) if remaining else ""}
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    return calls


def test_two_claude_agents_do_not_share_one_session(tmp_path: Path) -> None:
    # The failure this prevents is silent and expensive: with a single
    # `claude_session_id` field, the second Claude would have resumed the
    # first one's conversation - so the "independent reviewer" would have been
    # the author, holding the author's context, agreeing with itself.
    project = make_project(tmp_path)
    orchestrator = Orchestrator(
        project,
        lineup=Lineup.of(
            [
                AgentSlot(ROLE_AUTHOR, "claude", "author-session"),
                AgentSlot(ROLE_REVIEWER, "claude", "reviewer-session"),
            ]
        ),
    )
    calls = _record_runs(orchestrator, ["author-session", "reviewer-session"])

    orchestrator.run_claude()
    ok = CliResult("claude", True, 0, "x", "")
    orchestrator.run_codex_review(ok, ok, "diff", ["a.py"])

    resumed = [
        call[call.index("--resume") + 1] for call in calls if "--resume" in call
    ]
    assert resumed == ["author-session", "reviewer-session"]
    assert all(call[0] == "claude" for call in calls), (
        "reviewer 由 Claude 擔任，卻仍然去叫 codex"
    )


def test_the_default_lineup_is_what_aicc_has_always_run(tmp_path: Path) -> None:
    orchestrator = Orchestrator(make_project(tmp_path))

    assert orchestrator.provider_for_role(ROLE_AUTHOR) == "claude"
    assert orchestrator.provider_for_role(ROLE_REVIEWER) == "codex"


def test_a_writing_step_no_longer_refuses_codex(tmp_path: Path) -> None:
    """⛔ 這一條 2026-09-01 之前是反過來的：會寫檔的 Codex 步驟直接丟例外。

    老闆那天：「哪個智障說只有 claude 可以寫的，我之前明明就說都可以」。
    查完他是對的——那條規則沒有人問過他，源頭是 `orchestrator` 自己
    「沒填 Codex 的沙箱值」的一段註解。填上 `-s workspace-write` 之後，
    那個拒絕拿掉了。

    翻面留著而不是刪掉：**空的位置會讓下一個人以為那件事還被守著。**
    那個值本身由 `tests/test_a_writing_codex_gets_a_writing_sandbox.py` 盯著。
    """
    project = make_project(tmp_path)
    orchestrator = Orchestrator(
        project,
        lineup=Lineup.of([AgentSlot(ROLE_AUTHOR, "codex", "")]),
    )
    runs = _record_runs(orchestrator, [])

    orchestrator.run_claude()

    assert runs, "會寫檔的 Codex 步驟又被擋下來了（或根本沒有派工）"


def test_a_read_only_step_runs_as_whoever_holds_the_role(tmp_path: Path) -> None:
    # reviewer filled by Claude: the review has to actually invoke claude, not
    # codex with a Claude session id attached to it.
    project = make_project(tmp_path)
    orchestrator = Orchestrator(
        project,
        lineup=Lineup.of(
            [
                AgentSlot(ROLE_AUTHOR, "claude", "a"),
                AgentSlot(ROLE_REVIEWER, "claude", "b"),
            ]
        ),
    )
    calls = _record_runs(orchestrator, ["b"])

    ok = CliResult("claude", True, 0, "x", "")
    orchestrator.run_codex_review(ok, ok, "diff", ["a.py"])

    assert calls[0][0] == "claude"
    assert "--permission-mode" in calls[0]
    assert calls[0][calls[0].index("--permission-mode") + 1] == "plan"


def test_only_seats_the_engine_runs_get_a_duty() -> None:
    # The composer offers seats; the engine dispatches two. A seat with no duty
    # is a seat that will be stored, drawn, and never executed - so the seat
    # count offered has to come from what actually runs, not from what the data
    # model can hold.
    development = Orchestrator.sequence_for("development")
    planning = Orchestrator.sequence_for("planning")

    # **這幾句 2026-08-25 從中文字面改成問 `t()`。**
    # 那張表的字那天全改過（「寫程式」違反【不是所有使用者都寫程式】，
    # 「審查／複查」是同一件事的兩個字，而每一句都加了「這一輪：」前綴
    # 好跟旁邊的職責欄分開）。**測試寫死中文的話，改字就要改測試，
    # 而那正是「改使用者看得到的字來讓檢查過」的入口。**
    assert development["label"] == t("sequence.development")
    assert development["jobs"] == {
        "0": t("job.build"), "1": t("job.review")
    }
    # Planning swaps the duties without swapping the seats - the thing the
    # neutral seat names exist to avoid mis-stating.
    assert planning["jobs"] == {
        "0": t("job.read_plan"), "1": t("job.write_plan")
    }

    assert development["executed_slots"] == len(CLASSIC_ROLES)
    assert set(development["jobs"]) == {
        str(i) for i in range(len(CLASSIC_ROLES))
    }

    # With the planner seated the duties shift with the seats, because the
    # table is keyed by role. Keyed by index it would have relabelled the
    # author 「擬做法」 with nothing failing.
    planned = Orchestrator.sequence_for("development", PLANNED_ROLES)
    assert planned["jobs"] == {
        "0": t("job.plan"), "1": t("job.build"), "2": t("job.review")
    }
    assert planned["executed_slots"] == 3

    # A planner seated in *planning* mode has no duty there, and the composer
    # has to be able to say 不會被執行 rather than leave the line blank.
    planning_with_planner = Orchestrator.sequence_for("planning", PLANNED_ROLES)
    assert planning_with_planner["jobs"] == {
        "1": t("job.read_plan"), "2": t("job.write_plan")
    }
    assert planning_with_planner["executed_slots"] == 2


def test_every_writing_step_belongs_to_a_declared_writing_seat(
    tmp_path: Path,
) -> None:
    # 守的東西沒變：**會寫檔的那一步，必須來自一個宣告會寫檔的位子。**
    # 判準從「名字在不在 WRITING_ROLES 裡」改成「它的職責是不是 work」——
    # 2026-08-22 起名字是使用者的（他可以叫它「粗剪」），職責才是引擎看的。
    #
    # 一樣是**驅動真正的寫入步驟**來斷言，不是把常數讀回來給自己看。
    from app.core.lineup import DUTY_PLAN, DUTY_WORK

    orchestrator = Orchestrator(make_project(tmp_path))
    _record_runs(orchestrator, ["c-1"])

    orchestrator.run_claude()   # the writing step

    assert orchestrator.duty_for(ROLE_AUTHOR) == DUTY_WORK
    # 規劃者不准寫檔。一個會寫檔的規劃者已經變成第二個作者，那會讓後面那道
    # 審查變成審自己的東西。（原本這裡還接了一句「而且它也會解鎖『寫檔的
    # 位子配 Codex』」——那條限制 2026-09-01 拿掉了，理由見
    # `app/core/lineup.py`。這一條守的東西沒有變。）
    planned = Orchestrator(make_project(tmp_path / "planned"))
    assert planned.duty_for(ROLE_PLANNER) in ("", DUTY_PLAN)


def test_the_provider_shaped_accessors_still_answer(tmp_path: Path) -> None:
    orchestrator = Orchestrator(
        make_project(tmp_path),
        claude_session_id="c-1",
        codex_session_id="x-1",
    )

    assert orchestrator.claude_session_id == "c-1"
    assert orchestrator.codex_session_id == "x-1"
    assert orchestrator.session_for_role(ROLE_AUTHOR) == "c-1"


# ---- 現在跑到哪一格 -------------------------------------------------------
#
# 「亮錯格比不亮更糟，因為它是一個看起來很確定的錯誤」. So the index comes from
# the engine's own dispatch (Orchestrator.EXECUTED_ROLES), never from the
# workflow mode - the duties swap between sequences while the seats do not.


def test_the_engine_announces_which_seat_it_dispatches_to(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")

    orchestrator = Orchestrator(project)
    seen: list[str] = []
    orchestrator.on_role_start = seen.append
    orchestrator.runner.run = lambda *a, **k: CliResult(  # type: ignore[method-assign]
        command="codex", success=True, return_code=0, stdout="{}", stderr=""
    )

    orchestrator.run_codex_plan(task="做一件事")

    assert seen == [ROLE_REVIEWER]
    # The index the UI lights comes from this project's own line-up, not from
    # a fixed list: with a planner seated the reviewer is at 2, and a constant
    # would have lit the author.
    assert orchestrator.lineup.index_of_role(ROLE_REVIEWER) == 1
    assert Lineup.classic().with_planner().index_of_role(ROLE_REVIEWER) == 2


def test_a_refused_seat_is_never_announced(tmp_path: Path) -> None:
    """被拒絕的座位不准先亮起來——亮了就等於工作線上有一步沒發生過。

    ⛔ 這一條本來用「Codex 坐在會寫檔的位子」當觸發，而那個拒絕 2026-09-01
    拿掉了（老闆：「我之前明明就說都可以」）。**這一條守的東西沒有過期**，
    只是換一個還在的拒絕來觸發：陣容裡根本沒有那一格。
    """
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")

    orchestrator = Orchestrator(project)
    seen: list[str] = []
    orchestrator.on_role_start = seen.append
    orchestrator.lineup = Lineup(
        slots=[AgentSlot(role=ROLE_REVIEWER, provider="codex")]
    )

    with pytest.raises(NotImplementedError):
        orchestrator._run_role_prompt(ROLE_AUTHOR, "prompt", "task")

    assert seen == [], "被拒絕的座位不該被點亮"
