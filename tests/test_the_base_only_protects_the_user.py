"""產品底座只放保護使用者的，不放限制使用者的。

定案原文（2026-08-21）：

> 【底座的判準】「**如果有任何一種使用者需要做這件事，它就不該在底座。**」
> 否決：把**本專案的紀律**寫進產品底座。
> 不變式：**底座只放保護使用者的，不放限制使用者的**；
> 「不對外發佈」屬於專案規則層。

## 為什麼這條會被違反

因為違反它**很方便**。AICC 自己就是它管理的第一個專案，而這個專案的紀律
（不對外發佈、每個改動要負向驗證、零失敗零跳過）在寫程式的人腦子裡是
「顯然對的事」。把它們寫進內建提示詞，AICC 自己這一份會立刻變好用。

代價是**別人的專案**：一個只是想剪影片的使用者，會收到一句
「每個改動要跑負向驗證」——他沒有負向驗證，而那句話會讓他的 AI 開始找一個
不存在的東西。

## 產品底座不是檔案

`.aicc/knowledge/` 底下**每一份都是專案規則層**，包括那兩份逐字送的
（`SECURITY_MODEL.md`／`OPEN_QUESTIONS.md`）——它們每個專案自己一份，
換個專案內容就不一樣。**它們特別的地方不是層級，是送法。**

（2026-08-23 的 L2 版本把它們標成底座，那是錯的。這一份是改對之後的守衛。）
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.core import rule_layers


def _shipped_text(path: Path) -> list[str]:
    """真的會被送出去的字串。註解與 docstring 不算。

    這個專案犯過三次同一種錯：斷言打在**提到**某個東西的散文上，而那段散文
    正是在解釋為什麼不該那樣做。所以只看會被執行的字面值。
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    docstrings = set()
    for node in ast.walk(tree):
        if (
            isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            )
            and node.body
        ):
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(
                first.value, ast.Constant
            ) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_base_carries_none_of_this_projects_discipline() -> None:
    """**這一條是這份檔案存在的理由。**"""
    offenders = []
    for name in rule_layers.BASE_LIVES_IN:
        for text in _shipped_text(Path(name)):
            for word in rule_layers.NOT_IN_THE_BASE:
                if word in text:
                    offenders.append(
                        f"{name}：「{word}」出現在 "
                        f"{' '.join(text.split())[:60]}"
                    )
    assert not offenders, (
        "這幾句是**這個專案自己的紀律**，跑進產品底座了——別人的專案會收到"
        "一句對他沒有意義的規則，而他的 AI 會照著去找一個不存在的東西：\n  "
        + "\n  ".join(offenders)
        + "\n底座只放保護使用者的，不放限制使用者的。"
    )


def test_the_files_it_scans_really_exist() -> None:
    """前提檢查：檔案搬走的話上面那條會變成恆真。"""
    for name in rule_layers.BASE_LIVES_IN:
        assert Path(name).is_file(), f"{name} 不見了，上面那條檢查空轉"
    assert rule_layers.NOT_IN_THE_BASE


def test_the_scan_can_actually_see_a_violation() -> None:
    """而且那個掃法真的看得到違規。

    掃法看不到的話，上面那條會永遠綠——而綠燈在被弄紅過之前不算證據。
    """
    fake = "\n".join(
        [
            "def shipped():",
            '    return "每個改動要跑負向驗證。"',
        ]
    )
    tree = ast.parse(fake)
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert any("負向驗證" in text for text in literals)


def test_every_knowledge_file_is_the_project_layer() -> None:
    """`.aicc/knowledge/` 底下沒有一份是產品底座。

    改壞的樣子就是它 2026-08-23 早上的樣子：把那兩份逐字送的標成底座，
    於是「層級」跟「送法」被混成同一件事，而下一個人會以為那兩份是產品內建的。
    """
    from app.services.project_knowledge import MEMORY_FILES, PROJECT_RULES_FILE

    for name in (*MEMORY_FILES, PROJECT_RULES_FILE, *rule_layers.PUSHED_VERBATIM):
        assert rule_layers.layer_of(name) == rule_layers.LAYER_PROJECT, (
            f"{name} 被標成產品底座了——它是每個專案自己一份的檔案。"
        )


def test_how_it_is_sent_is_not_which_layer_it_is_in() -> None:
    """送法跟層級是兩件事，不准再混在一起。"""
    pushed = set(rule_layers.PUSHED_VERBATIM)
    assert pushed, "逐字送的清單是空的"
    for name in pushed:
        assert rule_layers.layer_of(name) == rule_layers.LAYER_PROJECT
