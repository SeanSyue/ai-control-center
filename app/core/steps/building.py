"""執行：陣容裡每一格「執行」職責的位子依序動手。

## 門後藏著什麼

* **第一次只交做法，不交成品**——它自己用自己的話重講一遍要做什麼
* 每一格看得到前面每一格做出來的東西——**那就是「連線」**
* 它要找別人討論的話，走 `consulting` 那一扇門
* 它動手之後發現做不到，可以**駁回驗收標準**

## 先交做法為什麼是唯讀的一次派工

老闆 2026-08-22 的原話：**「先寫程式是大忌　一個是不夠對齊需求」**。

用的是跟擬做法那一格同一份提示詞，差別在**誰跑**：那一格是別人替它想，
這一步是它自己用自己的話重講一遍。**講不出來就代表還沒對齊**，而那件事
只有它自己講得出來——別人替它講的那一份，它可以照抄。

## 大門

    build(site, work_plan) -> Built

`Built` 刻意**不是**結果字典。它回答四件事：跑出什麼、哪幾格真的動了手、
有沒有人駁回驗收標準、要不要停下來。**怎麼把那四件事寫成一份回報，是主程式
的事。**
"""
from __future__ import annotations

from app.i18n import t

from dataclasses import dataclass, field

from app.core.lineup import DUTY_WORK
from app.core.orchestrator_parsing import parse_criteria_rejection
from app.core.steps import Site, Stop, consulting, shown_name

SEPARATOR = "\n\n---\n\n"


@dataclass
class Built:
    """執行跑完之後知道的事。

    `ran` 是**真的動了手的那幾格**，給蓋章用。寫死 author 的話，使用者把那一
    格取名「實作」之後，拍板檢查會永遠說缺一個章**而且補不出來**。
    """

    result: object | None = None
    ran: list = field(default_factory=list)
    # 有人動手之後說「這份驗收標準做不到」。
    objection: str = ""
    rejected_by: str = ""
    stop: Stop | None = None
    ending: str = ""


def build(site: Site, work_plan: str) -> Built:
    """把陣容裡每一格做事的位子依序跑過。"""
    orchestrator = site.orchestrator
    site.say(t("step.claude_working"))
    seats = [
        slot.role for slot in orchestrator.lineup if slot.duty == DUTY_WORK
    ]
    if not seats:
        # **停下來，不要挑一格代打。**挑的那一格會拿到寫入權限，而使用者從來
        # 沒有把那一格排成「執行」——他會得到一個他沒有授權的動作，而畫面上
        # 看起來一切正常。
        return Built(ending="NO_WORKING_SEAT")

    built = Built()
    carried = work_plan

    for seat in seats:
        intent, stop, failed = _what_it_plans_to_do(site, seat)
        if stop is not None:
            built.stop = stop
            return built
        if failed is not None:
            # 它連「打算怎麼做」那一次派工都沒跑起來。**那一次的結果照原樣
            # 帶回去**——主程式要把它報成一次執行失敗（CLI 出事），而不是報成
            # 「它交不出做法」。兩者在畫面上必須分得開。
            built.result = failed
            return built

        site.say(f"{shown_name(seat)}正在執行任務")
        result, stop = consulting.ask(
            site,
            seat,
            lambda talk, at=seat, plan=carried, mine=intent: (
                orchestrator.run_claude(
                    work_plan=plan, role=at, discussion=talk, own_intent=mine
                )
            ),
        )
        if stop is not None:
            built.stop = stop
            return built

        built.result = result
        built.ran.append((seat, result))
        if not result.success:
            break

        objection = parse_criteria_rejection(result.stdout)
        if objection:
            # 【每個 node 都能駁回】它是**動手之後**發現做不到的，那是量出來
            # 的證據——比擬做法那一格「還沒動手就覺得不對」重得多。所以這一種
            # 會退回去重訂，那一種是停下來問人。
            built.objection = objection
            built.rejected_by = seat
            break

        if len(seats) > 1:
            carried = _carry(carried, seat, result.stdout)

    return built


def _carry(carried: str, seat: str, output: str) -> str:
    """把這一格做出來的東西接到下一格看得到的地方。

    少了這一步，五個節點只是五次各自為政的呼叫——第三格不知道第二格做了什麼，
    於是它會把同一件事再做一次，或者做一件跟前面互相矛盾的事。
    """
    return (
        f"{carried}{SEPARATOR}## {seat} 已經做的\n\n{output.strip()}"
    ).strip()


def _what_it_plans_to_do(
    site: Site, seat: str
) -> tuple[str, Stop | None, object | None]:
    """先問它打算怎麼做。回傳 `(做法, 要停下來的理由, 跑壞了的那一次派工)`。

    三格只有一格有意義。第三格單獨列出來，是因為「那一次派工自己沒跑起來」
    跟「它交不出做法」在畫面上必須分得開——一個是 CLI 出事，一個是它沒話講。

    續跑沿用已經交過的那一份，不重問。重問會多跑一輪，而且拿到的可能是一份
    跟它已經做到一半的事對不起來的新說法。
    """
    orchestrator = site.orchestrator
    existing = orchestrator.stored_work_intent(seat)
    if existing.strip():
        return existing, None, None

    site.say(f"{shown_name(seat)}正在說它打算怎麼做")
    told, stop = consulting.ask(
        site,
        seat,
        lambda talk, at=seat: orchestrator.run_work_intent(
            role=at, discussion=talk
        ),
    )
    if stop is not None:
        return "", stop, None
    if not told.success:
        return "", None, told
    return told.stdout.strip(), None, None
