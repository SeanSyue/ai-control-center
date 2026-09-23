"""專案精靈：開新專案的時候問三題，然後**寫進本來就有的那幾個家**。

## 老闆給的使用者情境（2026-08-24，不是我推的）

> 新使用者在開新專案的時候使用專案精靈
> 選了三人後**導入預設設定**
> 那這個設定裡面要有一個**預設的角色設定**
> 然後使用者可以更改
> **一更改就變成自訂模式**
>
> 專案精靈在**新專案開設時**使用，這幾個問題都要問
> 等於是問了之後可以讓他選幾個 NODE
> 讓他選幾個 NODE 那邊也要給個**自訂**這個選項

所以第②題不是「要有幾個人看」——那是我問錯的版本，他當場說
「我看不懂你的問題」。**它問的是「這個專案要排幾個 NODE」**，
而四個答案是【一人／兩人／三人／自訂】。

## 三題，不多不少

| | 問什麼 | 寫到哪 |
|---|---|---|
| ① | 這個專案碰得到多敏感的東西 | `.aicc/project.json` |
| ② | 要排幾個 NODE（四選一）| `.aicc/sessions.json` |
| ③ | 有沒有特別規矩（可跳過）| `.aicc/knowledge/PROJECT_RULES.md` |

**第七題、第八題會一題一題加回來**，除非有東西擋著。擋著的是
`tests/test_the_wizard_asks_three_questions.py`：多一題就變紅，
而變紅的時候要有人回答「為什麼這一題非問不可」。

## 精靈不准建新檔（`DECISIONS` 170）

它問的每一樣東西**都已經有主人了**。所以精靈只是設定頁的引導模式，
寫的是設定頁在寫的那幾個檔——位置全部來自 `settings_homes.HOMES`，
而 `WRITES_TO` 底下那支檢查會確認每一個都在清單上。
"""
from __future__ import annotations

from app.i18n import t

from dataclasses import dataclass, field
from pathlib import Path

from app.core.lineup import Lineup
from app.services.lineup_presets import (
    LineupPreset,
    PresetError,
    apply_preset,
    built_in_presets,
)
from app.services.project_registry import (
    DEFAULT_SENSITIVITY,
    SENSITIVITY_LEVELS,
    ProjectRegistry,
)
from app.services.session_registry import SessionRegistry

# 第②題的四個答案。**「自訂」是第四個模式，不是「沒有選」。**
#
# 2026-08-24 之前引擎對「自訂」回的是空字串，於是畫面把它讀成「沒有選中」，
# 而老闆的原話是：「之前講的一個 NODE 兩個 NODE 三個 NODE 自訂**這四個模式**
# 你沒放上去，那這樣沒辦法自訂的話我比較難進行後面比較難的測試」。
CHOICE_ONE = "one"
CHOICE_TWO = "two"
CHOICE_THREE = "three"
CHOICE_CUSTOM = "custom"

CHOICES = (CHOICE_ONE, CHOICE_TWO, CHOICE_THREE, CHOICE_CUSTOM)

# 每一個選項對到哪一張出廠表格。**「自訂」對到兩人**，理由不是它像兩人，
# 是「從空白開始」對一個新使用者沒有意義——他會看到一張空表格，然後不知道
# 第一格該填什麼。從兩人起跳，他改的是**已經跑得動的東西**。
_PRESET_INDEX = {
    CHOICE_ONE: 0,
    CHOICE_TWO: 1,
    CHOICE_THREE: 2,
    CHOICE_CUSTOM: 1,
}


@dataclass(frozen=True)
class Answers:
    """三題的答案。**每一個欄位都要在 `WRITES_TO` 裡有一個家。**"""

    sensitivity: str = DEFAULT_SENSITIVITY
    lineup: str = CHOICE_TWO
    # 可跳過。空的就是不寫那個檔——**不寫一個空檔案**：空的
    # `PROJECT_RULES.md` 會被派工當成「這個專案有規矩」推給每一個 NODE。
    rules: str = ""


# 每一題寫到哪一個「家」。字串對到 `settings_homes.HOMES` 裡的 `what`。
#
# 用功能名而不是檔案路徑，是因為【依功能分類】那條：**先回答「這是什麼」
# 才找得到位置**。路徑改了這裡不用動，改的是清單。
WRITES_TO = {
    "sensitivity": "home.project.what_and_how",
    "lineup": "home.project.people",
    "rules": "home.project.rules",
}


