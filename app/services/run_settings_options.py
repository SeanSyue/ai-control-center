"""這一次要怎麼跑：那 22 個控制項各自有哪些選項。

2026-08-27 交接（`交接給PM-2026-08-27-B`）寫著：

> **每一個控制項的可選項一定要一起推。** 這一頁已經為同一個形狀壞過兩次
> （座位上限、`gate_floor` 那張手抄的表）——畫面自己寫一份選項清單，
> 引擎改了之後畫面會繼續提供一個已經不存在的選擇。

所以選項在這裡宣告一次，網頁那一段照收。

## 存的是 **i18n 的鍵**，不是那幾個字

原生面板每一個 `addItem` 都走 `t()`。這裡存字面值的話，
翻譯的那天只有一邊會變——而**兩邊都還讀得通**，只是講的不是同一句。

**一張表裡不准有兩種形狀。**模型名（`Claude Sonnet 5`）今天剛好全是
ASCII、看起來不需要翻，但「今天不用翻」跟「不是介面文字」是兩件事，
而一張表裡有些存鍵、有些存字，遲早會有人挑錯一種。所以那 15 個
2026-08-27 一起補進了 `app/i18n.py`。

## 這一份跟原生面板的關係

原生面板今天仍然自己 `addItem()`。**兩邊漂開的話畫面會提供一個引擎
不認得的值**，而那個值送回來會落進 `set_run_setting` 的擋門——
他改了一個東西，然後拿到一句「畫面上那份清單跟引擎對不上了」。

守衛：`tests/test_the_web_and_the_native_panel_offer_the_same_choices.py`。
它**真的把面板開起來**，把每一個下拉的 `(itemData, itemText)` 倒出來比對。

**這一份不是「應該長怎樣」，是「原生面板現在長怎樣」的鏡子。**
面板改了、這裡沒跟上 → 紅。反過來也一樣。
"""
from __future__ import annotations

from app.i18n import t

__all__ = [
    "CHOICES",
    "SPINS",
    "TOGGLES",
    "LABELS",
    "PROFILE_KEYS",
    "SETTING_FIELD",
    "labelled",
    "label_for",
    "profiles",
]

# 下拉：`(存進 settings 的值, i18n 的鍵)`。順序就是畫面上的順序。
CHOICES: dict[str, list[tuple[object, str]]] = {
    "workflow_mode": [
        ("planning", "sequence.planning"),
        ("development", "sequence.development"),
    ],
    "claude_model": [
        ("sonnet", "ai.model.sonnet"),
        ("opus", "ai.model.opus"),
        ("haiku", "ai.model.haiku"),
        ("default", "ai.follow_cli_default"),
    ],
    "claude_effort": [
        ("auto", "ai.effort.auto"),
        ("low", "ai.effort.low"),
        ("medium", "ai.effort.medium"),
        ("high", "ai.effort.high"),
        ("xhigh", "ai.effort.xhigh"),
        ("max", "ai.effort.max"),
        ("ultracode", "ai.effort.ultracode"),
    ],
    "claude_speed": [
        ("standard", "ai.claude.speed_note"),
    ],
    "claude_permission_mode": [
        ("auto", "ai.permission.auto"),
        ("acceptEdits", "ai.permission.edits_only"),
        ("default", "ai.permission.manual"),
        ("plan", "ai.permission.read_only"),
    ],
    "claude_timeout_minutes": [
        (10, "ai.timeout.10m"),
        (30, "ai.timeout.30m"),
        (60, "ai.timeout.1h"),
        (180, "ai.timeout.3h"),
        (360, "ai.timeout.6h"),
        (0, "common.unlimited"),
    ],
    "codex_model": [
        ("gpt-5.5", "ai.codex.model.gpt55"),
        ("gpt-5.6-sol", "ai.codex.model.sol"),
        ("gpt-5.6-terra", "ai.codex.model.terra"),
        ("gpt-5.6-luna", "ai.codex.model.luna"),
        ("gpt-5.4-mini", "ai.codex.model.mini"),
    ],
    "codex_effort": [
        ("low", "ai.effort.low"),
        ("medium", "ai.effort.medium"),
        ("high", "ai.effort.high"),
        ("xhigh", "ai.effort.xhigh"),
    ],
    "codex_speed": [
        ("model_default", "ai.codex.speed_note"),
    ],
    "claude_verbosity": [
        ("concise", "verbosity.concise"),
        ("standard", "profile.standard"),
        ("detailed", "verbosity.detailed"),
    ],
    "context_profile": [
        ("minimal", "ai.context.minimal"),
        ("balanced", "ai.context.balanced"),
        ("strict", "ai.context.strict"),
    ],
}

# 數字框：`(最小, 最大, 後綴的鍵或空字串)`。
SPINS: dict[str, tuple[int, int, str]] = {
    "planning_rounds": (0, 5, "unit.rounds"),
    "max_fix_attempts": (0, 2, "unit.times"),
    "claude_max_turns": (0, 200, ""),
}

