"""工作線模式（ROADMAP P10 Stage E）。

「模式」聽起來像一個新功能，而這些測試大半是在確認它**不是**——它就是把
「幾格、每格誰、每格什麼身分」存成一筆資料。剩下的斷言都在防同一類事故：
套用一個模式會換掉整個陣容，所以任何安靜的失敗，看起來都跟「套用成功了、
只是長得一樣」一模一樣，而下一步就是有人按下開始執行去花錢。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.lineup import AgentSlot, Lineup
from app.core.orchestrator import Orchestrator
from app.i18n import t
from app.services.lineup_presets import (
    LineupPreset,
    LineupPresetStore,
    PresetError,
    apply_preset,
    built_in_presets,
)

def store(tmp_path: Path) -> LineupPresetStore:
    return LineupPresetStore(config_dir=tmp_path / "config")


# ---- the built-ins -------------------------------------------------------


def test_the_three_shipped_tables_are_the_ones_that_were_decided() -> None:
    """出廠三張：一人（小事）／兩人（一般）／三人（重要），名稱執行／複查／核准。

    2026-08-21 定案。**出廠只給三張**——選項超過三個會讓人不決定；要更多自己
    複製去改。畫面上另外有一個「自訂」，那不是第四張表格，是「從頭自己排」。

    這條也守著名稱：定案寫明預設名稱是那三個中文詞。改回英文代號的話，使用者
    第一次打開看到的是 `author`／`reviewer`，而他不是工程師。
    """
    shapes = {preset.lineup.roles for preset in built_in_presets()}

    assert len(built_in_presets()) == 3
    # 2026-08-25 全部加了「者」：以前的「執行／複查／核准」跟**職責下拉**
    # 的四個字一模一樣，於是三人模式排出來的那一列，名字欄與職責欄印著
    # 同一個字。名字是人的稱呼，職責是工作的種類。
    #
    # 問 `t()` 不寫死字面：寫死的話，改字就要改測試，而那是
    #「改使用者看得到的字來讓檢查過」的入口。
    worker, reviewer, approver = (
        t("role.worker"), t("role.reviewer"), t("role.approver")
    )
    assert (worker,) in shapes
    assert (worker, reviewer) in shapes
    assert (worker, reviewer, approver) in shapes


def test_every_built_in_is_a_shape_the_engine_can_actually_run() -> None:
    """A shipped mode that stores a seat nothing dispatches would be the
    original P10 lie, shipped as a feature."""
    from app.core.lineup import DUTY_WORK, VALID_DUTIES

    for preset in built_in_presets():
        assert len(preset.lineup) >= 1, preset.name
        for slot in preset.lineup:
            # 守的東西沒變，判準變了：以前比對的是「角色名在不在那份三個名字
            # 的清單裡」，而那份清單把「上限三格」變成了實質規格。現在每一格
            # 都會被派工，要說得出來的是它**做哪一種事**。
            assert slot.duty in VALID_DUTIES, (preset.name, slot.role)
            if slot.duty == DUTY_WORK:
                assert slot.provider == "claude", (preset.name, slot.role)


def test_a_built_in_cannot_be_overwritten(tmp_path: Path) -> None:
    """They are the way back to the shipped shapes. Shadowed, there is none.

    Refused rather than stored-and-ignored: a save that reports success and
    changes nothing is worse than a refusal.
    """
    presets = store(tmp_path)
    name = built_in_presets()[0].name

    with pytest.raises(PresetError, match="內建模式"):
        presets.save(name, Lineup.classic())


def test_a_saved_preset_never_shadows_a_built_in(tmp_path: Path) -> None:
    # Belt and braces: even a hand-edited file must not take the name.
    presets = store(tmp_path)
    name = built_in_presets()[0].name
    presets.path.write_text(
        json.dumps(
            {
                "version": 1,
                "presets": [
                    {
                        "name": name,
                        "slots": [{"role": "author", "provider": "codex"}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    found = presets.find(name)

    assert found is not None
    assert found.built_in is True
    assert found.lineup.roles == built_in_presets()[0].lineup.roles
    # And it does not appear twice. `find` returning the built-in is not
    # enough - a duplicate entry still reaches the page, which would draw two
    # rows with the same name and different shapes.
    names = [preset.name for preset in presets.all()]
    assert names.count(name) == 1


# ---- saving --------------------------------------------------------------


def test_a_saved_mode_keeps_the_seats_and_their_names(tmp_path: Path) -> None:
    presets = store(tmp_path)
    lineup = Lineup.classic().with_planner().with_identity(
        "author", "你是熟悉境外開戶法規的資深顧問。"
    )

    presets.save("開戶案專用", lineup, "三格，作者換成法規顧問")

    reloaded = store(tmp_path).find("開戶案專用")
    assert reloaded is not None
    assert reloaded.lineup.roles == ("planner", "author", "reviewer")
    assert (
        reloaded.lineup.identity_for("author")
        == "你是熟悉境外開戶法規的資深顧問。"
    )


def test_a_saved_mode_does_not_carry_a_conversation_id(tmp_path: Path) -> None:
    """A mode is reused across projects; a session id belongs to one.

    Carried along, applying the mode elsewhere would point a seat at a
    conversation that vendor has never heard of in a project that never had it.
    """
    presets = store(tmp_path)
    lineup = Lineup.classic().with_session("author", "claude-abc")

    presets.save("帶著 session", lineup)

    # Asserted against the **file**, not the reloaded object. The reader
    # ignores an unknown key, so a leak would round-trip to "" and look fine
    # while a global file on this machine quietly held a project's
    # conversation id.
    raw = presets.path.read_text(encoding="utf-8")
    assert "claude-abc" not in raw

    reloaded = store(tmp_path).find("帶著 session")
    assert reloaded is not None
    assert reloaded.lineup.session_for_role("author") == ""


def test_saving_the_same_name_replaces_rather_than_duplicates(
    tmp_path: Path,
) -> None:
    presets = store(tmp_path)
    presets.save("我的", Lineup.classic())
    presets.save("我的", Lineup.classic().with_planner())

    mine = [p for p in presets.all() if p.name == "我的"]
    assert len(mine) == 1
    assert mine[0].lineup.roles == ("planner", "author", "reviewer")


def test_an_empty_name_or_an_empty_lineup_is_refused(tmp_path: Path) -> None:
    presets = store(tmp_path)

    with pytest.raises(PresetError):
        presets.save("   ", Lineup.classic())
    with pytest.raises(PresetError):
        presets.save("空的", Lineup.of([]))


def test_one_broken_entry_costs_that_entry_only(tmp_path: Path) -> None:
    presets = store(tmp_path)
    presets.save("好的", Lineup.classic())
    raw = json.loads(presets.path.read_text(encoding="utf-8"))
    raw["presets"].insert(0, {"name": "壞的"})  # no slots
    presets.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    names = [p.name for p in presets.saved()]

    assert names == ["好的"]


# ---- applying ------------------------------------------------------------


def test_applying_a_mode_keeps_this_project_s_own_conversations(
    tmp_path: Path,
) -> None:
    """The mode says who does what; the project says which conversation."""
    current = Lineup.classic().with_session("author", "claude-1").with_session(
        "reviewer", "codex-1"
    )
    preset = LineupPreset(
        "三格", "", Lineup.classic().with_planner()
    )

    applied = apply_preset(preset, current)

    assert applied.roles == ("planner", "author", "reviewer")
    assert applied.session_for_role("author") == "claude-1"
    assert applied.session_for_role("reviewer") == "codex-1"
    assert applied.session_for_role("planner") == ""


def test_a_seat_the_mode_drops_takes_its_binding_with_it(
    tmp_path: Path,
) -> None:
    # Not a loss to guard against: the seat is genuinely gone, and keeping a
    # binding for a seat nobody sits in would be a record of nothing.
    current = Lineup.classic().with_planner().with_session("planner", "codex-9")
    preset = LineupPreset("兩格", "", Lineup.classic())

    applied = apply_preset(preset, current)

    assert applied.roles == ("author", "reviewer")
    assert applied.for_role("planner") is None


def test_applying_a_mode_carries_the_names_it_was_saved_with() -> None:
    preset = LineupPreset(
        "開戶案",
        "",
        Lineup.classic().with_identity("author", "你是法規顧問。"),
    )

    applied = apply_preset(preset, Lineup.classic())

    assert applied.identity_for("author") == "你是法規顧問。"


def test_a_mode_may_name_its_seats_whatever_the_user_wants() -> None:
    """**這一條反過來了。**

    2026-08-22 之前守的是「模式裡出現引擎跑不動的角色名要被拒絕」，而那份
    「跑得動的名字」只有三個。使用者的原話：「角色那邊不是寫死，而是給使用者
    一個預設，然後也給他們自由編輯的權力」。

    【角色表格】那條定案的不變式本來就寫著**角色名與連線都可改**。名字是
    使用者的；引擎看的是 `duty`。所以現在守的是相反的事：一個叫「粗剪」的
    位子必須排得進去。
    """
    from app.core.lineup import DUTY_APPROVE, DUTY_PLAN, DUTY_WORK
    from app.services.lineup_presets import LineupPreset, apply_preset

    preset = LineupPreset(
        name="剪片流程",
        description="",
        lineup=Lineup.of(
            [
                AgentSlot("發想", "codex", duty=DUTY_PLAN),
                AgentSlot("粗剪", "claude", duty=DUTY_WORK),
                AgentSlot("上字幕", "claude", duty=DUTY_WORK),
                AgentSlot("審閱", "codex", duty=DUTY_APPROVE),
            ]
        ),
    )

    applied = apply_preset(preset, Lineup.classic())

    assert applied.roles == ("發想", "粗剪", "上字幕", "審閱")


# --- 會寫入的座位配哪一家（2026-09-01 起兩家都可以）-------------------------
#
# 下拉選單那條路 2026-09-01 起不再擋（`test_a_writing_seat_can_be_handed_to_codex_now`），
# 但**套用模式是另一條路**，而它原本只檢查角色跑不跑得動、不檢查誰坐在上面。
#
# 最實際的觸發情境不是有人手改 JSON，是 `WRITING_ROLES` 哪天多一個角色——
# 那一刻，所有以前存過的模式都可能變成不合法的，而它們畫出來完全正常，
# 只在按下開始執行之後才炸。ROADMAP 說得很清楚：要在排陣容的當下就擋。


def test_a_preset_may_put_codex_in_a_writing_seat_now() -> None:
    """⛔ 這一條 2026-09-01 之前是反過來的：套用一份「Codex 坐在執行那一格」
    的模式會被 `PresetError` 擋下來。

    老闆那天看到同型的拒絕訊息時說：

        > 哪個智障說只有 claude 可以寫的，我之前明明就說都可以

    查完他是對的——那條規則沒有人問過他，源頭是引擎「沒填 Codex 的沙箱值」
    的一段註解。填上 `-s workspace-write` 之後這道閘門永遠不成立，整條拿掉。

    這一條翻面留著，因為**它證明那道閘門真的走了**：留一個空的位置的話，
    下一個人會以為那件事還被守著。
    """
    from app.services.lineup_presets import LineupPreset, apply_preset

    both_codex = LineupPreset(
        name="兩格都是 Codex",
        description="",
        lineup=Lineup.of(
            [AgentSlot("author", "codex"), AgentSlot("reviewer", "codex")]
        ),
    )

    applied = apply_preset(both_codex, Lineup.classic())

    assert [s.provider for s in applied] == ["codex", "codex"], (
        f"套不上去：{[(s.role, s.provider) for s in applied]}"
    )


def test_a_preset_with_codex_in_a_read_only_seat_is_fine() -> None:
    # 反方向：修法不可以變成「模式一律不准用 Codex」——審查者本來就是 Codex。
    from app.services.lineup_presets import LineupPreset, apply_preset

    good = LineupPreset(
        name="正常的模式",
        description="",
        lineup=Lineup.of(
            [AgentSlot("author", "claude"), AgentSlot("reviewer", "codex")]
        ),
    )

    applied = apply_preset(good, Lineup.classic())

    assert [slot.provider for slot in applied] == ["claude", "codex"]


def test_every_built_in_preset_satisfies_the_rule() -> None:
    """內建的兩組不可以自己違規。

    它們是使用者最可能按下去的東西，而**一個內建就違規的規則，會讓人以為
    那條規則不是真的**。
    """
    from app.core.lineup import DUTY_WORK
    from app.services.lineup_presets import built_in_presets

    for preset in built_in_presets():
        for slot in preset.lineup:
            # 判準從「名字在不在那份寫死的清單裡」改成「它的職責是不是 work」
            # ——2026-08-22 起名字是使用者的，職責才是引擎看的。
            if slot.duty == DUTY_WORK:
                assert slot.provider == "claude", (
                    f"內建模式「{preset.name}」把 {slot.role} 給了 {slot.provider}"
                )


def test_the_panel_refuses_it_too_and_keeps_the_old_lineup(
    tmp_path, monkeypatch
) -> None:
    """走真正的那條路，而且失敗之後不可以留下半套陣容。

    `apply_preset` 會拋，而拋出來的當下 `self._lineup` 還沒被指派——這條把
    那件事釘住，因為「套用失敗但陣容被改了一半」比直接失敗更難查。
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.services.lineup_presets import LineupPreset
    from app.ui.lineup_web_panel import LineupWebPanel

    QApplication.instance() or QApplication([])
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    panel = LineupWebPanel(project)
    before = [(s.role, s.provider) for s in panel._lineup]

    # ⛔ 這一份 2026-09-01 之前是「Codex 坐在執行那一格」的模式，靠那條
    # 已經拿掉的規則去觸發失敗。這一條真正要釘住的**不是哪一條規則**，
    # 是**面板收到 `PresetError` 之後的行為**：說一句話，而且陣容原封不動。
    # 所以改成直接讓 `apply_preset` 拋，規則換了它也不會跟著壞。
    from app.services import lineup_presets as _presets

    bad = LineupPreset(name="壞掉的模式", description="", lineup=Lineup.classic())

    def _always_refuses(*_a, **_k):
        raise _presets.PresetError("這份模式套不上去（測試用）")

    monkeypatch.setattr(
        "app.ui.lineup_web_panel.apply_preset", _always_refuses, raising=False
    )
    monkeypatch.setattr(_presets, "apply_preset", _always_refuses)
    monkeypatch.setattr(panel._presets, "find", lambda name: bad)
    complaints: list[str] = []
    panel.action_failed.connect(complaints.append)

    panel.handle_action("apply_preset", "壞掉的模式")

    assert complaints, "套用了一個跑不起來的模式卻沒有說話"
    assert "套不上去" in complaints[0], complaints[0]
    assert [(s.role, s.provider) for s in panel._lineup] == before, (
        "套用失敗了，陣容卻被改掉一半 —— 那比直接失敗更難查"
    )
    panel.deleteLater()
