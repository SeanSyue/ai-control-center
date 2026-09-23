"""系統精靈：這台機器上的三件事，問一次就好。

`DECISIONS` 172【兩個精靈，各管各的層級】。合成一個是被否決過的：
**一個管全域、一個管單案，混在一起的時候使用者分不出「我現在改的是這個
案子，還是每個案子」**——而那一題答錯，下一個專案會安靜地繼承他以為
只設給這一個的東西。

跟專案精靈同一套規矩，所以這份檔案跟
`test_the_wizard_asks_three_questions.py` 長得很像。**那是刻意的**：
兩個精靈的形狀不一樣的話，使用者要學兩次。
"""
from __future__ import annotations

import dataclasses

import pytest

from app.services import system_wizard as wizard
from app.services import ui_preferences
from app.services.ai_settings import AISettingsStore
from app.services.settings_homes import HOMES


def test_it_asks_two_questions_and_runs_one_check() -> None:
    """第①題不是問句——**是檢查**（兩支 CLI 裝了沒、登入了沒）。

    他答不了那一題，機器答得了。所以 `Answers` 上只有兩個欄位，
    而多出來的第三個欄位就是精靈開始長胖。
    """
    fields = [f.name for f in dataclasses.fields(wizard.Answers)]
    assert fields == ["depth", "look"], (
        f"系統精靈的題目變成 {fields}。第①題是 CLI 檢查，不是欄位。"
    )


def test_every_question_has_a_home_and_the_home_is_on_the_list() -> None:
    known = {home.what for home in HOMES}
    for field in dataclasses.fields(wizard.Answers):
        home = wizard.WRITES_TO.get(field.name)
        assert home, f"「{field.name}」沒有說它寫到哪"
        assert home in known, f"「{home}」不在 settings_homes.HOMES 裡"


def test_the_two_wizards_have_the_same_shape() -> None:
    """兩個精靈都是「三選一 ＋ 自訂」。

    形狀一樣，使用者學一次就好。**這一條不是美感**：他 2026-08-24 說
    「讓他選幾個 NODE 那邊也要給個自訂這個選項」，而同一句話對系統精靈
    也成立——一個有自訂、一個沒有，是他每次都要重新確認的東西。
    """
    from app.services import project_wizard

    assert len(wizard.DEPTHS) == len(project_wizard.CHOICES) == 4
    # 2026-08-24：那四個深淺改成**代號**了（它們會存進 ai-settings.json，
    # 而拿一句給人看的話當存進檔案的值，等於介面翻譯的那天所有既有設定都失效）。
    # **要守的事沒有變**：第四個模式要存在、要是一個真的值。
    from app.services.ai_settings import PROFILE_CUSTOM, profile_label

    assert wizard.DEPTH_CUSTOM == PROFILE_CUSTOM
    assert profile_label(wizard.DEPTH_CUSTOM) == "自訂"
    assert wizard.DEPTH_CUSTOM in wizard.DEPTHS


def test_custom_starts_from_standard_not_from_blank() -> None:
    """跟專案精靈的「自訂」從兩人起跳同一個理由：

    從空白開始的自訂，第一格就會卡住——而這裡的「空白」更糟，
    因為 `AIExecutionSettings` 有二十幾個欄位。
    """
    assert wizard.settings_for(wizard.DEPTH_CUSTOM) == wizard.settings_for(
        wizard.DEPTH_STANDARD
    )


def test_the_three_depths_are_actually_different() -> None:
    """**三個選項要真的不一樣。**

    改壞的樣子很安靜：三張出廠設定的名字不同、內容一樣，於是使用者選了
    「深入」，付了他以為的代價，拿到的是「省額度」的結果。
    """
    shapes = {
        depth: dataclasses.astuple(wizard.settings_for(depth))
        for depth in (wizard.DEPTH_THRIFTY, wizard.DEPTH_STANDARD, wizard.DEPTH_DEEP)
    }
    assert len(set(shapes.values())) == 3, "有兩個深淺選項套出來一模一樣"


