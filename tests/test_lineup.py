"""The line-up model, and the two shapes the old one could not express.

ROADMAP P10's claim is specific: modelling an agent as {role, provider,
session_id} covers same-vendor pairs *and* chains longer than two. Both are
asserted here end to end through TimelineStore, because a model that only holds
the shape in memory has not removed the blocker - the run has to come back off
disk still saying who was on it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.lineup import (
    ROLE_AUTHOR,
    ROLE_FINISHER,
    ROLE_REVIEWER,
    AgentSlot,
    Lineup,
    LineupError,
)
from app.services.timeline_store import TimelineStore


def test_a_slot_needs_a_role_and_a_provider_that_exists() -> None:
    # Providers are a closed set because each one needs a CLI adapter behind
    # it; a line-up naming a provider AICC cannot launch is a crash later
    # rather than a composition someone was allowed to make.
    with pytest.raises(LineupError):
        AgentSlot(ROLE_AUTHOR, "gemini")
    with pytest.raises(LineupError):
        AgentSlot("", "claude")

    slot = AgentSlot("  Author ", " Claude ", "  abc  ")
    assert (slot.role, slot.provider, slot.session_id) == ("author", "claude", "abc")


def test_classic_is_the_pairing_aicc_has_always_run() -> None:
    lineup = Lineup.classic("c-1", "x-1")

    assert [(s.role, s.provider) for s in lineup] == [
        (ROLE_AUTHOR, "claude"),
        (ROLE_REVIEWER, "codex"),
    ]
    assert lineup.session_for_provider("claude") == "c-1"
    assert lineup.session_for_provider("codex") == "x-1"
    assert lineup.is_same_provider is False


def test_two_claude_sessions_are_expressible(tmp_path: Path) -> None:
    # The shape the old two-named-fields model could not hold: both positions
    # filled by one vendor, each with its own session.
    lineup = Lineup.of(
        [
            AgentSlot(ROLE_AUTHOR, "claude", "systems"),
            AgentSlot(ROLE_REVIEWER, "claude", "design"),
        ]
    )
    store = TimelineStore(tmp_path)
    run_id = store.create_run("task", lineup=lineup)

    loaded = next(r for r in store.list_runs() if r.run_id == run_id)

    assert [(s.role, s.provider, s.session_id) for s in loaded.lineup] == [
        ("author", "claude", "systems"),
        ("reviewer", "claude", "design"),
    ]
    assert loaded.lineup.is_same_provider is True
    assert loaded.lineup.session_for_role(ROLE_REVIEWER) == "design"


def test_a_three_position_relay_is_expressible(tmp_path: Path) -> None:
    # Claude -> Codex -> Claude. The old shape had exactly two named fields, so
    # a third position had nowhere to go at all - which is why a fix that only
    # taught it about same-vendor pairs would have had to be redone.
    lineup = Lineup.of(
        [
            AgentSlot(ROLE_AUTHOR, "claude", "a"),
            AgentSlot(ROLE_REVIEWER, "codex", "b"),
            AgentSlot(ROLE_FINISHER, "claude", "c"),
        ]
    )
    store = TimelineStore(tmp_path)
    run_id = store.create_run("task", lineup=lineup)

    loaded = next(r for r in store.list_runs() if r.run_id == run_id)

    assert len(loaded.lineup) == 3
    assert [s.role for s in loaded.lineup] == ["author", "reviewer", "finisher"]
    assert [s.session_id for s in loaded.lineup] == ["a", "b", "c"]


def test_asking_by_provider_is_lossy_once_a_vendor_holds_two_seats() -> None:
    # Documented on purpose. "The Claude one" stops identifying a single agent
    # the moment two positions are Claude, so the compatibility accessors
    # answer with the first and anything that needs to know which one has to
    # ask by role.
    lineup = Lineup.of(
        [
            AgentSlot(ROLE_AUTHOR, "claude", "first"),
            AgentSlot(ROLE_REVIEWER, "claude", "second"),
        ]
    )

    assert lineup.session_for_provider("claude") == "first"
    assert lineup.session_for_role(ROLE_REVIEWER) == "second"


def test_a_run_written_before_p10_still_says_who_was_on_it(tmp_path: Path) -> None:
    # There are real runs on disk with no `agents` key. Reading them as an
    # empty line-up would erase history that is still in the file.
    store = TimelineStore(tmp_path)
    run_dir = store.runs_dir / "legacy_run"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "version": 1,
                "run_id": "legacy_run",
                "created_at": "2026-08-01T00:00:00+08:00",
                "updated_at": "2026-08-01T00:00:00+08:00",
                "status": "PASS",
                "task_preview": "舊的執行",
                "claude_session_id": "old-c",
                "codex_session_id": "old-x",
            }
        ),
        encoding="utf-8",
    )

    loaded = next(r for r in store.list_runs() if r.run_id == "legacy_run")

    assert loaded.claude_session_id == "old-c"
    assert loaded.codex_session_id == "old-x"
    assert [(s.role, s.provider) for s in loaded.lineup] == [
        ("author", "claude"),
        ("reviewer", "codex"),
    ]


def test_the_flat_keys_are_still_written_for_older_builds(tmp_path: Path) -> None:
    store = TimelineStore(tmp_path)
    run_id = store.create_run("task", claude_session_id="c-9", codex_session_id="x-9")

    payload = json.loads(
        (store.runs_dir / run_id / "run.json").read_text(encoding="utf-8")
    )

    assert payload["claude_session_id"] == "c-9"
    assert payload["codex_session_id"] == "x-9"
    assert payload["agents"] == [
        # `credential` is empty because AICC does not supply one yet - the CLI
        # uses whatever it already has, and writing "login" here would be a
        # guess. See AgentSlot.credential.
        # `credential` and `identity` are empty for different reasons.
        # `credential` because AICC does not supply one yet; `identity`
        # because nobody has renamed this seat - a blank one means "use the
        # shipped 「你是⋯⋯」", which is also what clearing the box does.
        # `duty` 是 2026-08-22 加的：位子**做哪一種事**，跟它叫什麼名字分開。
        # 【角色表格】那條定案要求角色名可改，而引擎又必須知道要不要給它寫檔
        # 權限——名字自由、職責封閉，是那條定案唯一能落地的形狀。
        #
        # 舊檔案沒有這一格，讀進來時照舊的角色名推回去（author→work、
        # reviewer→review），所以既有專案升級之後職責不會消失。
        #
        # `writes_files` 空的，理由又跟上面那幾個都不一樣：它問的是「這一格
        # 動不動得了你的檔案」，而**這一版（2026-08-25）才第一次問**。空字串
        # 代表「還沒有人被問過」，答案照他已經記下來的那一欄（職責）走——
        # 所以舊檔升級之後，每一格碰得到的東西跟升級前一模一樣。
        # `parent` 空的（2026-08-27 加的）：它問的是「這一格接在哪一格後面」，
        # 而空字串代表**接前面那一格**——也就是今天畫面上那一排。出廠陣容
        # 一格都沒有被拉過線，所以這裡兩個都是空的。**這一欄一定要出現在
        # 這份紀錄裡**：這一份是事後回看「那一輪是誰跑的」的憑據，而
        # 「東西怎麼流」是那句話的一半。
        {
            "role": "author", "provider": "claude", "session_id": "c-9",
            "credential": "", "identity": "", "duty": "work",
            "writes_files": "", "parent": "",
        },
        {
            "role": "reviewer", "provider": "codex", "session_id": "x-9",
            "credential": "", "identity": "", "duty": "review",
            "writes_files": "", "parent": "",
        },
    ]


def test_updating_by_provider_name_still_works(tmp_path: Path) -> None:
    # Every current caller says claude_session_id=/codex_session_id=. They keep
    # working, and the line-up is what actually gets stored.
    store = TimelineStore(tmp_path)
    run_id = store.create_run("task")

    store.update_run(run_id, claude_session_id="later-c", codex_session_id="later-x")

    loaded = next(r for r in store.list_runs() if r.run_id == run_id)
    assert loaded.claude_session_id == "later-c"
    assert loaded.codex_session_id == "later-x"
    assert loaded.lineup.session_for_role(ROLE_AUTHOR) == "later-c"


def test_a_seat_records_which_kind_of_credential_paid_for_it(tmp_path: Path) -> None:
    # Not the key - the kind. It is only knowable while the run happens: once a
    # bill arrives, nothing else on disk says which runs were charged to a key.
    lineup = Lineup.of(
        [
            AgentSlot(ROLE_AUTHOR, "claude", "a", "api_key"),
            AgentSlot(ROLE_REVIEWER, "codex", "b", "login"),
        ]
    )
    store = TimelineStore(tmp_path)
    run_id = store.create_run("task", lineup=lineup)

    loaded = next(r for r in store.list_runs() if r.run_id == run_id)

    assert [s.credential for s in loaded.lineup] == ["api_key", "login"]


def test_an_unrecognised_credential_kind_is_refused() -> None:
    # The field exists to be read back later as evidence. A free-form string
    # would let a typo become a category nobody can query for.
    with pytest.raises(LineupError):
        AgentSlot(ROLE_AUTHOR, "claude", "", "oauth2")


def test_binding_a_session_keeps_the_credential(tmp_path: Path) -> None:
    # Assigning a conversation must not quietly forget how the seat is paid
    # for; `with_session` rebuilds the slot, so it has to carry it through.
    lineup = Lineup.of([AgentSlot(ROLE_AUTHOR, "claude", "old", "api_key")])

    updated = lineup.with_session(ROLE_AUTHOR, "new")

    assert updated.slots[0].session_id == "new"
    assert updated.slots[0].credential == "api_key"


def test_an_unreadable_slot_costs_that_slot_not_the_line_up() -> None:
    lineup = Lineup.from_list(
        [
            {"role": "author", "provider": "claude", "session_id": "ok"},
            {"role": "reviewer", "provider": "gemini"},   # provider AICC cannot run
            "not a dict",
            {"role": "", "provider": "codex"},            # no role
            {"role": "finisher", "provider": "codex", "session_id": "fine"},
        ]
    )

    assert [(s.role, s.provider) for s in lineup] == [
        ("author", "claude"),
        ("finisher", "codex"),
    ]
