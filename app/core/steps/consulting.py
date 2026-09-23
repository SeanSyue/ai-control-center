"""討論：一格派工的時候說「我要問某某」，替它問，帶著答案再派一次。

老闆的五節點流程裡有三處寫著「有問題向 NODE2 討論」「與 NODE3 討論」
「丟回 NODE1 確認可行性」。**少了這條路，那三句話在引擎裡不存在**——每一格
只能對著它拿到的文字自己猜，而猜錯要等到最後複查才會被發現，那時候要重做的
不只是它那一格。

## 門後藏著什麼

* 指名（不准問「上一關」，要指名一格）
* 名冊（指到不存在的那一格就停下來，**不猜**）
* 不准問自己
* **沒有輪數上限**；收斂的判準是「同一題問第二次還是同一題」
* 不討論的時候**一次呼叫都不多**

## 大門

    ask(site, role, send) -> (結果, Stop | None)

`send(discussion) -> CliResult` 是「怎麼派這一格」，由呼叫的人給——這扇門
不知道被派的是擬做法、執行還是別的。**它只管討論這件事。**
"""
from __future__ import annotations

from app.i18n import t

from typing import Callable

from app.core.orchestrator_parsing import parse_consult_request
from app.core.steps import Site, Stop, shown_name

# 同一題問到第幾次算卡住。**不是輪數上限**——問不同的題目可以一直問下去。
STUCK_AFTER_REPEATS = 2

# 比對「是不是同一題」的時候，取前面這麼多字。整題比的話，模型改一個標點
# 就會被當成新問題，而它其實在原地打轉。
QUESTION_KEY_CHARS = 400


def ask(
    site: Site,
    role: str,
    send: Callable[[tuple], object],
) -> tuple[object, Stop | None]:
    """派一格，需要的話替它去問別人。

    **沒有輪數上限。**設了上限，到最後一輪它會為了結束而假裝問清楚了——
    然後帶著一個它其實沒搞懂的前提往下做。收斂的判準是定案那一條：
    **同一個問題問第二次還是同一個問題，就是卡住。**
    """
    orchestrator = site.orchestrator
    talk: list[tuple[str, str, str]] = []
    asked: list[str] = []
    seats = orchestrator.lineup.roles

    while True:
        result = send(tuple(talk))
        if not result.success:
            return result, None

        request = parse_consult_request(result.stdout, seats=seats)
        if not request["asking"]:
            return result, None

        stop = _why_it_cannot_be_asked(role, request, asked)
        if stop is not None:
            return result, stop

        asked.append(_key(request["question"]))
        site.say(
            f"{shown_name(role)}正在跟{shown_name(request['who'])}討論"
        )
        answer = orchestrator.run_consult(
            role=request["who"],
            asked_by=role,
            question=request["question"],
            context=result.stdout,
        )
        if not answer.success:
            return answer, None
        talk.append(
            (request["who"], request["question"], answer.stdout.strip())
        )


def _key(question: str) -> str:
    return " ".join(question.split())[:QUESTION_KEY_CHARS]


def _why_it_cannot_be_asked(
    role: str, request: dict, asked: list[str]
) -> Stop | None:
    """這一題問不下去的三種理由。回 `None` 代表可以問。

    寫成一個函式而不是三段 `if`，是因為三種都會回同一種停下來，而分開寫的
    那一天有人只補了其中一段。
    """
    if request["problem"]:
        # **不猜。**替它挑一個對象就變回「問上一關」，而那正是
        # 【駁回直接送給該負責的人】否決的形狀。
        return _invalid(role, request["problem"])

    if request["who"] == role:
        return _invalid(
            role,
            t("consult.self_target"),
        )

    key = _key(request["question"])
    if len(asked) >= STUCK_AFTER_REPEATS - 1 and asked[-1] == key:
        return _stuck(role, request["who"], request["question"])

    return None


def _invalid(role: str, problem: str) -> Stop:
    """它說要討論，但引擎照著做不到——停下來說清楚。"""
    return Stop(
        status="CONSULT_INVALID",
        review_status="NOT_RUN",
        note=f"【{shown_name(role)}】{problem}",
    )


def _stuck(role: str, other: str, question: str) -> Stop:
    """同一題問第二次還是同一題——這一題沒有進展，交給使用者。

    【談不攏交給人】：不自動放行，也不自動終止。自動放行的話，它會帶著一個
    沒有答案的前提往下做；自動終止的話，使用者只會看到「失敗」，而真正發生
    的是兩格對同一件事有不同的理解。
    """
    return Stop(
        status="CONSULT_STUCK",
        review_status="NOT_RUN",
        note=(
            f"【{shown_name(role)}】跟【{shown_name(other)}】討論之後，"
            f"又問了同一個問題——這一題沒有進展，停在這裡等你決定。"
            f"\n\n問的是：{question}"
        ),
    )
