"""The composer's rules, checked where they are enforced rather than drawn.

The page enforces most of these too (disabled buttons, locked dropdowns). That
is not enough on its own: a stale page, a hand-edited sessions.json, or a run
record read back from disk can all present a line-up the page would not have
let anyone build.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.i18n import t  # noqa: E402
from app.core.lineup import AgentSlot, Lineup  # noqa: E402
from app.core.orchestrator import Orchestrator  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402


def _panel(tmp_path: Path, monkeypatch) -> LineupWebPanel:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    panel = LineupWebPanel(project)
    # The scan walks every local session on disk; these tests are about the
    # line-up rules, not discovery.
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    return panel


def test_the_seat_cap_is_what_the_engine_dispatches(tmp_path, monkeypatch) -> None:
    # Not a layout number. A seat that can be composed but never runs is the
    # same thing as a switch that changes nothing - the design session's own
    # reason for refusing the export_scan toggle.
    panel = _panel(tmp_path, monkeypatch)
    assert len(panel._lineup) == 2

    panel.handle_action("add_slot", "")

    assert len(panel._lineup) == 3
    # 原本那兩格保留原名。以位置改名的話，「加一格」會變成「每個人的工作
    # 往後挪一格」。新的接在最後面。
    assert panel._lineup.roles[:2] == ("author", "reviewer")

    # 這條原本叫「上限＝引擎派得動的數量」，理由是「排得出來卻永遠不會跑的
    # 位子，等於一個什麼都不改的開關」。
    #
    # 那個理由現在由 `Orchestrator.seated_roles` 直接保證——它回傳的就是
    # 陣容本身，沒有任何寫死的清單在過濾。守著那件事的是
    # `test_lineup_wiring.py`，不是這裡；這裡只要確認加一格真的多一格、
    # 而且沒有人被改名。

    # 反方向：任何一格都刪得掉，刪掉的就是被點名的那一格。
    #
    # 以前這裡是「刪中間那格不會有事，只有選配的規劃者刪得掉」——那條下限
    # 擋掉了【出廠三張】明著給的一人表格。
    before = panel._lineup.roles
    panel.handle_action("remove_slot", "1")
    assert panel._lineup.roles == (before[0], before[2])


def test_a_stored_third_seat_is_still_shown(tmp_path, monkeypatch) -> None:
    # The cap governs composing, not displaying. Drawing only the first two of
    # a stored three would be the same lie pointing the other way, and the
    # harder one to notice.
    panel = _panel(tmp_path, monkeypatch)
    SessionRegistry(panel._project).save_lineup(
        Lineup.of(
            [
                AgentSlot("author", "claude", "a"),
                AgentSlot("reviewer", "codex", "b"),
                AgentSlot("finisher", "claude", "c"),
            ]
        )
    )
    panel.set_project(panel._project)

    slots = panel.collect_state()["lineup"]["slots"]

    assert len(slots) == 3
    # 2026-08-25 從 2 改成 3，而**它以前是 2 這件事本身就是那個 bug**。
    #
    # `finisher` 的職責是 `approve`，而 `SEQUENCE_JOBS` 那張表以前用出廠
    # **名字**當鍵、又沒有核准那一種——所以這一格拿不到工作，畫面把它畫成
    # 虛線紅框加「不會被執行」。**而 `steps/approval.py` 真的會派工給它。**
    #
    # 也就是說這條斷言不是在抓那個 bug，是在**保證它繼續發生**。
    # 改成 3 之後，一份三格陣容的每一格都認得出自己這一輪要做什麼。
    assert panel.collect_state()["sequence"]["executed_slots"] == 3
    jobs = panel.collect_state()["sequence"]["jobs"]
    assert jobs["2"], "核准那一格還是拿不到工作"


def test_two_seats_is_the_floor(tmp_path, monkeypatch) -> None:
    panel = _panel(tmp_path, monkeypatch)

    panel.handle_action("remove_slot", "0")
    assert len(panel._lineup) == 1, "刪到剩一格是合法的：【出廠三張】給了一人表格"

    panel.handle_action("remove_slot", "0")
    assert len(panel._lineup) == 1, "最後一格不准刪，那會留下一個按了不會動的專案"


def test_a_writing_seat_can_be_handed_to_codex_now(tmp_path, monkeypatch) -> None:
    """⛔ 這一條 2026-09-01 之前是反過來的：把會寫檔的那一格換成 Codex 會被擋。

    老闆那天的原話：

        > 哪個智障說只有 claude 可以寫的，我之前明明就說都可以，
        > 只是在預設設定的時候說過那個 node 用 codex 剛好只有審閱、
        > 不需要寫資料而已

    查完他是對的——沒有人問過他。源頭是引擎「沒填 Codex 的沙箱值」那段註解，
    而 2026-09-01 填上了 `-s workspace-write`。

    翻面留著而不是刪掉：**空的位置會讓下一個人以為那件事還被守著。**
    真正接手的守衛是
    `tests/test_a_writing_codex_gets_a_writing_sandbox.py`——它問的是
    「會寫檔的步驟有沒有拿到寫得了檔的沙箱」。
    """
    panel = _panel(tmp_path, monkeypatch)
    assert panel._lineup.slots[0].duty == "work", "第一格不是執行，這條沒有對照組"

    panel.handle_action("set_slot_provider", "0:codex")

    assert panel._lineup.slots[0].provider == "codex", (
        "把執行那一格換成 Codex 又被擋下來了"
    )


def test_changing_provider_drops_the_other_vendors_session(
    tmp_path, monkeypatch
) -> None:
    # A session id belongs to the provider it came from. Carrying it across
    # would leave the seat pointing at a conversation that vendor has never
    # heard of.
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("set_slot_session", "1:codex-session")
    assert panel._lineup.slots[1].session_id == "codex-session"

    panel.handle_action("set_slot_provider", "1:claude")

    assert panel._lineup.slots[1].provider == "claude"
    assert panel._lineup.slots[1].session_id == ""


def test_assignments_survive_a_reload(tmp_path, monkeypatch) -> None:
    panel = _panel(tmp_path, monkeypatch)

    panel.handle_action("set_slot_session", "0:claude-1")

    assert SessionRegistry(panel._project).load_lineup().session_for_role(
        "author"
    ) == "claude-1"


def test_moving_a_seat_takes_its_duty_with_it(tmp_path, monkeypatch) -> None:
    # 老闆 2026-08-27 拍板：**箭頭搬的是整個 node**（名字、廠牌、對話、身分、
    # 職責、寫檔開關、線一起走）。這一條在這裡只確認那件事在最常見的那一份
    # 陣容上成立——一欄一條斷言的完整版在
    # `tests/test_the_arrow_moves_the_whole_node.py`。
    #
    # 在那之前這一支換的只有坐在位子上的廠牌與對話，職責留在位置上，所以
    # 出廠的兩人陣容（執行＝Claude、複查＝Codex）一移動就會把 Codex 放上會
    # 寫檔案的位子，然後被擋——**他第一次按這顆箭頭就動不了**。
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("set_slot_session", "0:claude-1")

    panel.handle_action("move_slot", "0:1")

    slots = panel._lineup.slots
    assert [s.role for s in slots] == ["reviewer", "author"]
    # 職責跟著它自己那一格走：`author` 到哪裡都還是做事的那一格。
    assert [s.duty for s in slots] == ["review", "work"]
    assert slots[1].session_id == "claude-1", "坐在位子上的人沒有跟著移動"


def test_the_seat_duties_come_from_the_engine(tmp_path, monkeypatch) -> None:
    # Not a copy in the page. The duties swap between sequences, and a second
    # copy would drift - with the screen being the more convincing of the two.
    panel = _panel(tmp_path, monkeypatch)

    sequence = panel.collect_state()["sequence"]

    assert sequence["jobs"] == Orchestrator.sequence_for(
        panel._workflow_mode(), panel._lineup.roles
    )["jobs"]
    assert set(sequence["jobs"]) == {
        str(i) for i in range(len(panel._lineup))
    }

    # And the duties follow the seats, not the positions. Asserted as a shift
    # rather than against fixed strings, because this project's configured
    # sequence decides which strings they are - and the property being checked
    # holds in either sequence: whatever the author was doing at seat 0, it is
    # the same job at seat 1 once a planner sits in front of them.
    author_duty = sequence["jobs"]["0"]
    reviewer_duty = sequence["jobs"]["1"]

    panel.handle_action("add_slot", "")
    after = panel.collect_state()["sequence"]["jobs"]

    # 加一格之後，原本那兩格的工作內容一個字都不能變——那就是「職責跟著
    # 位子走，不跟著位置走」。以前新的插在最前面，所以同一個性質是用位移
    # 來斷言的；現在接在後面，斷言的是不動。
    assert after.get("0") == author_duty
    assert after.get("1") == reviewer_duty


def test_the_execution_bar_summary_is_the_real_order(
    tmp_path, monkeypatch
) -> None:
    panel = _panel(tmp_path, monkeypatch)

    assert panel.provider_sequence() == ["claude", "codex"]

    panel.handle_action("set_slot_provider", "1:claude")
    assert panel.provider_sequence() == ["claude", "claude"]


def test_every_seat_reports_the_two_axes_the_reading_needs(
    tmp_path, monkeypatch
) -> None:
    # Vendor alone is a coarse proxy for independence: two Claudes on different
    # material can be more independent than two vendors reading one repo. The
    # page needs both signals or it will hand out a green light for a pairing
    # that shares everything.
    panel = _panel(tmp_path, monkeypatch)

    for slot in panel.collect_state()["lineup"]["slots"]:
        assert "provider" in slot
        assert "in_project" in slot
        assert "can_write" in slot


def test_moving_a_seat_does_not_rename_the_reviewer_into_a_dead_role(
    tmp_path, monkeypatch
) -> None:
    """箭頭只**重排**名字，一個字都不改寫。

    它以前照一份寫死的 (author, reviewer, finisher) 依位置改名，而排了規劃者
    之後那份清單偏一格：按一次前移，複查那一格就被改名成 `finisher`——一個
    沒有任何步驟會派工的角色。**審查從此不再發生，而畫面還是三格健康的
    座位。**

    2026-08-27 老闆拍板「箭頭搬的是整個 node」之後，名字是**跟著那一格走**的
    （`Lineup.moved` 只換位置，不動任何欄位），所以改名這件事在這條路上根本
    不存在。這一條還在，是因為它守的是那個症狀，不是那個實作：搬完之後這三個
    名字要一個不多一個不少，而且沒有人被改成 `finisher`。
    """
    panel = _panel(tmp_path, monkeypatch)
    # 直接排一個三格陣容。以前這裡用 add_slot（它只會加規劃者），而
    # 2026-08-22 之後加一格是加一個通用節點，就測不到「搬動會不會改名」了。
    panel._lineup = Lineup.classic().with_planner()
    panel._persist()
    panel.handle_action("set_slot_session", "0:planner-chat")
    assert panel._lineup.roles == ("planner", "author", "reviewer")

    panel.handle_action("move_slot", "1:-1")

    assert panel._lineup.roles == ("author", "planner", "reviewer"), (
        "搬動改到的不只是順序"
    )
    assert "finisher" not in panel._lineup.roles
    # 那一格連同它的對話一起搬過去了，這才是箭頭答應的事。
    assert panel._lineup.for_role("planner").session_id == "planner-chat"


def test_only_the_optional_seat_reports_itself_as_removable(
    tmp_path, monkeypatch
) -> None:
    # The backend refuses 移除 on the author and the reviewer. A button that
    # is offered and then silently refused is worse than a disabled one, so
    # the page is told which seats can actually go.
    panel = _panel(tmp_path, monkeypatch)

    # 2026-08-22 起**每一格都刪得掉**，只剩「不准刪到零格」。以前只有選配的
    # 規劃者能刪，理由是「至少要兩格」——而那條下限擋掉了【出廠三張】明著
    # 給的一人表格。
    two_seats = panel.collect_state()["lineup"]["slots"]
    assert [s["can_remove"] for s in two_seats] == [True, True]

    panel.handle_action("add_slot", "")
    three_seats = panel.collect_state()["lineup"]["slots"]
    assert [s["can_remove"] for s in three_seats] == [True, True, True]


def test_a_seat_can_be_renamed_and_cleared_from_the_composer(
    tmp_path, monkeypatch
) -> None:
    """Clearing the box *is* the restore (ROADMAP P10 Stage D).

    There is no separate reset flag, on purpose: a flag and a text box are two
    states that can disagree, and the disagreement would read as 「我按了還原
    但它還是用我打的那個」.
    """
    panel = _panel(tmp_path, monkeypatch)

    panel.handle_action("set_slot_identity", "0:你是熟悉境外開戶法規的資深顧問。")
    assert panel._lineup.identity_for("author") == "你是熟悉境外開戶法規的資深顧問。"
    assert SessionRegistry(panel._project).load_lineup().identity_for(
        "author"
    ) == "你是熟悉境外開戶法規的資深顧問。"

    panel.handle_action("set_slot_identity", "0:")
    assert panel._lineup.identity_for("author") == ""


def test_a_rename_survives_the_edits_that_happen_around_it(
    tmp_path, monkeypatch
) -> None:
    """Every path that rebuilds a slot must carry the identity through.

    Each of these is a silent loss: the run still works, it just speaks as
    somebody else, and nothing on screen says the box emptied itself.
    """
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("set_slot_identity", "1:你是很嚴格的審查員。")

    panel.handle_action("set_slot_session", "1:codex-session")
    assert panel._lineup.identity_for("reviewer") == "你是很嚴格的審查員。"

    panel.handle_action("set_slot_provider", "1:claude")
    assert panel._lineup.identity_for("reviewer") == "你是很嚴格的審查員。"

    panel.handle_action("move_slot", "1:-1")
    # 整個 node 一起搬（老闆 2026-08-27 拍板），所以那一格連同它的名字與那句
    # 「你是⋯」一起換到前面去了——**查得到它，而且還是他打的那一句。**
    assert panel._lineup.roles == ("reviewer", "author"), "前提壞了：沒有移動"
    assert panel._lineup.identity_for("reviewer") == "你是很嚴格的審查員。"

    panel.handle_action("add_slot", "")
    assert panel._lineup.identity_for("reviewer") == "你是很嚴格的審查員。"


def test_the_composer_is_given_the_titles_a_rename_would_replace(
    tmp_path, monkeypatch
) -> None:
    # A box showing only the first of several would promise something the
    # engine does not do - see test_step_persona.
    panel = _panel(tmp_path, monkeypatch)

    slots = panel.collect_state()["lineup"]["slots"]

    assert slots[0]["identity"] == ""
    assert len(slots[0]["shipped_identities"]) >= 3
    assert "你是此專案的開發工程師。" in slots[0]["shipped_identities"]
    assert slots[0]["identity_max"] > 0


def test_the_composer_can_apply_and_save_a_mode(tmp_path, monkeypatch) -> None:
    # Stage E through the panel: the mode has to reach `sessions.json`, not
    # just the in-memory line-up, or it is gone on the next focus switch.
    from app.services.lineup_presets import LineupPresetStore

    panel = _panel(tmp_path, monkeypatch)
    panel._presets = LineupPresetStore(config_dir=tmp_path / "presets")
    panel.handle_action("set_slot_session", "0:claude-1")

    panel.handle_action("apply_preset", t("preset.three.name"))

    assert panel._lineup.roles == (t("role.worker"), t("role.reviewer"), t("role.approver"))
    stored = SessionRegistry(panel._project).load_lineup()
    assert stored.roles == (t("role.worker"), t("role.reviewer"), t("role.approver"))

    panel.handle_action("set_slot_identity", "0:你是法規顧問。")
    panel.handle_action("save_preset", "開戶案專用")

    saved = panel._presets.find("開戶案專用")
    assert saved is not None
    assert saved.lineup.identity_for(t("role.worker")) == "你是法規顧問。"
    # 職責也要一起存下來。少了它，套用之後**沒有任何一格能寫檔案**——使用者
    # 自己取的名字推不出職責，會落到安全的那一邊（複查），而畫面上完全看不出
    # 為什麼那個專案再也做不出東西。
    assert [slot.duty for slot in saved.lineup] == ["work", "review", "approve"]


def test_a_mode_that_cannot_be_applied_says_so_out_loud(
    tmp_path, monkeypatch
) -> None:
    """Silence here is the worst option available.

    Applying a mode replaces every seat, so 「按了沒反應」 and 「套用了，只是
    長得一樣」 look identical - and the next thing the user does is start a
    paid run against a line-up they think they changed.
    """
    panel = _panel(tmp_path, monkeypatch)
    complaints: list[str] = []
    panel.action_failed.connect(complaints.append)

    panel.handle_action("apply_preset", "沒有這個模式")

    assert complaints and "沒有這個模式" in complaints[0]
    assert panel._lineup.roles == ("author", "reviewer")


def test_the_active_mode_is_derived_not_remembered(
    tmp_path, monkeypatch
) -> None:
    """A remembered name keeps saying 「三格模式」 over two seats.

    Derived from the shape, it stops claiming that the moment a seat changes -
    which is the only behaviour that stays true without anyone maintaining it.
    """
    from app.services.lineup_presets import LineupPresetStore

    panel = _panel(tmp_path, monkeypatch)
    panel._presets = LineupPresetStore(config_dir=tmp_path / "presets")

    # 專案還沒套過任何一張表格，所以現在的形狀不是出廠的任何一張——
    # 那個時候它必須說「沒有」，不准挑一張最像的來寫。
    assert panel.collect_state()["lineup"]["active_preset"] == ""

    panel.handle_action("apply_preset", t("preset.three.name"))
    assert panel.collect_state()["lineup"]["active_preset"] == (
        t("preset.three.name")
    )

    # Change one seat by hand and it stops claiming to be that mode.
    # 改第二格（複查）而不是第一格：這一條要問的是別的事，動第一格會把它
    # 跟「執行那一格配誰」混在一起（2026-09-01 起兩家都配得上）
    # 執行，把它改成 Codex 會被擋下來——那樣這條測試量到的是「改不動」，
    # 不是「改了之後不再自稱那張表格」。
    panel.handle_action("set_slot_provider", "1:claude")
    assert panel.collect_state()["lineup"]["active_preset"] == ""


def test_two_modes_with_the_same_vendors_but_different_seats_are_told_apart(
    tmp_path, monkeypatch
) -> None:
    """Vendors alone do not identify a mode.

    Found by negative control: dropping the role comparison left every
    assertion green, because in the obvious cases the vendor lists differ too.
    A saved 「規劃 → 作者」 mode has the same vendors as the built-in
    「作者 → 審查」, and matching on vendors alone would label a two-seat
    development line-up with the name of a mode it is not.
    """
    from app.core.lineup import AgentSlot, Lineup
    from app.services.lineup_presets import LineupPresetStore

    panel = _panel(tmp_path, monkeypatch)
    panel._presets = LineupPresetStore(config_dir=tmp_path / "presets")
    panel._presets.save(
        "規劃兩格",
        Lineup.of(
            [AgentSlot("planner", "claude"), AgentSlot("author", "claude")]
        ),
    )
    # Two Claudes, so no built-in matches on vendors either - which is what
    # makes the saved one the first thing a vendors-only comparison would hit.
    panel.handle_action("set_slot_provider", "1:claude")

    assert panel._lineup.roles == ("author", "reviewer")
    assert [s.provider for s in panel._lineup] == ["claude", "claude"]

    assert panel.collect_state()["lineup"]["active_preset"] == "", (
        "兩格作者+審查被當成了「規劃兩格」——那兩個模式只是剛好用同樣的廠牌"
    )


# --- 改名字與選職責（2026-08-22）-------------------------------------------
#
# 引擎 2026-08-22 開放了任意節點數、自由命名、四選一的職責，**但畫面按不到**：
# 後端沒有 `set_slot_role` 也沒有 `set_slot_duty`，而且推給頁面的資料裡連
# `duty` 這一欄都沒有。所以那兩個控制項就算畫出來也接不上任何東西。
#
# 交接文件裡老闆的原話：「**能力做出來了卻按不到，跟沒做一樣。**」


def test_a_seat_can_be_renamed_through_the_panel(tmp_path, monkeypatch) -> None:
    """改名字要真的存進 `sessions.json`，不是只改記憶體裡那一份。

    只改記憶體的話，下一次切換專案回來就沒了——而使用者以為他改好了。
    """
    panel = _panel(tmp_path, monkeypatch)

    panel.handle_action("set_slot_role", "0:發想")

    assert panel._lineup.roles[0] == "發想"
    stored = SessionRegistry(panel._project).load_lineup()
    assert stored.roles[0] == "發想"


def test_renaming_a_seat_keeps_everything_else_about_it(
    tmp_path, monkeypatch
) -> None:
    """改名字只改名字。

    這一格的職責、廠商、對話、身分都是使用者一項一項設的。改個名字順手弄丟
    其中一項的話，**執行照跑、不報錯，只是跑起來的是別人**。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("set_slot_provider", "0:claude")
    panel.handle_action("set_slot_identity", "0:你是法規顧問。")
    panel.handle_action("set_slot_duty", "0:work")

    panel.handle_action("set_slot_role", "0:實作")

    slot = panel._lineup.for_role("實作")
    assert slot is not None
    assert slot.provider == "claude"
    assert slot.identity == "你是法規顧問。"
    assert slot.duty == "work"


