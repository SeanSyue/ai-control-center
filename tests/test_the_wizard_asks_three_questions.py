"""專案精靈：三題，不多不少，而且**不准自己發明存放位置**。

老闆 2026-08-21：

> 阿那些精靈設定下來的東西寫在哪　**會不會亂跑亂發散亂存**

`DECISIONS` 170【精靈不准自己發明存放位置】、176【每一層一份清單】。
清單在 `app/services/settings_homes.py`，這裡驗精靈有沒有踩出去。

## 為什麼「三題」本身要有東西守著

`門檻二-別人打得開.md` 那句：

> 這一條是精靈不會慢慢長胖的唯一機制。
> **沒有它，第七題、第八題會一題一題加回來。**

每一題單看都合理（「順便問一下要不要開通知？」），而第七題出現的那一天，
精靈就從「三題就能開始」變成「又一份要填的表格」——**那正是它要解決的問題**。
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.core.lineup import DUTY_APPROVE, DUTY_REVIEW, DUTY_WORK
from app.services import project_wizard as wizard
from app.services.settings_homes import HOMES, SCOPE_PROJECT


def test_the_wizard_asks_exactly_three_questions() -> None:
    """**多一題就變紅**，而變紅的時候要有人回答「為什麼這一題非問不可」。"""
    fields = [f.name for f in dataclasses.fields(wizard.Answers)]
    assert fields == ["sensitivity", "lineup", "rules"], (
        f"精靈的題目變成 {fields}。\n"
        "加一題之前先讀 `docs/門檻二-別人打得開.md` 裡那句："
        "沒有這條，第七題、第八題會一題一題加回來。"
    )


def test_every_question_has_a_home_and_the_home_is_on_the_list() -> None:
    """**每一題都要知道自己寫到哪，而那個位置要有人決定過。**"""
    known = {home.what for home in HOMES}
    for field in dataclasses.fields(wizard.Answers):
        home = wizard.WRITES_TO.get(field.name)
        assert home, f"第「{field.name}」題沒有說它寫到哪"
        assert home in known, (
            f"「{home}」不在 settings_homes.HOMES 裡。"
            "精靈不准自己發明存放位置——先決定它屬於哪一個功能。"
        )


def test_custom_is_a_fourth_mode_not_an_empty_answer() -> None:
    """老闆 2026-08-24 明著要的第四個模式。

    改壞的樣子就是它 2026-08-24 之前的樣子：引擎對「自訂」回空字串，
    畫面把空字串讀成「沒有選中」——於是那個模式**在畫面上不存在**。
    """
    assert wizard.CHOICE_CUSTOM in wizard.CHOICES
    assert wizard.CHOICE_CUSTOM.strip()
    assert len(wizard.CHOICES) == 4


def test_custom_starts_from_the_two_person_shape_not_from_blank() -> None:
    """「自訂」不是「從空白開始」。

    一張空表格對一個新使用者沒有意義——他會看到第一格，然後不知道要填什麼。
    從兩人起跳，他改的是**已經跑得動的東西**。
    """
    seeded = wizard.lineup_for(wizard.CHOICE_CUSTOM)
    assert list(seeded) == list(wizard.lineup_for(wizard.CHOICE_TWO))
    assert len(seeded) >= 2


def test_each_preset_carries_a_role_setting() -> None:
    """老闆的原話：「選了三人後導入預設設定，**那這個設定裡面要有一個預設的
    角色設定**，然後使用者可以更改」。

    每一格都要有名字、職責、供應商——少任何一個，他打開陣容頁看到的就是
    一張要自己從頭填的表。
    """
    for choice in wizard.CHOICES:
        for slot in wizard.lineup_for(choice):
            assert slot.role.strip(), f"{choice} 有一格沒有名字"
            assert slot.duty in (DUTY_WORK, DUTY_REVIEW, DUTY_APPROVE), (
                f"{choice} 有一格的職責是 {slot.duty!r}"
            )
            assert slot.provider.strip(), f"{choice} 有一格沒有配廠商"


def test_every_preset_has_someone_who_can_write_files() -> None:
    """**每一張表格都要有至少一格做得了事。**

    這一條是負向驗證逼出來的。原本上面那條只驗「職責是三種之一」，而
    W8（把出廠表格的職責拿掉）**是綠的**——因為空職責不會變成無效值，
    它會安靜地變成 `review`：

    > 每一格都明著寫職責。不寫的話它會從名字推——而「執行」「複查」這種
    > 中文名字推不出來，會落到安全的那一邊（複查），於是一張叫「一人」的
    > 表格排出來之後**沒有任何一格能寫檔案**，而畫面上完全看不出為什麼。
    >     —— `lineup_presets.BUILT_IN` 自己的註解

    也就是說：壞掉的樣子不是「值不合法」，是「值合法但全部一樣」。
    驗值域驗不到，要驗的是**這張表格排出來做不做得了事**。
    """
    for choice in wizard.CHOICES:
        duties = [slot.duty for slot in wizard.lineup_for(choice)]
        assert DUTY_WORK in duties, (
            f"「{choice}」這張表格裡沒有任何一格能寫檔案（職責是 {duties}）。"
            "它跑得完，但什麼都不會產出，而畫面上看不出為什麼。"
        )


def test_an_unknown_choice_raises_rather_than_quietly_defaulting() -> None:
    """**不回預設值。**

    回預設值的話，畫面上多一個打錯字的選項，使用者會拿到一張他沒有選的
    表格，而完全看不出來——這正是【有預設值的分類等於沒有分類】那條。
    """
    with pytest.raises(wizard.WizardError):
        wizard.preset_for("four")
    with pytest.raises(wizard.WizardError):
        wizard.preset_for("")


# --- 真的跑一次，看它到底在硬碟上留下什麼 --------------------------------

def _declared_project_paths() -> tuple[str, ...]:
    return tuple(h.where for h in HOMES if h.scope == SCOPE_PROJECT)


def _is_declared(relative: str) -> bool:
    for declared in _declared_project_paths():
        if declared.endswith("/"):
            if relative.startswith(declared):
                return True
        elif relative == declared:
            return True
    return False


def test_the_wizard_writes_nothing_outside_its_declared_homes(tmp_path) -> None:
    """**這一條是這份檔案存在的理由。**

    上面那些驗的是宣告（「精靈說它會寫這三個地方」），這一條驗的是**實際
    落在硬碟上的東西**。兩者會分家：某一層底下的服務順手多寫一個檔，
    精靈的宣告不會變，而 `.aicc/` 底下就多了一個誰都沒決定過的東西。
    """
    project = tmp_path / "案子"
    project.mkdir()

    wizard.apply(
        wizard.Answers(
            sensitivity="client",
            lineup=wizard.CHOICE_THREE,
            rules="不要碰 `legacy/` 底下的東西。",
        ),
        project,
    )

    strays = sorted(
        path.relative_to(project).as_posix()
        for path in project.rglob("*")
        if path.is_file()
        and not _is_declared(path.relative_to(project).as_posix())
    )
    assert not strays, (
        "精靈在清單以外的地方留下了東西：\n  "
        + "\n  ".join(strays)
        + "\n\n先決定它屬於哪一個功能，再加進 `app/services/settings_homes.py`。"
    )


def test_all_three_answers_actually_land(tmp_path) -> None:
    """存進去了沒有——不是「有沒有報錯」，是**再讀一次讀不讀得到**。"""
    from app.services.project_registry import ProjectRegistry
    from app.services.session_registry import SessionRegistry

    project = tmp_path / "案子"
    project.mkdir()
    result = wizard.apply(
        wizard.Answers(
            sensitivity="sensitive_kyc",
            lineup=wizard.CHOICE_THREE,
            rules="每一次都要跑負向驗證。",
        ),
        project,
    )

    assert not result.problems, result.problems
    assert len(result.written) == 3

    assert ProjectRegistry.get_sensitivity(project) == "sensitive_kyc"
    assert len(SessionRegistry(project).load_lineup()) == 3
    rules = project / ".aicc" / "knowledge" / "PROJECT_RULES.md"
    assert rules.is_file() and "負向驗證" in rules.read_text(encoding="utf-8")


def test_skipping_the_rules_writes_no_file_at_all(tmp_path) -> None:
    """第③題可跳過，而跳過**不是寫一個空檔案**。

    空的 `PROJECT_RULES.md` 會被派工當成「這個專案有規矩」，於是每一個 NODE
    都收到一份標題底下什麼都沒有的規矩——而那比沒有更糟：它教 AI 把那個
    標題當真。
    """
    project = tmp_path / "案子"
    project.mkdir()
    wizard.apply(wizard.Answers(rules="   \n  "), project)
    assert not (project / ".aicc" / "knowledge" / "PROJECT_RULES.md").exists()


def test_custom_sends_him_to_the_lineup_page(tmp_path) -> None:
    """選「自訂」之後接下來發生什麼：先塞兩人的形狀，**然後帶他去陣容頁**。"""
    project = tmp_path / "案子"
    project.mkdir()
    assert wizard.apply(wizard.Answers(lineup=wizard.CHOICE_CUSTOM), project).goes_to_lineup_page
    assert not wizard.apply(wizard.Answers(lineup=wizard.CHOICE_TWO), project).goes_to_lineup_page


def test_one_question_failing_does_not_take_the_other_two_down(tmp_path, monkeypatch) -> None:
    """**一題失敗不影響另外兩題。**

    綁成「全部成功或全部不做」的話，一個寫不進去的規矩檔會擋掉整個專案設定
    ——而那時候他只知道「精靈失敗了」，不知道是哪一題。
    """
    project = tmp_path / "案子"
    project.mkdir()

    def refuse(*_args, **_kwargs):
        raise OSError("硬碟滿了")

    monkeypatch.setattr(wizard, "write_project_rules", refuse)
    result = wizard.apply(
        wizard.Answers(sensitivity="client", rules="有規矩"), project
    )

    assert result.problems, "規矩沒存成，卻一個字都沒說"
    assert any("規矩" in p for p in result.problems), result.problems
    # 另外兩題照樣落地
    assert len(result.written) == 2
