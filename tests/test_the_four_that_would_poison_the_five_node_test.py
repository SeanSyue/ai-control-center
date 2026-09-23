"""五節點測試前修掉的四件：**它們都會讓他讀到一個假的成功**。

2026-08-24 掃描出 30 條假綠燈，他拍板測試前先修這四件。挑這四件的判準是
「他跑那五個節點的時候會不會讀到假結果」，而這四件裡有三件不只是誤導
——**它們會動到他的東西**（八份共同記憶）。

| # | 沒修的時候 |
|---|---|
| ① | 讀不懂複查回覆 → 當成通過 → 未經審查的草稿整檔覆寫八份記憶 |
| ② | 維護記憶那次呼叫失敗 → 收場照樣薄荷色「工作流程已完成」 |
| ③ | 機密檢查壞掉 → 回空字串 → **自動放行** |
| ④ | `inspect_run.py` 把「被駁到修正上限」印成「全部成功」 |

④ 不在產品裡，但它是他讀報告的那支工具——**印錯的報告跟壞掉的產品，
對做決定的人是同一件事**。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.core import orchestrator_parsing as parsing


# ---- ① 讀不懂複查回覆，不准當成通過 -------------------------------------


@pytest.mark.parametrize(
    "output",
    [
        "這不是 JSON",
        "",
        "[1, 2, 3]",
        '{"status": "PASS"',
    ],
)
def test_an_unreadable_memory_review_is_marked_unparsed(output: str) -> None:
    """**這一條是這份檔案存在的理由。**

    `status` 照舊回 `PASS`（那是既有契約，動它會牽到別處），但多一格
    `parsed=False` 讓呼叫端分得出「AI 說可以」跟「我讀不懂 AI 說什麼」。
    """
    review = parsing.parse_memory_review(output)
    assert review["parsed"] is False, (
        f"{output!r} 讀不懂，卻回了一個跟真的通過審查一模一樣的形狀"
    )


def test_a_readable_review_is_marked_parsed() -> None:
    assert parsing.parse_memory_review('{"status": "PASS"}')["parsed"] is True
    assert parsing.parse_memory_review('{"status": "ASK_USER"}')["parsed"] is True


@pytest.mark.parametrize("fence", ["```json", "```"])
def test_a_fenced_json_reply_is_still_readable(fence: str) -> None:
    """**圍籬是最常見的輸出形狀。**

    不剝的話，一個內容完全正確的回覆會走進「讀不懂」那一支——
    而在 2026-08-24 之前，那一支的意思是「當成通過」。
    """
    wrapped = f'{fence}\n{{"status": "ASK_USER", "question": "要不要改"}}\n```'
    review = parsing.parse_memory_review(wrapped)
    assert review["parsed"] is True, "被圍籬包起來的正確 JSON 被判成讀不懂"
    assert review["status"] == "ASK_USER"


def test_the_two_parsers_agree_on_what_unreadable_means() -> None:
    """決策關卡與記憶審查**對同一種輸入要給同一種待遇**。

    2026-08-24 之前它們並排寫著，一個 fail-closed（`ASK_USER`）
    一個 fail-open（`PASS`）——而 fail-open 的那一個會覆寫檔案。
    """
    bad = "這不是 JSON"
    assert parsing.parse_decision_gate(bad)["parsed"] is False
    assert parsing.parse_memory_review(bad)["parsed"] is False


# ---- ③ 機密檢查壞掉 → 失敗關閉 -------------------------------------------


def test_a_broken_secret_check_does_not_wave_it_through() -> None:
    """掃描壞掉的意思是**這一輪沒有人在看機密**，不是「沒問題」。"""
    with patch(
        "app.services.security_scan.SecurityScanner.scan_text",
        side_effect=RuntimeError("掃描壞了"),
    ):
        veto = parsing.gate_veto("把設定檔貼上來看看")
    assert veto, "機密檢查壞掉卻放行了"
    assert veto == parsing.SECRET_CHECK_BROKE


def test_the_broken_message_does_not_go_through_translation() -> None:
    """那句話**寫死**——壞掉的有可能正好是 i18n 本身。

    走 `t()` 的話，會在報告故障的路上再掉進同一個洞，等於什麼都沒報。
    """
    assert isinstance(parsing.SECRET_CHECK_BROKE, str)
    assert parsing.SECRET_CHECK_BROKE.strip()
    from app.i18n import STRINGS

    assert parsing.SECRET_CHECK_BROKE not in STRINGS.values(), (
        "那句話進了 i18n——它就會跟著翻譯走，而它要擋的正是「翻譯也壞了」"
    )


def test_a_secret_that_was_actually_found_is_never_swallowed() -> None:
    """**掃到了、只是標籤取不到**，不准被當成掃描壞掉。

    2026-08-24 之前 `t(...)` 在 try 裡面，於是一個**已經驗出機密**的結果
    會被 except 吃成放行。
    """
    class _Found:
        has_blocking_findings = True

    with patch(
        "app.services.security_scan.SecurityScanner.scan_text",
        return_value=_Found(),
    ), patch("app.core.orchestrator_parsing.t", side_effect=RuntimeError("翻譯壞了")):
        with pytest.raises(RuntimeError):
            parsing.gate_veto("裡面有 key")


def test_a_clean_question_still_goes_through() -> None:
    """**不准擋過頭**：正常的問題照樣過。"""
    assert parsing.gate_veto("把 README 的錯字改一下") == ""


# ---- ② 維護記憶失敗要有自己的收場 ---------------------------------------


def test_a_failed_memory_maintenance_is_not_a_completed_run() -> None:
    """CLI 掛掉 → 那一輪沒有人維護記憶 → **不准畫成薄荷色的完成**。"""
    from app.core import outcomes

    assert "MEMORY_ERROR" in outcomes.known_statuses(), (
        "維護記憶失敗這一種收場沒有家——沒有家的收場會掉進 else 變成 COMPLETED"
    )
    described = outcomes.describe(
        {"workflow_status": "MEMORY_ERROR", "memory_problem": "codex 沒有啟動"}
    )
    assert described.style != "done", "畫成了「做完了」"
    assert "codex 沒有啟動" in described.stage, (
        "收場那一列要講得出**哪一次呼叫沒成**，不是只講「記憶沒維護到」"
        f"——他修不了後者：{described.stage}"
    )
