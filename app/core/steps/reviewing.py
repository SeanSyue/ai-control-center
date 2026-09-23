"""複查：陣容裡每一格複查都要看過，而且都要同意。

2026-08-22 之前這裡只跑**最後一格**。使用者排了兩格複查，引擎只跑一格，
而執行紀錄回報一切正常——老闆當天罵的就是這種：

> 他排得出來的東西，引擎跑不動，**而且不會有任何一個字說了這件事**。

## 門後藏著什麼

* 每一格都跑，有一格不同意就停在那裡——**不繼續問下一格**
* 【異議分兩級】只有**須處理**級會讓這一輪回頭重做
* 供參考級**不擋，但不准丟掉**，而且要署名
* 同意的每一格各蓋一個審查章，理由用它自己寫的那一句
* **說可以而沒寫憑什麼＝無效回覆**，不是通過（2026-08-24）
* **一條意見都沒列的 FIX 不算「只有供參考級」**，不准升級成通過（同上）

## 兩條「沒有人真的同意」的路，2026-08-24 補上

五節點測試前掃假綠燈掃到的：這扇門有兩條路會生出一個說著「我同意」的
審查章，而**沒有任何一格真的同意過**。

* 複查回 `PASS` 但 `summary` 是空的 → 以前補一句固定字串當理由，那一句
  剛好讓「審查章要寫為什麼同意」那條守衛放行，於是拍板檢查從「缺一個章」
  翻成「章齊了」。
* 複查回 `FIX` 但 `issues` 一條都沒有 → 以前被讀成「全是供參考級」而升級
  成 `PASS`，而它那句反對的話（「不能 PASS：⋯」）被當成**憑什麼同意**寫
  進審查章，還餵給核准那一格。

兩條的畫面都一樣：薄荷色的「工作流程已完成」。

## 大門

    look(site, ...) -> Reviewed

`Reviewed` 刻意**不是**結果字典：這一輪的 claude 結果、測試結果都要照原樣
帶進回報，而那份回報有十九個欄位——門後的模組不該認識它。
"""
from __future__ import annotations

from app.i18n import t

from dataclasses import dataclass, field

# `ROLE_REVIEWER` 這一支已經沒有人用了——2026-08-24 把 `_stamp` 那個
# 「沒有人同意就掛在 reviewer 名下」的預設值拿掉了。名字**留著匯入**是刻意的：
# `tools/mutations/mutations_last_seat_reads_stamps.json` 的 N8 會把它塞回
# `_stamp`（「複查的章寫死掛在 reviewer 名下」），而那條負向驗證要紅在
# **章掛錯角色**上，不是紅在 `NameError` 上——後者那條配方就不再證明任何事。
from app.core.lineup import DUTY_REVIEW, ROLE_REVIEWER  # noqa: F401
from app.core.steps import Site, shown_name


@dataclass(frozen=True)
class Reviewed:
    """複查跑完之後知道的事。

    `ending` 空字串代表可以往下走。**不是**「都同意了」——被要求修改的那一種
    也是空字串，因為修正迴圈是主程式的事，不是這扇門的。
    """

    status: str = ""
    data: dict = field(default_factory=dict)
    # 供參考級的意見，**帶著是誰說的**。純字串陣列的話，畫面只寫得出
    # 「有人說了一句話」，而使用者接下來第一個問題一定是「誰說的」。
    notes: list[dict] = field(default_factory=list)
    codex_result: object | None = None
    ending: str = ""

    @property
    def everybody_agreed(self) -> bool:
        return self.status == "PASS"


