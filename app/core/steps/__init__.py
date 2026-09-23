"""工作流程的每一扇門。

老闆 2026-08-23 的話：

> 我們需要依照功能去劃分模組，未來有任何需求只需主程式對應單一大門，
> 對應功能需求的時候只要去找這個對應模組就好，**所有邏輯藏在門後**。

## 每一扇門長一樣

    <動詞>(site, ...) -> (產出, Stop | None)

**大門不叫 `run`**：那是安全檢查認得的啟動器名字（掃 `.run(` 有沒有拿變數
當指令），叫 `run` 會被它讀成「這個檔案開始動態組指令了」。**那條檢查沒有
錯，是命名踩到它**——而為了命名去放寬一條安全檢查是最糟的交換。所以每一扇
門用講得出自己在做什麼的動詞：`intake.confirm(site)`。

`Stop` 不是結果字典，是**一句「為什麼停下來」**。門後的模組不知道結果字典
長什麼樣——那是主程式的事。這條界線是刻意的：

* 門不用認識十九個欄位，加欄位的時候不用一扇一扇改。
* 主程式不用認識門後的細節，它只會按順序敲門、把停下來的理由翻成回報。

## `Site` 是「現場」

一次執行手上的東西：派工的人、蓋章的人、怎麼跟畫面說話、使用者剛剛回了什麼。
傳一個現場而不是傳七個參數，是因為**加一樣東西的時候不用改每一扇門的簽章**
——而簽章一改，每一扇門都要跟著改一次，那正是這次重構要消滅的東西。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.core.orchestrator import Orchestrator
from app.core.run_stamps import RunStamper


# 角色的中文名住在 `lineup.py`（角色代號在那裡）。這裡只是轉出去，
# 讓各扇門照舊 `from app.core.steps import shown_name`。
from app.core.lineup import shown_name as shown_name


@dataclass
class Answer:
    """使用者剛剛回的那一句，以及它回的是誰問的。

    **可變的，而且只准被吃掉一次。**兩條路都會讀它（需求確認、開發工程師），
    吃掉之後沒清空的話，第二條路會拿一句回答別人問題的話當成給自己的答案，
    然後照著它動手。

    包成一個物件而不是兩個字串，就是為了讓「吃掉」是一個動作
    （`take()`），而不是兩行各自要記得的賦值。
    """

    question: str = ""
    text: str = ""

    def __bool__(self) -> bool:
        return bool(self.text)

    def take(self) -> tuple[str, str]:
        """把它拿走。拿走之後這個位子是空的。"""
        question, text = self.question, self.text
        self.question, self.text = "", ""
        return question, text


@dataclass
class Stop:
    """停下來，以及為什麼。

    刻意**不是**結果字典：門後的模組不該知道回報長什麼樣。它只說三件事——
    停在哪一種狀況、要不要等使用者回答、以及一句人看得懂的理由。
    """

    status: str
    note: str = ""
    question: str = ""
    # 有些停下來要沿用某一次派工的結果（例如 CLI 自己失敗了），這裡帶回去。
    cli_result: object | None = None
    review_status: str = "NOT_RUN"


@dataclass
class Site:
    """一次執行的現場。"""

    orchestrator: Orchestrator
    stamper: RunStamper
    on_stage: Callable[[str], None]
    answer: Answer = field(default_factory=Answer)

    def say(self, message: str) -> None:
        self.on_stage(message)
