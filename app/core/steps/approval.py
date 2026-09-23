"""核准：看完所有的章，說這一整條鏈站不站得住。

老闆的五節點流程最後一句：**「NODE5 審閱所有簽章」**。

## 門後藏著什麼

* **這一輪什麼都沒做出來就不派它**——沒有產出就沒有東西可以拍板
* **有紅的機器章就不派它**——那不是它能翻的案
* 只看**還算數的**章（修好過的執行，鏈子上留著先前那個紅的驗證章）
* 說可以一定要寫理由，不寫當成無效回覆
* **沒排核准那一格的陣容，一次呼叫都不多**
* 章齊不齊是另一回事：`is_undecidable()`

## 兩扇門

    decide(site, deliverable) -> (None, Stop | None)
    is_undecidable(site, deliverable) -> Undecidable | None

分成兩扇是因為它們回答的是兩個問題：**「拍板那一格同不同意」**跟
**「這一版的章齊不齊」**。第二個沒有派任何工，它只是把帳算一次。
"""
from __future__ import annotations

from app.i18n import t

from dataclasses import dataclass

from app.core.lineup import chain_checker
from app.core.orchestrator_parsing import parse_approval
from app.core import run_stamps
from app.core.run_stamps import describe_chain, describe_readiness
from app.core.steps import Site, Stop, shown_name
from app.services.approval_chain import Result, StampKind, Versions


def decide(site: Site, deliverable: str) -> tuple[None, Stop | None]:
    """派核准那幾格審閱所有簽章。

    回傳的第一格永遠是 `None`——這扇門不產出東西，它只會蓋章或者擋下來。
    留著那一格是為了讓每一扇門長得一樣：`(產出, Stop | None)`。
    """
    orchestrator = site.orchestrator
    stamper = site.stamper

    # **先問「這一輪到底有沒有做出東西」。**排在最前面，連陣容都還沒看——
    # 沒有產出的話，有沒有那一格、章齊不齊都不改變「沒有東西可以拍板」。
    empty_handed = _nothing_was_produced(site, deliverable)
    if empty_handed is not None:
        return None, empty_handed

    # 老闆 2026-08-24 拍板：「規定要最後一個 NODE 去驗證。」
    #
    # 所以這是一條規定，不是選項：不管線排成什麼形狀，最後那一格都會被問
    # 「這一整條鏈站不站得住」。細節、代價、以及「為什麼這一格可以自動指定
    # 而執行那一格不行」寫在 `lineup.chain_checker`。
    checker = chain_checker(orchestrator.lineup)
    if not checker:
        # 空陣容。沒有格子就沒有最後一格。
        return None, None
    approvers = [checker]

    chain = stamper.chain()
    criteria = orchestrator.stored_acceptance_criteria()
    verdict = stamper.readiness(
        deliverable,
        # 透過模組叫，不是 `from ... import`：**「這一次該有哪些章」要有
        # 一個可以被換掉的地方**。綁成區域名字的話，程式搬一次家，那條
        # 「缺章要擋」的檢查就悄悄變成什麼都沒在守。
        run_stamps.requires_for(orchestrator.lineup),
        criteria=criteria,
    )

    blocked = _red_machine_stamps(chain, criteria, deliverable)
    if blocked is not None:
        return None, blocked

    for seat in approvers:
        site.say(f"{shown_name(seat)}正在審閱所有簽章")
        result = orchestrator.run_approval(
            role=seat,
            chain=describe_chain(chain),
            readiness=describe_readiness(verdict),
            # 老闆 2026-08-24：「NODE5 要可以看到產出與專案計畫書但不能更改。」
            # 沒有這一行，它要比對的兩樣東西只拿得到一樣。
            deliverable=deliverable,
        )
        if not result.success:
            return None, Stop(
                status="APPROVAL_ERROR",
                review_status="NOT_RUN",
                cli_result=result,
            )

        decision = parse_approval(result.stdout, seats=orchestrator.lineup.roles)
        stop = _why_it_did_not_stamp(seat, decision)
        if stop is not None:
            return None, stop

        stamper.approved(
            deliverable=deliverable,
            role=seat,
            reasoning=decision["reasoning"],
        )
    return None, None