def test_a_duplicate_name_is_refused_out_loud(tmp_path, monkeypatch) -> None:
    """撞名要說出來，不是安靜地不改。

    整條線是靠名字指人的（派工、駁回、蓋章、討論指名）。同名的話「退給它」
    會有兩個答案——而按了沒反應跟改好了長得一模一樣。
    """
    panel = _panel(tmp_path, monkeypatch)
    complaints: list[str] = []
    panel.action_failed.connect(complaints.append)
    second = panel._lineup.roles[1]

    panel.handle_action("set_slot_role", f"0:{second}")

    assert complaints, "撞名了卻一個字都沒說"
    assert panel._lineup.roles[0] != second


def test_a_seat_duty_can_be_chosen_and_an_unknown_one_is_refused(
    tmp_path, monkeypatch
) -> None:
    """職責四選一，**引擎只看這個**。

    不認得的值要拒絕而不是退回預設值：退回預設值的話，使用者選了一個東西、
    按下去沒有錯誤訊息、而那一格變成別的職責，他不會發現。
    """
    panel = _panel(tmp_path, monkeypatch)
    complaints: list[str] = []
    panel.action_failed.connect(complaints.append)

    panel.handle_action("set_slot_duty", "1:approve")
    assert panel._lineup.slots[1].duty == "approve"

    panel.handle_action("set_slot_duty", "1:老闆")
    assert complaints, "不認得的職責被安靜地吞掉了"
    assert panel._lineup.slots[1].duty == "approve", "而且還被改掉了"


