"""需求確認：第一格跟使用者來回問，問到清楚為止。

這一步的產物就是**驗收標準**——後面每一格照它做，最後拿它驗收。

## 門後藏著什麼

* 問答輪要存下來，中斷續跑接得回去
* **沒有輪數上限**（定案原文不准用輪數收尾）
* 格式看不懂就停下來，**不猜**
* 使用者的答案只准被吃掉一次
* 訂好之後蓋章，並且寫進專案的計畫書
* 一格都不做事的陣容**沒有這一步**——執行的那一格不准自己訂標準

## 大門

    confirm(site) -> (驗收標準, Stop | None)

只有一個有意義。`Stop` 是「為什麼停」，不是回報——回報長什麼樣是主程式的事。
"""
from __future__ import annotations

from app.i18n import t

from app.core.lineup import criteria_seat
from app.core.orchestrator_parsing import parse_intake
from app.core.steps import Site, Stop, shown_name


def why_it_is_settled(rounds: list[tuple[str, str]]) -> str:
    """憑什麼認為問清楚了。審查章沒有這一格就蓋不出來，那是刻意的。

    寫的是**過程**不是結論：事後回溯的時候「來回了幾輪、最後問的是什麼」
    比「已確認」三個字有用得多。
    """
    if not rounds:
        return (
            t("intake.clear_enough")
        )
    question = rounds[-1][0].strip().replace(chr(10), " ")[:120]
    answer = rounds[-1][1].strip().replace(chr(10), " ")[:120]
    return (
        f"跟使用者來回 {len(rounds)} 輪之後訂下的。"
        f"最後一題：{question}／使用者答：{answer}"
    )


def confirm(site: Site) -> tuple[str, Stop | None]:
    """跟使用者把需求問清楚。

    **沒有輪數上限。**設了上限，到最後一輪不管清楚沒有，它都會為了結束而
    宣告定案——而後面每一格都會照著那份標準做。老闆的原話：「誇張來說問到
    天亮都沒問題」。
    """
    orchestrator = site.orchestrator
    seat = criteria_seat(orchestrator.lineup)
    if not seat:
        # 整條線只有執行的那一格：沒有人可以訂標準，而執行的自己訂就是自己
        # 出考卷自己考。
        #
        # **不擋**——擋掉的是出廠那張「一人」表格，而那張表格是拍過板的
        #（`DECISIONS.md`：一人表格「沒有人檢核」不必特別解釋，你跟單一 AI
        # 討論事情的時候本身就預設不會有其他 AI 或人來檢核）。
        #
        # **但不准安靜地跳過。**這裡原本寫的放行理由是「只是拍板檢查會誠實地
        # 說少一個章」，而那句話是假的：`requires_for()` 是**從陣容算的**，
        # `criteria_seat` 空的時候「驗收標準的章」那一項根本不會被要求，
        # 鏈子照樣回 READY、收場照樣是綠色的「工作流程已完成」。也就是說
        # 那句話唯一適用的情境，正是它不成立的情境——而它就掛在這裡當作
        # 不用做事的理由。
        #
        # 照隔壁那個對稱情境辦（`workflow_worker.py` 的 NO_REVIEWING_SEAT：
        # 「不准安靜地跳過：時間軸留一筆，不然使用者會得到一個『沒有人看過
        # 但看起來很正常』的結果」）。這一輪沒有驗收標準，時間軸要說出來。
        #
        # 字借 `CRITERIA_NO_OWNER` 那個收場的那一句——它講的就是同一件事
        # （「這條線上只有做事的那一格，沒有人可以訂標準」）。**刻意不另立
        # 一個新 key**：兩個 key 同一句中文，翻譯會翻出兩種說法，而
        # `test_the_catalogue_has_no_accidental_duplicates` 守的正是這個。
        orchestrator.timeline.append(
            orchestrator.run_id,
            "aicc",
            "no_criteria_seat",
            t("outcome.CRITERIA_NO_OWNER.stage"),
        )
        return "", None

    existing = orchestrator.stored_acceptance_criteria()
    if existing.strip():
        # 續跑：這一次已經問清楚了，不重問。重問會再等一輪，而且很可能拿到
        # 一份不一樣的標準——那會讓已經蓋在舊標準上的章全部失效。
        return existing, None

    rounds = list(orchestrator.stored_intake_rounds())
    waiting = bool(rounds) and not rounds[-1][1]

    if waiting and site.answer:
        # 使用者回答的是這一格問的那一題。`take()` 之後那個位子是空的——
        # 不清的話，底下「依照使用者答案繼續」那條路會拿同一句話再用一次，
        # 而那句話是回答別人的問題的。
        _question, text = site.answer.take()
        rounds[-1] = (rounds[-1][0], text)
        orchestrator.save_intake_rounds(tuple(rounds))
    elif waiting:
        # 還在等答案，而這一次沒有人帶答案回來（使用者關掉了對話框、或者
        # 從頭按了一次開始）。**不要再派一次工**——再派一次會問出一個新的
        # 問題，舊的那題就消失了，而且要再等一輪。
        return "", asking(rounds[-1][0], seat)

    site.say(f"{shown_name(seat)}正在跟你確認需求")
    result = orchestrator.run_intake(seat, tuple(rounds))
    if not result.success:
        return "", Stop(status="INTAKE_ERROR", cli_result=result)

    parsed = parse_intake(result.stdout)
    if not parsed["valid"]:
        # 兩個開頭都寫了、或者都沒寫。**不猜。**猜成「還要問」會把一坨命令列
        # 輸出丟到使用者臉上；猜成「定案」會拿一個問句當驗收標準，而後面
        # 每一格都照它做。
        return "", Stop(
            status="INTAKE_INVALID",
            note=f"【{shown_name(seat)}】{parsed['problem']}",
        )

    if not parsed["settled"]:
        rounds.append((parsed["question"], ""))
        orchestrator.save_intake_rounds(tuple(rounds))
        return "", asking(parsed["question"], seat)

    criteria = parsed["criteria"]
    orchestrator.save_acceptance_criteria(criteria)
    site.stamper.acceptance_set(
        criteria=criteria, role=seat, reasoning=why_it_is_settled(rounds)
    )
    return criteria, None


def asking(question: str, seat: str) -> Stop:
    """第一格有話要問使用者。

    `review_status` 用 `ASK_USER`，因為畫面上「有一題在等你」的整套動線
    （橫幅、回答按鈕、通知）已經綁在那個值上。**不另立一個新的等待狀態**
    ——第二種等待就是第二套要記得同步的畫面，而漏掉的那一次，使用者會看到
    一個沒有回答按鈕的問題。
    """
    return Stop(
        status="INTAKE_ASKING",
        review_status="ASK_USER",
        note=f"【{shown_name(seat)}】在動手之前要先跟你確認一件事。",
        question=question,
    )
