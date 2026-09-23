"""兩件「一份，不是每個畫面自己寫一份」。

都是頁面設計 2026-08-24 那一輪查到的，而且他們是對的。

## 一、敏感度為什麼是這個值（**安全關鍵**）

`get_sensitivity()` 在設定檔讀不到的時候會退到最嚴的那一級
（`sensitive_kyc`，fail-closed）。那是對的——**看不懂的設定不准當成寬鬆的**。

但畫面拿到的只有一個值，分不出三件事：

| 值是怎麼來的 | 使用者該有的反應 |
|---|---|
| 他自己選的 | 沒事 |
| 他還沒選過 | 可以選一下 |
| **設定檔讀不到** | **這個專案有東西壞了** |

他們的原話：

> 規格原本要把它標成「出廠預設，你還沒選過」，而使用者最合理的反應是把它
> 改低。**壞掉的專案會被手動降級，執行紀錄就外流了，而畫面上完全正常。**

## 二、廠牌在畫面上叫什麼

引擎給的永遠是小寫代號。畫面要寫「Claude Code」。

**同一個病已經得過一次**：`SENSITIVITY_LABELS` 是唯一來源，而
`settings_v1.html` 裡有自己寫的一份，兩邊的字已經不一樣了。
"""
from __future__ import annotations

import json

from app.services.project_registry import ProjectRegistry
from app.services.providers import display_name, known


def _project(tmp_path, name: str):
    folder = tmp_path / name
    folder.mkdir()
    ProjectRegistry.initialize_project(folder)
    return folder


def test_a_brand_new_project_says_it_was_never_chosen(tmp_path) -> None:
    project = _project(tmp_path, "全新的")
    assert ProjectRegistry.sensitivity_source(project) == ProjectRegistry.SOURCE_DEFAULT


def test_a_chosen_level_says_it_was_chosen(tmp_path) -> None:
    project = _project(tmp_path, "選過的")
    ProjectRegistry.set_sensitivity(project, "client")
    assert ProjectRegistry.sensitivity_source(project) == ProjectRegistry.SOURCE_CHOSEN


