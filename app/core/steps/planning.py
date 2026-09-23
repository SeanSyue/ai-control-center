"""擬做法：陣容裡每一個「擬做法」的節點依序跑過，各交一份，串成一份。

老闆 2026-08-22 的原話：**「先寫程式是大忌　一個是不夠對齊需求」**。

## 門後藏著什麼

* 每一格看得到前面所有格的產出——**這就是「連線」**。少了它，五個節點只是
  五次各自為政的呼叫
* 訂標準的那一格要不要再擬一次（後面還有別人在擬就跳過，它是唯一一格不能省的）
* 續跑不重擬（重擬會拿到一份不一樣的做法，後面蓋好的章全部失效）
* 每一格都提得出「這份驗收標準有問題」——**而且是停下來交給人，不是自動改**
* 它要找別人討論的話，走 `consulting` 那一扇門

## 大門

    draft(site, criteria) -> (做法, Stop | None)

只有一個有意義。停下來刻意**不是**「沒有做法就繼續」——那會讓使用者排的是
五格、實際跑的是三格，而執行紀錄回報一切正常。他會拿到一個跟他編的不一樣的
流程，而畫面上沒有任何一個字說了這件事。
"""
from __future__ import annotations

from app.core.lineup import DUTY_PLAN, criteria_seat
from app.core.orchestrator_parsing import parse_criteria_rejection
from app.core.steps import Site, Stop, consulting, shown_name

# 每一格的做法在串起來的時候用這個分隔。後面那一格看得到前面每一格寫了什麼，
# 而它要分得出哪一段是誰寫的。
SEPARATOR = "\n\n---\n\n"


def draft(site: Site, criteria: str = "") -> tuple[str, Stop | None]:
    """把陣容裡每一個「擬做法」的節點依序跑過。

    2026-08-22 之前這裡只跑**一個**規劃者。老闆的五節點流程有兩個擬做法的
    位子（發想、規劃），第二個以前根本不會被派工——而畫面上沒有任何一個字
    說了這件事。
    """
    orchestrator = site.orchestrator
    pieces: list[str] = []
    bar_setter = criteria_seat(orchestrator.lineup)
    skip_the_bar_setter = _the_bar_setter_plans_elsewhere(
        orchestrator, criteria, bar_setter
    )

    for slot in orchestrator.lineup:
        if slot.duty != DUTY_PLAN:
            continue

        settled = _already_said(
            orchestrator, slot.role, criteria, bar_setter, skip_the_bar_setter
        )
        if settled is not None:
            pieces.append(_piece(slot.role, settled))
            continue

        # 畫面上寫使用者自己取的名字。出廠那三個英文角色名例外——
        # 「planner 正在擬做法」讀起來像系統訊息，不像在講他排的那一格。
        site.say(f"{shown_name(slot.role)}正在擬定做法")
        result, stop = consulting.ask(
            site,
            slot.role,
            lambda talk, seat=slot.role: orchestrator.run_work_plan(
                role=seat, discussion=talk
            ),
        )
        if stop is not None:
            return "", stop
        if not result.success:
            return "", Stop(
                status="PLANNER_ERROR",
                review_status="NOT_RUN",
                cli_result=result,
            )

        objection = parse_criteria_rejection(result.stdout)
        if objection and criteria.strip():
            return "", rejects_the_criteria(slot.role, objection)

        pieces.append(_piece(slot.role, result.stdout))

    return SEPARATOR.join(pieces), None


def _piece(role: str, text: str) -> str:
    return f"## {role}\n\n{text.strip()}"


def _the_bar_setter_plans_elsewhere(
    orchestrator, criteria: str, bar_setter: str  # noqa: ANN001
) -> bool:
    """訂標準的那一格要不要再擬一次做法，看它後面還有沒有別人在擬。

    老闆的五節點流程裡第一格是「發想與確認專案資訊」、第二格才是「規劃」——
    第一格的工作就是需求確認本身，再派它擬一次做法等於同一格做兩次事、多跑
    一次，而且第二次拿到的做法會跟它剛訂的標準各說各話。

    但它是**唯一**一格在擬做法的時候不能省：省掉的話這條線上就沒有人擬做法
    了，而畫面上不會有任何一個字說了這件事。
    """
    planners = [
        slot.role for slot in orchestrator.lineup if slot.duty == DUTY_PLAN
    ]
    return bool(criteria.strip()) and bar_setter in planners and len(planners) > 1


def _already_said(
    orchestrator,  # noqa: ANN001
    role: str,
    criteria: str,
    bar_setter: str,
    skip_the_bar_setter: bool,
) -> str | None:
    """這一格不用再派工的兩種情形，以及要用回哪一段話。

    回 `None` 代表要派工。
    """
    if role == bar_setter and skip_the_bar_setter:
        return criteria
    existing = orchestrator.stored_work_plan(role)
    if existing.strip():
        # 續跑：這一格已經跑過一次了，用回原來那份。重跑會拿到一份不一樣的
        # 做法，而後面已經蓋好的章全部會失效。
        return existing
    return None


def rejects_the_criteria(role: str, objection: str) -> Stop:
    """擬做法的那一格說這份驗收標準有問題。

    【每個 node 都能駁回】包含擬做法那幾格。它在擬做法的時候**正好是最有可能
    發現「這個標準訂錯了」的那一刻**——它要想清楚怎麼做，而想不出來的原因常常
    是標準本身有問題。沒有這條路的話，它只能硬擬一份做不到的做法，然後由執行
    那一格撞牆之後才退回去——多走一整輪，而且那一輪的產出沒有人要。

    **停下來交給使用者**，不像執行那一格那樣自動退回重訂。兩者的差別是證據的
    分量：執行那一格是**動手之後**發現做不到，那是量出來的；擬做法這一格是
    **還沒動手**就覺得不對，那是判斷。判斷不該直接推翻一份跟使用者確認過的
    標準——那條標準是他點頭的，而它連試都還沒試。
    """
    return Stop(
        status="PLAN_REJECTS_CRITERIA",
        review_status="NOT_RUN",
        note=(
            f"【{shown_name(role)}】在擬做法的時候認為這份驗收標準有問題，"
            f"還沒有人動手。\n\n它說：{objection}\n\n"
            "這一輪停在這裡——那份標準是你確認過的，而它連試都還沒試，"
            "所以不自動改。"
        ),
    )