# 勾選框：那一句完整說明的鍵。**不要在畫面上縮寫它**——
# 括號裡那半句講的是代價，而代價是他決定勾不勾的依據。
TOGGLES: dict[str, str] = {
    "force_new_sessions": "ai.force_new_sessions",
    "auto_session_bootstrap": "ai.auto_bootstrap",
    "auto_memory_maintenance": "ai.auto_memory",
}

# 那三張出廠表格。**「自訂」不在這裡**——它不是一種表格，
# 是「你現在這一組跟三張都不一樣」，跟陣容頁那句「這條線還沒存成模式」
# 同一個形狀，而那五個字誤導過老闆一次。
PROFILE_KEYS: list[str] = ["profile.thrifty", "profile.standard", "profile.deep"]

# 畫面送回來的鍵 → `AIExecutionSettings` 的欄位名。
# 兩邊同名的不列（那是多數）；只列會漂的那幾個。
# 每一個控制項**自己的名字**（不是它那幾個選項的名字）。
#
# 2026-09-01 加的：畫面那一側只拿得到 `key`（`claude_model`⋯⋯），
# 而 `claude_model` 不是一句人話。頁面設計明講**不會自己抄一份標籤表**
# ——這一頁已經為手抄的表壞過兩次（座位上限、`gate_floor`）。
#
# **放在這裡而不是各畫各的**，理由跟選項同一條：兩個地方各有一份的那天，
# 它們會不一樣，而畫面上看起來完全正常。原生面板現在也讀這一份
# （`label_for()`），所以它們不可能漂開。
#
# ⛔ 搬過來的時候修掉一個真的錯：原生面板把 `ai.max_fix_attempts` 的標籤
# 加在 `(6, 0)`，**而 `ai.context_profile` 已經在那一格**——後加的蓋掉前面
# 那個，於是「上下文範圍」那個下拉旁邊寫著「修正重試次數」，而真正的
# 修正重試次數那個數字框**一個標籤都沒有**。
# 兩個都在 grid 上、兩個都畫得出來、沒有任何測試看得到。
LABELS: dict[str, str] = {
    "workflow_mode": "ai.workflow_mode",
    "planning_rounds": "ai.planning_rounds",
    "claude_model": "ai.claude.model",
    "claude_effort": "ai.claude.effort",
    "claude_speed": "ai.claude.speed",
    "claude_max_turns": "ai.claude.max_turns",
    "claude_permission_mode": "ai.claude.permission",
    "claude_timeout_minutes": "ai.timeout",
    "codex_model": "ai.codex.model",
    "codex_effort": "ai.codex.effort",
    "codex_speed": "ai.codex.speed",
    "claude_verbosity": "ai.verbosity",
    "context_profile": "ai.context_profile",
    "max_fix_attempts": "ai.max_fix_attempts",
}

SETTING_FIELD: dict[str, str] = {
    "verbosity": "claude_verbosity",
    "permission": "claude_permission_mode",
    "effort": "claude_effort",
}


def label_for(key: str) -> str:
    """這個控制項在畫面上的名字，當下語言。

    **勾選框不在 `LABELS` 裡**：它們的名字就是那一整句 `TOGGLES[key]`
    （「⋯⋯（會增加本次 token）」），而那半句括號講的是代價——
    另外給一個短標籤等於把代價搬到別的地方，然後有人會只畫短的那個。
    """
    if key in LABELS:
        return t(LABELS[key])
    if key in TOGGLES:
        return t(TOGGLES[key])
    return key


def labelled(key: str) -> list[tuple[object, str]]:
    """把 `CHOICES[key]` 的鍵換成當下語言的字。"""
    return [(value, t(label_key)) for value, label_key in CHOICES[key]]


def profiles() -> list[dict]:
    """那三張出廠表格：**代號 ＋ 當下語言的字**。

    2026-09-01 之前只回字。畫面要能按下去套用一張，而**送回來的一定是代號**
    ——`AISettingsStore.profile()` 本來就吃代號（`profile_id()` 認得），
    送中文的話英文版上線那天沒有一張表對得上，
    **而畫面看起來完全正常**。這個專案的硬規則：不准拿中文當查詢的鍵。
    """
    from app.services.ai_settings import PROFILE_CUSTOM, PROFILE_LABEL_KEYS

    # **一份來源，不是兩排對齊的清單。** 第一版是 `zip(ids, PROFILE_KEYS)`
    # 加一句「張數對不上」的斷言——而那正是「同一件事寫兩份，再寫一條
    # 檢查它們有沒有漂開」。`PROFILE_LABEL_KEYS` 本來就是代號 → 標籤鍵。
    #
    # **`custom` 不是一張表**，是「你現在這一組跟三張都不一樣」
    # （`PROFILE_KEYS` 的註解已經寫過這件事）。
    return [
        {"id": pid, "text": t(key)}
        for pid, key in PROFILE_LABEL_KEYS.items()
        if pid != PROFILE_CUSTOM
    ]
