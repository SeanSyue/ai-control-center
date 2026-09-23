"""命令列工具自己講的那句話，要出現在畫面上。

老闆 2026-08-23 實測遇到的：

> 如果使用遇到 5 小時上限會暫停任務，之後不會繼續自動跑。
> 這邊衍伸出兩個狀況，一個是目前 AICC **無法在介面上處理這個狀況**⋯⋯

查證之後，成因比「沒有處理這個狀況」更簡單也更難堪：

**CLI 有講。AICC 把它換掉了。**

`claude_error` 這一欄裝著命令列工具自己的輸出（「額度用完了，幾點會回來」
那一類），而 2026-08-23 之前**畫面上沒有任何一個地方讀它**——收場表裡
`CLAUDE_ERROR` 那一行寫死一句「Claude 暫時無法完成任務」。

> 使用者看到的，跟「壞掉了」一模一樣。

## 為什麼這裡沒有一條 `if "usage limit" in ...`

因為**我沒有那個訊息的樣本**。照著猜寫一條字串比對，猜錯的時候它會**很有
自信地說錯話**——而一個很有自信的錯誤答案，比沒有答案糟。

把它自己講的話原樣端出去，不需要知道它會說什麼。等下一次真的撞到、把那段
文字留下來，再決定要不要給它一個專屬的狀態（那一半是 ❓17，待拍板）。
"""
from __future__ import annotations

from app.core import outcomes

LIMIT = "Claude usage limit reached. Your limit will reset at 3pm (Asia/Taipei)."


def test_the_screen_shows_what_the_cli_actually_said() -> None:
    """**這一條是這份檔案存在的理由。**"""
    outcome = outcomes.describe(
        {
            "workflow_status": "CLAUDE_ERROR",
            "claude_error": f"Loading...\nnode v20\n{LIMIT}\n",
        }
    )
    assert outcome.stage == LIMIT, (
        f"CLI 講的話沒有到畫面上，畫面說的是：{outcome.stage}"
    )


def test_the_preamble_does_not_push_the_reason_off_the_line() -> None:
    """狀態列只有一行，而工具把結論印在最後。

    往前多接幾行會把「Loading⋯」那種開場白一起端上去，然後結論被擠掉——
    那是用雜訊換掉答案。
    """
    outcome = outcomes.describe(
        {
            "workflow_status": "CLAUDE_ERROR",
            "claude_error": "\n".join(["前情提要 " + str(i) for i in range(50)])
            + "\n"
            + LIMIT,
        }
    )
    assert outcome.stage == LIMIT
    assert "前情提要" not in outcome.stage


def test_a_very_long_last_line_is_trimmed() -> None:
    """一行狀態列裝不下一整段堆疊。"""
    outcome = outcomes.describe(
        {"workflow_status": "CODEX_ERROR", "codex_log": "x" * 5000}
    )
    assert len(outcome.stage) <= outcomes.STAGE_MAX_CHARS


def test_saying_nothing_falls_back_to_our_own_sentence() -> None:
    """CLI 什麼都沒說的時候，畫面不准是空的。

    空白的狀態列讀起來像「還沒跑」，而這一次是真的跑了而且失敗了。
    """
    for empty in ("", "   \n  \n", None):
        outcome = outcomes.describe(
            {"workflow_status": "CLAUDE_ERROR", "claude_error": empty}
        )
        assert outcome.stage == "Claude 暫時無法完成任務"


def test_every_ending_that_can_only_be_explained_by_the_cli_reads_it() -> None:
    """CLI 沒跑起來的那幾種收場，每一種都要讀它自己那句話。

    漏掉一種的話，那一種收場的使用者會收到一句我們編的、比較沒用的話——
    而那正是這一整條路線在消滅的東西。
    """
    for status, key in (
        ("CLAUDE_ERROR", "claude_error"),
        ("CODEX_ERROR", "codex_log"),
        ("PLANNER_ERROR", "claude_error"),
        ("INTAKE_ERROR", "claude_error"),
        ("APPROVAL_ERROR", "claude_error"),
    ):
        outcome = outcomes.describe({"workflow_status": status, key: LIMIT})
        assert outcome.stage == LIMIT, f"{status} 沒有讀 {key}"


def test_nobody_invented_a_pattern_for_a_message_we_have_never_seen() -> None:
    """收場那張表裡不准出現「猜這句話長什麼樣」的字串比對。

    改壞的樣子：有人補一條 `if "usage limit" in text: return 撞到上限`。
    看起來體貼，實際上是**拿一個沒有樣本的猜測去覆蓋 CLI 真的講的話**——
    猜錯的時候它會很有自信地說錯話。

    真的要給「撞到上限」一個專屬狀態的那一天，前提是**手上有那段文字**。
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("app/core/outcomes.py").read_text(encoding="utf-8"))
    # 說明文字不算——這一份自己就在解釋為什麼不准猜，而**一條會被自己的說明
    # 打敗的檢查就是沒有牙齒的檢查**（這個專案犯過三次同樣的錯：斷言打在
    # 提到某個東西的註解上）。所以只看真的會被執行的字串。
    docstrings = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and node.body
        ):
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(
                first.value, ast.Constant
            ):
                docstrings.add(id(first.value))

    literals = [
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    assert literals, "抓不到任何字串字面值，這條會空轉"

    for guess in ("usage limit", "rate limit", "quota", "reset at"):
        offenders = [text for text in literals if guess in text]
        assert not offenders, (
            f"有人用 {guess!r} 猜 CLI 會怎麼講：{offenders}。"
            "手上沒有那段文字之前不要猜。"
        )

