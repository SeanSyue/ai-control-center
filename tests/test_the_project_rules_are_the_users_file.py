"""「這個專案的規矩」是使用者的檔案，AICC 只讀不寫。

【規則分三層】的中間那一層——**專案規則（使用者自己訂）**。
2026-08-23 之前這一層在程式裡完全不存在：他**沒有任何地方**可以寫
「這個專案不准碰 `vendor/`」「這個專案的測試要跑兩次」這種話。

## 為什麼它不在 `MEMORY_FILES` 裡

那八份是 **AICC 維護的**：跑完之後 Codex 起草、Claude 複審、才寫回去。
這一份是**他寫的**。

2026-08-23 真的把它放進那張表試過一次，`test_the_memory_prompt_asks_for_
exactly_the_files_the_writer_accepts` 立刻變紅——因為記憶維護那一步會把它
當成自己的東西改掉。**那條紅燈是對的，而它抓到的是一條所有權界線。**

> 一份被 AI 改掉的「使用者規矩」，讀起來跟他自己寫的一模一樣。
"""
from __future__ import annotations

from pathlib import Path

from app.core import rule_layers
from app.services.project_knowledge import MEMORY_FILES, PROJECT_RULES_FILE


def test_it_is_not_one_of_the_files_aicc_maintains() -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：有人「順手」把它加進 `MEMORY_FILES`，因為那樣共享記憶那一頁
    的下拉就會自動列出它——省事，代價是 AI 從此可以改寫使用者訂的規矩。
    """
    assert PROJECT_RULES_FILE not in MEMORY_FILES, (
        f"{PROJECT_RULES_FILE} 跑進 AICC 自己維護的那張表了——"
        "記憶維護那一步會把使用者訂的規矩當成自己的東西改掉。"
    )


def test_the_two_modules_mean_the_same_file() -> None:
    """檔名不准各寫一份。

    各寫一份的那一天，一邊讀 `PROJECT_RULES.md`、一邊寫 `project-rules.md`
    ——他打的字進了一個沒有人讀的檔案，而畫面上看起來存好了。
    """
    assert rule_layers.PROJECT_RULES_FILE == PROJECT_RULES_FILE


def test_it_is_pushed_word_for_word_when_it_exists() -> None:
    """它跟底座同一種東西：**不准做的事**，所以逐字送，不是點名。

    AI 不會主動去讀一份限制它的檔案。
    """
    assert PROJECT_RULES_FILE in rule_layers.pushed_files(has_project_rules=True)
    assert PROJECT_RULES_FILE not in rule_layers.pushed_files()


def test_it_belongs_to_the_project_layer() -> None:
    """`.aicc/knowledge/` 底下每一份都是專案規則層。

    2026-08-23 早上這條斷言寫的是「那兩份逐字送的＝產品底座」，而**那是錯的**：
    它們每個專案自己一份，換個專案內容就不一樣。**送法跟層級是兩件事**，
    改對了之後這條跟著改。細節在
    `tests/test_the_base_only_protects_the_user.py`。
    """
    assert rule_layers.layer_of(PROJECT_RULES_FILE) == rule_layers.LAYER_PROJECT
    for name in rule_layers.PUSHED_VERBATIM:
        assert rule_layers.layer_of(name) == rule_layers.LAYER_PROJECT
    assert rule_layers.layer_of("DECISIONS.md") == rule_layers.LAYER_PROJECT


def test_the_two_pushed_verbatim_are_the_two_that_cannot_be_cut() -> None:
    """逐字送的就是那兩份「少一句就等於失效」的。

    它們早就在「不准截斷」名單上，而那條名單的理由跟這裡是同一個。
    改壞的樣子：有人把 `DECISIONS.md`（15,667 字）加進逐字送的清單——那等於把
    「不准全量下發」那條定案退回原點，而畫面上看不出差別。
    """
    from app.services.project_knowledge import MEMORY_MUST_ARRIVE_WHOLE

    assert set(rule_layers.PUSHED_VERBATIM) == set(MEMORY_MUST_ARRIVE_WHOLE), (
        "逐字送的那幾份跟「不准截斷」名單不一樣了。兩者的理由是同一個"
        "（被截斷的安全規則讀起來跟完整的一模一樣），所以它們要一起改。"
    )


def test_a_seat_whose_duty_is_unknown_still_gets_told_to_read_something() -> None:
    """認不出職責的時候**不准給空的**。

    「不知道這一格在做什麼」跟「這一格不需要背景」是兩件完全不同的事，
    而空的那個回傳會讓它們長得一樣。
    """
    assert rule_layers.read_before_starting("這是誰")
    assert rule_layers.read_before_starting("")


def test_every_duty_the_lineup_can_hold_is_covered() -> None:
    """陣容排得出來的每一種職責，都要有人替它想過該讀什麼。

    漏掉一種就掉到 fallback，而 fallback 是「最保守的那一份」——那是安全網，
    不是答案。
    """
    from app.core.lineup import VALID_DUTIES

    for duty in VALID_DUTIES:
        assert duty in rule_layers._READ_BEFORE_STARTING, (
            f"職責「{duty}」沒有人替它想過開工前要讀什麼"
        )


def test_the_home_is_beside_the_other_memory_files() -> None:
    """放在 `.aicc/knowledge/` 底下，不是另外開一個地方。

    多開一個地方等於多一個他要記得去看的地方，而他不會記得。
    """
    source = Path("app/core/rule_layers.py").read_text(encoding="utf-8")
    assert "knowledge" in source
