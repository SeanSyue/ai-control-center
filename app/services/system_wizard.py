"""系統精靈：這台機器上的三件事，問一次就好。

## 兩個精靈，各管各的層級（`DECISIONS` 172）

| | 管什麼 | 什麼時候用 |
|---|---|---|
| **系統精靈**（這一份）| 這台機器（跑得動嗎、跑多深、看起來如何）| 第一次打開 AICC |
| **專案精靈** | 單一專案（敏感度、幾個 NODE、規矩）| 每次開新專案 |

合成一個是被否決過的：**一個管全域、一個管單案，混在一起的時候使用者
分不出「我現在改的是這個案子，還是每個案子」**——而那一題答錯，
下一個專案會安靜地繼承他以為只設給這一個的東西。

## 三題，而且第①題不是問句

第①題是**檢查**：兩支 CLI 裝了沒、登入了沒。他答不了這一題，機器答得了。
精靈要做的是把答案端出來，不能就給一顆按鈕（`credential_entry`）。

`門檻二-別人打得開.md` 那句：**他沒登入的話，精靈問完照樣跑不動，
而失敗訊息跟精靈無關**——他會以為是精靈壞了。

## 第②題就是「一題決定一整組」

`AIExecutionSettings` 有二十幾個欄位。**精靈一個都不問**，它問的是
「要省還是要深」，然後套 `AISettingsStore.profile()` 那三張出廠設定。

跟專案精靈的四模式是同一個形狀，而那是刻意的：使用者學一次就好。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services import ui_preferences
from app.services import ai_settings
from app.services.ai_settings import AIExecutionSettings, AISettingsStore
from app.i18n import t

# 第②題的四個答案。前三個是 `AISettingsStore.profile()` 認得的名字——
# **字串要跟那邊一模一樣**，不然套下去會靜靜落到最省的那一張。
# 那四個深淺**用代號，不用中文**：這個值會存進 `ai-settings.json`，
# 而拿一句給人看的話當存進檔案的值，等於介面翻譯的那天所有既有設定都失效。
#（`ai_settings` 那邊留著舊中文值的對照，所以既有檔案還讀得懂。）
DEPTH_THRIFTY = ai_settings.PROFILE_THRIFTY
DEPTH_STANDARD = ai_settings.PROFILE_STANDARD
DEPTH_DEEP = ai_settings.PROFILE_DEEP
DEPTH_CUSTOM = ai_settings.PROFILE_CUSTOM

DEPTHS = (DEPTH_THRIFTY, DEPTH_STANDARD, DEPTH_DEEP, DEPTH_CUSTOM)

# 「自訂」從「標準」起跳，理由跟專案精靈的「自訂」從兩人起跳一樣：
# 從空白開始的自訂，第一格就會卡住。
_CUSTOM_STARTS_FROM = DEPTH_STANDARD

# 第③題。值要跟 `web_panel._ThemeBus` 認得的一樣。
LOOK_NIGHT = ui_preferences.MODE_NIGHT
LOOK_DAY = ui_preferences.MODE_DAY
LOOKS = ui_preferences.MODES

_LOOK_LABEL_KEYS = {LOOK_NIGHT: "wizard.look.night", LOOK_DAY: "wizard.look.day"}


def look_label(look: str) -> str:
    """畫面上那個字。**在函式裡叫 `t()`**——模組層叫會把字凍在 import 那一刻。"""
    return t(_LOOK_LABEL_KEYS[look])


class SystemWizardError(ValueError):
    """答案本身有問題，而且要講給使用者聽。"""


@dataclass(frozen=True)
class Answers:
    """三題的答案。第①題沒有欄位——**它是檢查，不是問句**。"""

    depth: str = DEPTH_THRIFTY
    look: str = LOOK_NIGHT


WRITES_TO = {
    "depth": "home.machine.ai_settings",
    "look": "home.machine.ui",
}


@dataclass
class Applied:
    written: list[str] = field(default_factory=list)
    # 「自訂」要把他帶去 AI 設定頁自己調。
    goes_to_settings_page: bool = False
    problems: list[str] = field(default_factory=list)


def settings_for(depth: str) -> AIExecutionSettings:
    """這個深淺對應的整組設定。

    不認得的**丟例外，不回一個預設值**——回預設值的話，他選了「深入」
    卻拿到「省額度」，而畫面上兩者長得一模一樣。
    """
    if depth not in DEPTHS:
        raise SystemWizardError(f"不認得的深淺：{depth!r}")
    name = _CUSTOM_STARTS_FROM if depth == DEPTH_CUSTOM else depth
    return AISettingsStore().profile(name)


def apply(answers: Answers, store: AISettingsStore | None = None) -> Applied:
    """把答案寫進機器層那兩個家。

    跟專案精靈同一條規矩：**一題失敗不影響另一題**。
    """
    if answers.depth not in DEPTHS:
        raise SystemWizardError(f"不認得的深淺：{answers.depth!r}")
    if answers.look not in LOOKS:
        raise SystemWizardError(f"不認得的畫面：{answers.look!r}")

    result = Applied()

    # ② AI 怎麼跑 → ai-settings.json
    try:
        (store or AISettingsStore()).save_global(settings_for(answers.depth))
        result.written.append(WRITES_TO["depth"])
        result.goes_to_settings_page = answers.depth == DEPTH_CUSTOM
    except (OSError, ValueError) as exc:
        result.problems.append(f"AI 設定沒存成：{exc}")

    # ③ 畫面偏好 → 登錄檔
    try:
        save_look(answers.look)
        result.written.append(WRITES_TO["look"])
    except Exception as exc:  # noqa: BLE001 - QSettings 的失敗形狀不固定
        result.problems.append(f"畫面偏好沒存成：{exc}")

    return result


def save_look(look: str) -> None:
    """寫進登錄檔——**經由那個唯一的家**。

    不自己開 `QSettings`，也不 import `app.ui`：2026-08-24 量到 `ai_settings_panel.py` 用的是
    `QSettings("Sean", "AIControlCenter")`（少一個空格），在 Windows 登錄檔裡
    那是**另一個位置**，而畫面上完全看不出差別。多一個自己開 QSettings 的
    地方，就是多一次打錯字的機會。
    """
    if look not in LOOKS:
        raise SystemWizardError(f"不認得的畫面：{look!r}")
    ui_preferences.set_mode(look)