def _nothing_was_produced(site: Site, deliverable: str) -> Stop | None:
    """這一輪一個檔案都沒改到的話，**不准拍板**，也不派核准那一格。

    ## 沒有這一道會怎樣

    實測過的樣子：一次沒有動到任何檔案的執行，`get_git_diff()` 回空字串，
    於是四個章的指紋全部是 `sha256("")` 的**同一個常數**，版本判定說「完全
    一致」，核准那一格拿到的提示詞裡寫著「（這一輪沒有產出可以比對。）」
    ——**然後它照樣蓋得出同意章，整輪收成 COMPLETED**。首頁上是薄荷色的
    「工作流程已完成」，跟一次真的做完了的執行一個字都不差。

    `approval_chain.ApprovalChain.readiness` 那五道（自己驗自己／有紅的／
    沒得驗／版本分岔／缺章）**沒有一道問「產出是不是空的」**，而 `Stamp` 的
    守衛只擋「指紋這一欄是空白」——空字串的 sha256 不是空白，它是一個長得
    非常正常的四十字元十六進位數。

    ## 為什麼在派工之前擋

    跟隔壁 `_red_machine_stamps` 同一個理由：**那不是它能回答的問題**。
    核准那一格的工作是拿計畫書去比對「這一輪做出來的東西」，而被比對的那
    一樣不存在。派它去，換到的是一個對著空白發表的意見——多等一輪、多吃
    一次額度，而它說什麼都不會改變「沒有東西可以拍板」這件事。

    ## 為什麼還要多問一次 `get_changed_files()`

    **`git diff` 端不出來的東西，不代表這一輪沒做事。**`get_changed_files()`
    跑的是 `git status --short`，而 `get_git_diff()` 那一支這一輪只要漏看一種
    改法，這裡就會把一次真的做了事的執行告訴他「一個檔案都沒有改到」——而他
    打開資料夾看得到那個檔案。**一條會誤報的檢查比沒有更貴：它會訓練人忽略
    紅燈。**

    短路排在 `deliverable.strip()` 後面，所以**正常的執行一次呼叫都不多**：
    只有 diff 真的空了才會去問第二個問題。

    ### 這一問今天接得住什麼（2026-08-25 當天縮小了兩次，照實寫）

    寫這一道的時候 `get_git_diff()` 只有一行 `git diff`：untracked 的新檔案
    看不到、`git add` 過的改動也看不到，兩種都是「真的做了事而 diff 是空的」。
    同一天那一支補了兩件事——新檔案報告，以及 `_diff_base()`（有 commit 的
    專案跟 `HEAD` 比，還沒 commit 過的跟 `--cached` 比）。**兩個實例都補掉了**，
    所以這第二問現在接得住的只剩邊角：`get_git_diff()` 那幾支 git 呼叫其中
    一支失敗、而 `git status` 還活著的那種。

    留著它不是因為今天量得到，是因為**這兩支的答案本來就該一致，而不一致的
    時候要往「放行」倒**：擋錯一次的代價是他去改一份沒有問題的交辦內容，
    放行一次的代價是核准那一格多看一段空白。

    ### 放行不等於它看得到東西（這一句不要被讀成安全）

    這一道回 `None` 只表示「不擋」。**deliverable 是不是空的，它不改**——真的
    出現 diff 空、status 有東西的那一輪，核准那一格拿到的提示詞裡會寫著
    「（這一輪沒有產出可以比對。）」，章照樣蓋在 `sha256("")` 上。那是「產出
    看不見」不是「沒有產出」，講成後者就是誤報，所以**不該在這裡擋**；要補
    的位置永遠是 `Orchestrator.get_git_diff()`。

    ## 這一道擋不住什麼（誠實講）

    * **擋不住「改了東西，但改的是垃圾」**——那正是核准那一格的工作。
    * **擋不住 `get_git_diff()` 自己失敗**：那時候它回的是 `GIT_DIFF_ERROR:⋯`
      而不是空字串，所以這裡放行，核准那一格會拿到那串錯誤訊息當「產出」。
      那是另一個洞，不在這一道守的範圍裡。
    * **擋不住計畫序列走進來，因為它根本走不到這裡。**
      `workflow_mode == "planning"` 那條路（`workflow_worker._run_planning`）
      的產出是一份計畫書，不是檔案改動，而它從頭到尾不經過這一扇門——它收在
      `PLANNING_COMPLETED`。寫下來是因為「會不會誤擋」要查的第一件事就是它。
    """
    if deliverable.strip():
        return None
    if site.orchestrator.get_changed_files():
        return None
    return Stop(
        status="NOTHING_TO_APPROVE",
        # 沿用隔壁那一道的 `NOT_RUN`：兩道停在同一個位置、走同一條
        # `_early_stop_result`，這裡自己挑一個不一樣的值只會讓兩種相鄰的
        # 停法在紀錄裡長得不一樣，而差別講不出理由。
        review_status="NOT_RUN",
        note=t("approval.nothing_was_produced"),
    )


def _red_machine_stamps(chain, criteria: str, deliverable: str) -> Stop | None:  # noqa: ANN001
    """鏈子上有還算數的紅色機器章的話，不派核准那一格。

    **那不是它能翻的案**：跑出來是紅的就是紅的，而讓它看一眼再說「可以」，
    等於多跑一次去換一個蓋不掉事實的意見。

    只看**還算數的**（`live`），不是全部。修正迴圈跑過的執行，鏈子上一定留著
    先前那個紅的驗證章——但它蓋在舊版的交付物上，早就不算數了。拿全部去比的
    話，**每一個修好過的執行都會被擋在最後一關**，而擋它的理由是一件已經被
    修掉的事。
    """
    alive = chain.live(Versions.of(criteria, deliverable))
    red = [
        stamp
        for stamp in alive
        if stamp.kind is StampKind.MACHINE and stamp.result is Result.FAIL
    ]
    if not red:
        return None
    return Stop(
        status="APPROVAL_BLOCKED",
        review_status="NOT_RUN",
        note=(
            t("approval.skipped_red_stamp")
            + "、".join(f"【{s.role}】{s.subject}" for s in red)
        ),
    )