def look(
    site: Site,
    *,
    claude_result,  # noqa: ANN001
    test_result,  # noqa: ANN001
    git_diff: str,
    changed_files,  # noqa: ANN001
) -> Reviewed:
    """派每一格複查去看，同意的就蓋章。"""
    orchestrator = site.orchestrator
    seats = [
        slot.role for slot in orchestrator.lineup if slot.duty == DUTY_REVIEW
    ]
    if not seats:
        # 東西做出來了，但沒有人審。**不當成成功**：最終決策要一路上每個
        # node 都有章，而沒有複查的那一格，章就少一個。
        return Reviewed(ending="NO_REVIEWING_SEAT")

    site.say(t("step.collecting_diff"))
    agreed: list[tuple[str, str]] = []
    notes: list[dict] = []
    codex_result = None
    data: dict = {}
    status = ""

    for seat in seats:
        site.say(f"{shown_name(seat)}正在複查")
        codex_result = orchestrator.run_codex_review(
            claude_result=claude_result,
            test_result=test_result,
            git_diff=git_diff,
            changed_files=changed_files,
            role=seat,
        )
        if not codex_result.success:
            return Reviewed(
                status="UNKNOWN",
                codex_result=codex_result,
                notes=notes,
                ending="CODEX_ERROR",
            )

        data = orchestrator.parse_review(codex_result.stdout)
        status = data["status"]

        if status == "FIX" and not data["blocking_issues"]:
            # 【異議分兩級】只有**須處理**級會讓這一輪回頭重做。
            #
            # 老闆的原話：「如果這項決議沒有重要到去中斷的話　這樣不合邏輯」。
            # 只有一種異議的話，任何一句「順帶一提」都會讓整件事重跑一輪——
            # 而為了避開那件事就把意見全當成不重要，審查等於沒有牙齒。
            #
            # 這裡**不收意見**：改成 PASS 之後底下那一行會收一次。兩邊都收
            # 的話同一句話會進去兩次，而畫面照著印，使用者會以為有兩個人講了
            # 一模一樣的話。
            #
            # **一條意見都沒有的 FIX 不在這一條裡面。**「全是供參考級」的前提
            # 是真的有被評過級的意見；`issues` 空的時候兩邊都是空的，而空的
            # 那一邊被讀成「沒有須處理的」，於是一次明白的反對被升級成通過。
            # 2026-08-24 實跑到的樣子：複查回
            # `{"status":"FIX","summary":"不能 PASS：⋯","issues":[]}`，
            # 收場是「工作流程已完成」，而章上那句「憑什麼同意」寫的正是
            # 那句反對的話——核准那一格讀到的也是它。
            #
            # 定案是相反的：「沒有標級的一律算須處理——分不出來的時候停下來
            # 問人比默默放行便宜」。**零條意見是最極端的分不出來。**
            if data["note_issues"]:
                status = "PASS"
                data = dict(data, status="PASS")

        if status != "PASS":
            # **不繼續問下一格。**一份已經被要求修改的東西，再多幾個意見也
            # 不會讓它變成不用改——那幾次呼叫買不到任何東西。
            break

        why = _why_it_passed(data)
        if not why:
            # 說可以，卻沒有寫憑什麼。**當成無效回覆，不是當成通過。**
            #
            # 核准那一格遇到一模一樣的狀況走的就是這條路
            # （`approval._why_it_did_not_stamp`：「**不猜。**猜成「同意」會蓋
            # 一個沒有人真的說過的章，而那個章讀起來跟真的一模一樣」）。複查
            # 這一側以前反過來——`_why_it_passed` 補一句「複查回報 PASS，但
            # 沒有寫理由。」，而那一句剛好夠 `RunStamper._put` 放行
            # （它擋的是**空的**理由），於是章蓋出來、拍板檢查從
            # 「缺【複查】的審查章」翻成「章齊了」。
            #
            # **不要只做「不蓋章」那一半。**靜靜少一個章的話，使用者看到的是
            # 「還缺一個章」，而不知道是複查說可以卻沒說憑什麼——那是他唯一
            # 能處理的那半句。所以時間軸留一筆（比照 `no_reviewing_seat`），
            # 畫面也當場說一句。
            site.say(t("review.pass_without_reason"))
            orchestrator.timeline.append(
                orchestrator.run_id,
                "aicc",
                "review_pass_without_reason",
                t("review.pass_without_reason"),
            )
            return Reviewed(
                status="UNKNOWN",
                data=data,
                notes=notes,
                codex_result=codex_result,
            )

        notes.extend(_notes_from(shown_name(seat), data))
        agreed.append((seat, why))

    if status == "PASS":
        _stamp(site, agreed, git_diff)

    return Reviewed(
        status=status, data=data, notes=notes, codex_result=codex_result
    )


def _why_it_passed(data: dict) -> str:
    """憑什麼同意——**只有它自己寫的那一句，補不出來就是空的**。

    以前這裡有一個 `or t("review.pass_without_reason")`。那不是「找不到就
    講一句實話」，那是**把一個蓋不出來的章變成蓋得出來**：下一層
    （`RunStamper._put`）擋的條件是理由為空，補上那一句就剛好過關，而
    `run_stamps.py` 自己的註解寫著「這裡不修補它——一個被硬湊出來的章，
    讀起來跟一個真的章一模一樣」。

    回空字串之後，判斷「那算不算通過」是呼叫端的事，不是這裡的事：
    上面那一段把它當成無效回覆，並且說出為什麼。
    """
    return (
        str(data.get("summary") or "").strip()
    )


def _notes_from(shown: str, data: dict) -> list[dict]:
    """供參考級的意見，**帶著是誰說的**。

    不擋不等於丟掉。「不擋」變成「聽過就算了」的話，使用者事後最想知道的
    那一半就沒了——而畫面上會顯示一次乾乾淨淨的通過。
    """
    return [
        {"role": shown, "text": str(issue)}
        for issue in data.get("note_issues", ())
    ]


def _stamp(site: Site, agreed: list[tuple[str, str]], git_diff: str) -> None:
    """審查章：沒有結果也沒有證據，它說的是「我看過而且同意」。

    它跟驗證的機器章**故意不能互相取代**：一個是量出來的，一個是判斷。

    每一格同意的都蓋一個，**蓋在它自己的名字底下**，理由用它自己寫的那一句。
    重寫一遍等於紀錄裡有兩種說法，而事後看的人不知道該信哪一個。

    **一格都沒有同意就一個章都不蓋。**2026-08-24 之前這裡寫的是
    `agreed or [(ROLE_REVIEWER, t("review.pass"))]`——一個把「沒有人同意」
    補成「reviewer 同意了」的預設值。今天走不到（`_stamp` 只在
    `status == "PASS"` 之下被叫，而通往 PASS 的唯一路徑會先 `agreed.append`），
    但它補出來的那個章**掛在一個不一定存在於這個陣容裡的角色底下**，正是
    `test_the_review_stamp_carries_the_seat_that_actually_reviewed` 在防的事。
    留著一個現在走不到的預設值，等於留一個以後會咬人的預設值。
    """
    for seat, why in agreed:
        site.stamper.reviewed(deliverable=git_diff, reasoning=why, role=seat)