def test_the_page_is_told_what_each_seat_currently_is(
    tmp_path, monkeypatch
) -> None:
    """職責與身分要推給頁面。

    改得了但看不到現值，等於改不了：那個下拉不知道要顯示哪一個選項，
    而使用者只能靠記憶。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("set_slot_duty", "0:plan")
    panel.handle_action("set_slot_identity", "0:你是發想者。")

    first = panel.collect_state()["lineup"]["slots"][0]

    assert first["duty"] == "plan"
    assert first["identity"] == "你是發想者。"


def test_the_page_is_told_which_sequence_this_is(tmp_path, monkeypatch) -> None:
    """`sequence.mode` 是**代號**，不是那句翻譯過的標籤。

    老闆 2026-08-27：

    > 那計畫序列的要鎖定，然後開發序列的用下拉式選單，畢竟是不同功能

    畫面靠這個欄位分流。**不能靠 `label`**：那是 `t()` 出來的字，
    英文版上線那天判斷會永遠不成立，**而畫面看起來完全正常**——
    這一頁已經為「拿中文當查詢的鍵」壞過一次。
    """
    from app.services.ai_settings import AISettingsStore

    panel = _panel(tmp_path, monkeypatch)
    assert panel.collect_state()["sequence"]["mode"] == "development"

    store = AISettingsStore(panel._project)
    settings = store.load_project()
    settings.workflow_mode = "planning"
    store.save_project(settings)
    panel._lineup = panel._lineup  # 不動陣容，只換序列

    assert panel.collect_state()["sequence"]["mode"] == "planning", (
        "換成計畫序列之後畫面拿到的還是 development —— "
        "那一格會給他一個下拉，而那一輪的角色分派不是他排的"
    )


def test_every_run_setting_arrives_with_its_own_name(tmp_path, monkeypatch) -> None:
    """那 22 格推到畫面上的時候要帶著**人話的名字**。

    畫面只拿得到 `key`，而 `claude_model` 不是一句人話。
    沒有這個欄位，AI 執行設定那一段會變成一排原始鍵名。

    **而且名字要來自那唯一一份宣告**（`run_settings_options.LABELS`），
    不是畫面自己抄一份——這一頁已經為手抄的表壞過兩次
    （座位上限、`gate_floor`）。原生面板讀的是同一支 `label_for()`。
    """
    from app.services import run_settings_options as opts

    panel = _panel(tmp_path, monkeypatch)
    controls = panel.collect_state()["lineup"]["run_settings"]["controls"]
    assert controls, "一個控制項都沒推"

    wrong = [
        f"{c['key']}：推的是 {c.get('label')!r}，應該是 {opts.label_for(c['key'])!r}"
        for c in controls
        if c.get("label") != opts.label_for(c["key"])
    ]
    assert not wrong, "這幾格推過去的名字不對：\n  " + "\n  ".join(wrong)

    raw = [c["key"] for c in controls if c.get("label") == c["key"]]
    assert not raw, f"這幾格推的是原始鍵名，畫面上會直接印出來：{raw}"