@dataclass
class Applied:
    """精靈按下完成之後，實際發生了什麼。"""

    written: list[str] = field(default_factory=list)
    # 「自訂」要把他帶去陣容頁自己排。其他三個直接可以開始。
    goes_to_lineup_page: bool = False
    # 沒寫成的東西，以及原因。**不吞掉**——吞掉的樣子是他以為設好了。
    problems: list[str] = field(default_factory=list)


class WizardError(ValueError):
    """答案本身有問題，而且要講給使用者聽。"""


def preset_for(choice: str) -> LineupPreset:
    """這個選項對應的出廠表格。

    不認得的選項**丟例外，不回一個預設值**——回預設值的話，畫面上多一個
    打錯字的選項，使用者會拿到一張他沒有選的表格，而完全看不出來。
    """
    if choice not in CHOICES:
        raise WizardError(f"不認得的選項：{choice!r}")
    presets = built_in_presets()
    index = _PRESET_INDEX[choice]
    if index >= len(presets):
        raise WizardError(t("wizard.error.presets_missing"))
    return presets[index]


def validate(answers: Answers) -> list[str]:
    """答案有沒有問題。**回清單不是回布林**——他要知道是哪一題。"""
    problems: list[str] = []
    if answers.sensitivity not in SENSITIVITY_LEVELS:
        problems.append(f"不認得的敏感度：{answers.sensitivity!r}")
    if answers.lineup not in CHOICES:
        problems.append(f"不認得的 NODE 數：{answers.lineup!r}")
    return problems


def apply(answers: Answers, project_path: Path) -> Applied:
    """把三題的答案寫進它們各自的家。

    **一題失敗不影響另外兩題。**三個家是三個獨立的檔，而把它們綁成
    「全部成功或全部不做」會讓一個寫不進去的規矩檔擋掉整個專案設定
    ——那時候他只知道「精靈失敗了」，不知道是哪一題。
    """
    problems = validate(answers)
    if problems:
        raise WizardError("；".join(problems))

    result = Applied()
    project_path = Path(project_path)

    # ① 這個專案是什麼、怎麼驗
    try:
        ProjectRegistry.set_sensitivity(project_path, answers.sensitivity)
        result.written.append(WRITES_TO["sensitivity"])
    except (OSError, ValueError) as exc:
        result.problems.append(f"敏感度沒存成：{exc}")

    # ② 這個專案的人
    try:
        registry = SessionRegistry(project_path)
        seeded = apply_preset(preset_for(answers.lineup), registry.load_lineup())
        registry.save_lineup(seeded)
        result.written.append(WRITES_TO["lineup"])
        result.goes_to_lineup_page = answers.lineup == CHOICE_CUSTOM
    except (OSError, ValueError, PresetError) as exc:
        result.problems.append(f"陣容沒存成：{exc}")

    # ③ 這個專案的規矩（可跳過）
    if answers.rules.strip():
        try:
            write_project_rules(project_path, answers.rules)
            result.written.append(WRITES_TO["rules"])
        except OSError as exc:
            result.problems.append(f"專案規矩沒存成：{exc}")

    return result


def write_project_rules(project_path: Path, text: str) -> Path:
    """`.aicc/knowledge/PROJECT_RULES.md`——**在這之前沒有任何地方寫得進去**。

    派工那一邊早就會讀它（`project_knowledge._memory_context` 把它跟那八份
    一起推給每一個 NODE），但寫入端一直是空的：`write_memory_file()` 只收
    `MEMORY_FILES` 那八份，而 `PROJECT_RULES.md` 是**刻意排除在外**的
    ——它是使用者訂的，不是 AICC 維護的記憶。

    所以在 2026-08-24 之前，那個檔只能靠使用者自己用記事本開出來。
    """
    from app.services.project_knowledge import PROJECT_RULES_FILE

    path = Path(project_path) / ".aicc" / "knowledge" / PROJECT_RULES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def lineup_for(choice: str) -> Lineup:
    """這個選項排出來的陣容長什麼樣——**不寫任何檔**。

    精靈畫面上要先讓他看到「選這個會得到什麼」，而預覽不該有副作用。
    """
    return preset_for(choice).lineup