def test_an_unreadable_file_is_not_dressed_up_as_a_default(tmp_path) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：讀不到跟「還沒選過」給同一個答案。使用者看到「出廠預設」
    就把它改低，而那個專案的執行紀錄從那一刻起會被同步出去。
    """
    project = _project(tmp_path, "壞掉的")
    (project / ".aicc" / "project.json").write_text("{ 這不是 json", encoding="utf-8")

    assert ProjectRegistry.get_sensitivity(project) == "sensitive_kyc"
    assert (
        ProjectRegistry.sensitivity_source(project)
        == ProjectRegistry.SOURCE_UNREADABLE
    ), "讀不到卻說成「還沒選過」——使用者最合理的反應是把它改低"


def test_an_unknown_level_is_also_unreadable(tmp_path) -> None:
    """設定檔讀得動，但值不認得——同樣不是「他選的」。"""
    project = _project(tmp_path, "怪值的")
    target = project / ".aicc" / "project.json"
    target.write_text(json.dumps({"sensitivity": "top_secret"}), encoding="utf-8")

    assert ProjectRegistry.get_sensitivity(project) == "sensitive_kyc"
    assert (
        ProjectRegistry.sensitivity_source(project)
        == ProjectRegistry.SOURCE_UNREADABLE
    )


def test_the_two_answers_never_contradict_each_other(tmp_path) -> None:
    """`get_sensitivity()` 說「最嚴的」，`sensitivity_source()` 說「他選的」
    ——那種組合不准出現。

    這是這個專案已經得過的病：同一個事實兩份實作，各自演化。
    """
    for name, setup in (
        ("新", lambda p: None),
        ("選過", lambda p: ProjectRegistry.set_sensitivity(p, "client")),
        ("壞掉", lambda p: (p / ".aicc" / "project.json").write_text("{", encoding="utf-8")),
    ):
        project = _project(tmp_path, name)
        setup(project)
        level = ProjectRegistry.get_sensitivity(project)
        source = ProjectRegistry.sensitivity_source(project)
        if source == ProjectRegistry.SOURCE_UNREADABLE:
            assert level == "sensitive_kyc", f"{name}：說讀不到卻沒有 fail-closed"
        if source == ProjectRegistry.SOURCE_CHOSEN:
            assert level == "client", f"{name}：說是他選的，值卻不是他選的那個"


# --- 廠牌顯示名 -----------------------------------------------------------

def test_every_known_provider_has_a_display_name() -> None:
    for provider in known():
        name = display_name(provider)
        assert name != provider, f"{provider} 沒有顯示名，畫面上會出現小寫代號"
        assert name.strip()


def test_an_unknown_provider_falls_back_to_its_code_not_to_nothing() -> None:
    """**回代號本身，不回空字串。**

    空字串會讓畫面上出現一個沒有名字的下拉選項，而那比顯示 `gemini` 糟得多。
    也不丟例外：某一天多支援一個廠牌，畫面會在使用者打開陣容頁的那一刻炸掉。
    """
    assert display_name("gemini") == "gemini"


# 允許自己寫顯示名的地方，以及**為什麼**。放行要留下痕跡。
EXCUSED = {
    "providers.py": "定義那份對照表的地方，其他人都該叫它。",
    "i18n.py": "那些字實際住的地方，這裡是唯一的字面值來源。",
    "credential_entry.py": "它拿的是 i18n 的 key，不是字面值。",
    "conversation_search.py":
        "它那份表的 key 是**對話的來源**不是廠牌——`claude-local`、"
        "`claude-desktop`、`codex-app-server` 是同一個廠牌的不同存放位置，"
        "而 `display_name()` 回答的是「這個廠牌叫什麼」，是另一個問題。"
        "**但那兩個剛好等於廠牌名的要對齊**，見下一條。",
}


def test_the_excused_places_say_why() -> None:
    """放行不能變成免死金牌。"""
    for name, reason in EXCUSED.items():
        assert len(reason.strip()) >= 10, f"{name} 的放行理由太短"


def test_the_conversation_source_labels_have_not_drifted() -> None:
    """那兩個剛好等於廠牌名的，要跟唯一來源一樣。

    改壞的樣子就是它 2026-08-24 之前的樣子：`startup_diagnostics` 寫著
    「Codex」，而這裡寫「Codex CLI」——**同一個東西在同一個 app 裡兩個名字**。
    """
    from app.services.conversation_search import source_label, source_labels

    assert source_label("claude-local") == display_name("claude")
    assert source_label("codex-local-fallback") == display_name("codex")

    # 2026-08-24：這裡本來是兩張表在兩個檔案裡（短名給表格、長名給提示），
    # 而長名那一份**少了三個來源**。收成一份之後，每一個來源兩種名字都要有。
    for source in source_labels():
        assert source_label(source, long=True).strip(), f"{source} 沒有長名"
        assert source_label(source).strip(), f"{source} 沒有短名"


def test_nobody_writes_their_own_copy_of_the_display_names() -> None:
    """**同一個病不要得第二次。**

    `SENSITIVITY_LABELS` 是唯一來源，而 `settings_v1.html` 裡有自己寫的
    一份——兩邊的字已經不一樣了。廠牌名字剛做好，現在就把門關上。
    """
    import ast
    from pathlib import Path

    offenders: list[str] = []
    for path in Path("app").rglob("*.py"):
        if path.name in EXCUSED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and node.value in ("Claude Code", "Codex CLI")
            ):
                offenders.append(f"{path.as_posix()}:{node.lineno}")
    assert not offenders, (
        "這幾處自己寫了一份廠牌顯示名：\n  "
        + "\n  ".join(offenders)
        + "\n改叫 `app.services.providers.display_name()`。"
    )


# --- 拿中文當查詢的鍵，是這個專案得過三次的病 ----------------------------

def test_nothing_looks_things_up_by_a_translated_sentence() -> None:
    """**查詢的鍵不准是一句會被翻譯的話。**

    2026-08-24 一天之內量到三處：

    1. `project_wizard.WRITES_TO` 拿家的中文標籤當鍵——翻譯之後精靈說
       「我不知道這一題寫到哪」，在一個看起來完全正常的畫面上。
    2. `dashboard_web_panel.LANE_BY_STATUS` 拿狀態的中文當鍵——翻譯之後
       **每一個專案都會掉進 idle 那條泳道**，而畫面上完全正常。
    3. `settings_homes.Home.what` 同上。

    三個的共同形狀：**一份給人看的字，同時被當成程式的識別碼**。
    改掉那句話（翻譯、或只是改個用詞）就會斷，而斷掉的樣子都不是報錯。

    這條掃的是「模組層的表，key 是中文」——那是這個形狀最常見的長相。
    """
    import ast
    import re
    from pathlib import Path

    cjk = re.compile(r"[\u4e00-\u9fff]")
    # 這幾份裡的中文本來就不是介面文字（比對用、給 AI 看的、斷詞資料），
    # 拿它們當鍵是對的。
    NOT_INTERFACE = {
        "prompt_markers.py",
        "prompt_text.py",
        "chinese_text.py",
        "i18n.py",
        "prompt_builder.py",
    }

    # 用中文當鍵**是這張表的工作**：它認的正是「舊設定檔裡存的那個中文值」。
    # 拿掉它，每一個 2026-08-24 之前設定過的專案都會退回省額度。
    #
    # 放行的是**變數名**不是整個檔案——同一個檔案裡別的表照樣被看著。
    LEGITIMATELY_KEYED_BY_CHINESE = {"_LEGACY_PROFILE_NAMES"}

    offenders: list[str] = []
    for path in Path("app").rglob("*.py"):
        if path.name in NOT_INTERFACE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        inside_func: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for child in ast.walk(node):
                    inside_func.add(id(child))
        excused: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                getattr(target, "id", "") in LEGITIMATELY_KEYED_BY_CHINESE
                for target in node.targets
            ):
                for child in ast.walk(node):
                    excused.add(id(child))

        for node in ast.walk(tree):
            if id(node) in inside_func or id(node) in excused:
                continue
            # 字典：key 是中文
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and cjk.search(key.value)
                    ):
                        offenders.append(
                            f"{path.as_posix()}:{key.lineno} 用「{key.value[:12]}」當鍵"
                        )
                continue
            # 成對的元組（`(("等待決策", ...), ...)`）：第一格是中文。
            # **不只掃字典**——泳道那張表就是元組，而它是同一個病
            #（負向驗證 S6 第一次跑是綠的才發現）。
            if not isinstance(node, (ast.Tuple, ast.List)):
                continue
            for item in node.elts:
                if not isinstance(item, (ast.Tuple, ast.List)) or len(item.elts) < 2:
                    continue
                head = item.elts[0]
                if (
                    isinstance(head, ast.Constant)
                    and isinstance(head.value, str)
                    and cjk.search(head.value)
                ):
                    offenders.append(
                        f"{path.as_posix()}:{head.lineno} 用「{head.value[:12]}」當鍵"
                    )
    assert not offenders, (
        "這幾張表拿中文當查詢的鍵：\n  "
        + "\n  ".join(offenders)
        + "\n\n**一份給人看的字，同時被當成程式的識別碼**——改掉那句話"
        "（翻譯、或只是改個用詞）就會斷，而斷掉的樣子不是報錯。\n"
        "改成用 key（`app/i18n.py`），兩邊都從同一個 key 取。"
    )


# --- 存進檔案的值，翻譯之後還要讀得懂 ------------------------------------

def test_an_old_settings_file_still_gets_the_profile_it_chose() -> None:
    """**這一條守的是已經存在硬碟上的資料。**

    2026-08-24 之前，那四個深淺的值是中文（「省額度」「標準」「深入」），
    而那個值**同時是存進 `ai-settings.json` 的東西、也是 `profile()` 裡的
    比對字串**。

    介面翻成英文的那一刻，既有使用者的設定檔會對不上任何一個分支，
    **安靜地退回最省的那一張**——他選過「深入」、付過那個代價，然後某一次
    更新之後它變回省額度，而畫面上完全正常。

    這是【拿中文當查詢的鍵】那個病唯一一個**會影響到已經存在的資料**的入口。
    """
    from app.services.ai_settings import (
        PROFILE_DEEP,
        PROFILE_STANDARD,
        PROFILE_THRIFTY,
        AISettingsStore,
        profile_id,
    )

    # 舊檔案裡存的那幾個中文值
    assert profile_id("深入") == PROFILE_DEEP
    assert profile_id("標準") == PROFILE_STANDARD
    assert profile_id("省額度") == PROFILE_THRIFTY

    # 而且拿舊值去要設定，拿到的要是同一張
    assert (
        AISettingsStore.profile("深入").planning_rounds
        == AISettingsStore.profile(PROFILE_DEEP).planning_rounds
    ), "舊設定檔拿到的不是他選的那一張"


def test_the_profile_ids_are_not_sentences() -> None:
    """存進檔案的值不准是一句會被翻譯的話。"""
    import re

    from app.services.ai_settings import PROFILE_IDS

    cjk = re.compile(r"[\u4e00-\u9fff]")
    for value in PROFILE_IDS:
        assert not cjk.search(value), (
            f"「{value}」是一句中文，而它會被寫進 ai-settings.json。"
            "翻譯的那一刻既有使用者的設定就對不上了。"
        )