def _why_it_did_not_stamp(seat: str, decision: dict) -> Stop | None:
    """回 `None` 代表它同意了，可以蓋章。"""
    shown = shown_name(seat)
    if not decision["decided"]:
        # **不猜。**猜成「同意」會蓋一個沒有人真的說過的章，而那個章讀起來
        # 跟真的一模一樣。
        return Stop(
            status="APPROVAL_INVALID",
            review_status="NOT_RUN",
            note=f"【{shown}】{decision['problem']}",
        )

    if not decision["approved"]:
        back = decision["who"]
        return Stop(
            status="APPROVAL_REJECTED",
            review_status="NOT_RUN",
            note=(
                f"【{shown}】不同意拍板，退給【{shown_name(back)}】。\n\n"
                f"理由：{decision['reasoning']}"
            ),
        )
    return None


@dataclass(frozen=True)
class Undecidable:
    """章不齊，以及為什麼。

    刻意**不是**結果字典：這一輪真的跑完了，三個執行結果都要照原樣帶進回報，
    而那份回報長什麼樣是主程式的事。這裡只講「憑什麼說還不能拍板」。
    """

    problem: str
    verdict: str
    # 尾巴那一句的 key。**存 key 不存字**，而且**沒有預設值**：兩種「還不能
    # 拍板」要接的話不一樣，給一個預設等於讓「我想過了」跟「我沒想過」長得
    # 一模一樣。
    #
    # 章少了 → 接「少一個章不是卡在那一關」。
    # 記帳自己算不出來 → **不准接那一句**：缺口可能是零個，那句話會叫使用者
    # 去找一個沒有人量過的東西，而他找不到。空字串代表「這一種不接尾巴」。
    note_key: str

    @property
    def told_to_the_user(self) -> str:
        tail = t(self.note_key) if self.note_key else ""
        return t("approval.not_decidable_yet") + self.problem + tail


def is_undecidable(site: Site, deliverable: str) -> Undecidable | None:
    """章齊不齊。不齊就**不准報告完成**。

    定案原文：

    > 最終決策成立的條件，是一路上每個 node 都對**同一版內容**有章。
    > 少一個章不是卡在那一關，**是整件事還不能拍板**。

    這不是失敗：測試過了、複查也同意了，**只是這一版還缺一個能拍板的憑據**。
    把它講成失敗，使用者會去修一個沒壞的東西；把它講成完成，那份紀錄事後
    回溯不出來。

    **算不出來也不准報告完成。**記帳壞掉不該把一次真的執行弄死——那條原則
    從 `RunStamper` 第一天就在，而且照舊有效。但「不弄死」不等於「說謊」：
    2026-08-24 之前，`readiness` 在裡面丟例外的時候回 `None`，而 `None` 走的是
    底下那條放行路徑，於是畫面顯示薄荷色的「工作流程已完成」——跟章真的齊了
    那一次一個字都不差。同一支函式少一個章的時候會說「還缺【某某】的驗證章」，
    **兩者差別只在有沒有例外，而使用者看不到那個差別。**

    所以算不出來的結局是 `NOT_DECIDABLE`＋「跑完了，但拍板的判定自己算不出來」。
    `NOT_DECIDABLE` 本來就被設計成**不是失敗**（`run_stamps` 開頭那一段），
    所以這不違反上面那條原則。
    """
    orchestrator = site.orchestrator
    verdict = site.stamper.readiness(
        deliverable,
        run_stamps.requires_for(orchestrator.lineup),
        criteria=orchestrator.stored_acceptance_criteria(),
    )
    if verdict is run_stamps.UNCOMPUTABLE:
        return Undecidable(
            problem=describe_readiness(verdict),
            # 借 outcomes.py 那張表的 `READY` 那一列——理由與代價寫在
            # `run_stamps.UNCOMPUTABLE_SCREEN_KEY` 上面。**不要在這裡寫死字串**：
            # 那張表認不得的字會讓畫面丟 `UnknownOutcome`，而那是一個真的當機。
            verdict=run_stamps.UNCOMPUTABLE_SCREEN_KEY,
            note_key="",
        )
    # **`None` 這條路現在只有測試替身走得到**——`RunStamper.readiness` 不再回
    # `None`。留著它是因為 `test_accounting_that_cannot_compute_does_not_kill_the_run`
    # 明文釘住「記帳算不出來不准弄死執行」這條原則，而那是老闆定的；把它一起
    # 改掉要他點頭（已寫進 2026-08-24 那一則的〈還缺一半〉）。
    if verdict is None or verdict.can_decide:
        return None
    return Undecidable(
        problem=describe_readiness(verdict),
        # 判定名稱要帶出去：`NOT_DECIDABLE` 不是一種收場而是六種，而「缺兩個
        # 章」跟「有檢查是紅的」使用者要做的事完全相反。少了它，畫面只能把
        # 兩件相反的事畫成同一格。
        verdict=verdict.verdict.name,
        note_key="approval.missing_stamp_note",
    )