def test_deeper_costs_more_not_less() -> None:
    """順序要對。

    「深入」如果比「標準」給的輪數還少，畫面上完全看不出來——
    使用者只會覺得「這個工具很不穩定」。
    """
    rounds = [
        wizard.settings_for(d).planning_rounds
        for d in (wizard.DEPTH_THRIFTY, wizard.DEPTH_STANDARD, wizard.DEPTH_DEEP)
    ]
    assert rounds == sorted(rounds), f"深淺跟輪數對不上：{rounds}"


def test_an_unknown_depth_raises_rather_than_quietly_defaulting() -> None:
    """**不回預設值。**他選了「深入」卻拿到「省額度」，而畫面上兩者長得一樣。"""
    with pytest.raises(wizard.SystemWizardError):
        wizard.settings_for("超深")
    with pytest.raises(wizard.SystemWizardError):
        wizard.settings_for("")


def test_the_depth_names_match_what_the_settings_store_understands() -> None:
    """**字串要跟 `AISettingsStore.profile()` 認得的一模一樣。**

    對不上的話 `profile()` 會落到最省的那一張——不是報錯，是**安靜地
    給他別的東西**。這條是這份檔案裡最容易腐爛的一條：兩邊各自寫死一份
    中文名字，改一邊不會有人發現。
    """
    store = AISettingsStore()
    for depth in (wizard.DEPTH_THRIFTY, wizard.DEPTH_STANDARD, wizard.DEPTH_DEEP):
        assert store.profile(depth).profile_name == depth, (
            f"「{depth}」在 AISettingsStore 那邊叫別的名字，"
            "套下去會靜靜落到最省的那一張"
        )


def test_the_look_values_are_the_ones_the_screen_understands() -> None:
    """同一個病的第二個入口：畫面認得的是 `night` / `day`。"""
    assert set(wizard.LOOKS) == set(ui_preferences.MODES)
    with pytest.raises(wizard.SystemWizardError):
        wizard.save_look("dark")


def test_the_wizard_does_not_import_the_screen() -> None:
    """**服務層不准認識畫面層。**

    `save_look` 原本是 `from app.ui.web_panel import THEME`——會動，但那條
    相依會讓系統精靈變成只有開得起視窗才測得動的東西，而它的邏輯一行畫面
    都沒有。`QSettings` 是 `QtCore`，不需要 `QApplication`。
    """
    import ast
    from pathlib import Path

    # **用語法樹不用 grep。**這份檔案的說明裡就寫著「不 import `app.ui`」——
    # 字串比對會打到那句話本身，而那正是這個專案已經為此拿掉過三條檢查的
    # 同一種混淆：**提到一個東西不等於用了它。**
    tree = ast.parse(Path("app/services/system_wizard.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    offenders = [name for name in imported if name.startswith("app.ui")]
    assert not offenders, f"系統精靈開始 import 畫面層了：{offenders}"


def test_both_answers_land(tmp_path, monkeypatch) -> None:
    """存進去了沒有——不是「有沒有報錯」，是**再讀一次讀不讀得到**。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    store = AISettingsStore()
    result = wizard.apply(
        wizard.Answers(depth=wizard.DEPTH_DEEP, look=ui_preferences.MODE_DAY), store
    )

    assert not result.problems, result.problems
    assert len(result.written) == 2
    from app.services.ai_settings import PROFILE_DEEP

    assert store.load_global().profile_name == PROFILE_DEEP
    assert ui_preferences.mode() == ui_preferences.MODE_DAY
    ui_preferences.set_mode(ui_preferences.MODE_NIGHT)


def test_custom_sends_him_to_the_settings_page(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    store = AISettingsStore()
    assert wizard.apply(wizard.Answers(depth=wizard.DEPTH_CUSTOM), store).goes_to_settings_page
    assert not wizard.apply(wizard.Answers(depth=wizard.DEPTH_STANDARD), store).goes_to_settings_page
