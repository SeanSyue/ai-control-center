"""驗證：跑專案自己宣告的那道檢查，並且把結果蓋成一個機器章。

## 三種結果，不是兩種

一般的直覺是「過」跟「不過」。**這裡有三種**，而第三種正是這個模組存在的
理由：

| | 意思 | 畫面上不准說 |
|---|---|---|
| 過 / 不過 | 真的跑過了 | |
| **沒宣告** | 這個專案從來沒說過要怎麼驗 | 不准說「測試紅了」 |
| **逾時** | 跑了，但沒有在時限內結束 | 不准說「程式壞了」 |

後兩種都**沒有任何東西被證明是壞的**。畫成紅燈的話，使用者會去修一個沒壞的
東西——而那是這條路線一路在消滅的假紅燈。

## 大門

    declared(site) -> str            # "" ＝ 有宣告；否則是問題
    check(site) -> Checked

`Checked` 刻意**不是**結果字典：這一輪的 claude 結果、測試結果都要照原樣帶進
回報，而那份回報有十九個欄位——門後的模組不該認識它。這裡只回答
「跑出什麼」與「這是哪一種收場」。
"""
from __future__ import annotations

from app.i18n import t

from dataclasses import dataclass

from app.core.steps import Site
from app.services.project_verification import (
    TESTS_BY_RUBRIC,
    TESTS_TIMED_OUT,
    VerificationNotDeclared,
)


@dataclass(frozen=True)
class Checked:
    """驗證跑完之後知道的事。

    `ending` 空字串代表可以往下走。**不是**「成功」——不過但還能修的那一種
    也是空字串，因為修正迴圈是主程式的事，不是這扇門的。
    """

    result: object | None = None
    ending: str = ""
    problem: str = ""

    @property
    def can_go_on(self) -> bool:
        return not self.ending


def declared(site: Site) -> str:
    """這個專案有沒有說過要怎麼驗。回 `""` 代表有。

    **在派任何工之前問，不是到了測試那一步才問。**一次沒有辦法檢查自己成果
    的執行，不管前三步發生什麼都會停在第四步——所以現在停，代價是一個設定
    對話框；晚一點停，代價是一次已經跑掉的派工，加上一棵被人動過的工作樹。
    """
    verification = site.orchestrator.verification()
    if verification.command is not None:
        return ""

    # **有寫但壞掉的，不准掉進質化那條路。**問的是「他寫過那一行沒有」，
    # 不是「那一行跑不跑得起來」——`.venv` 被刪掉的專案兩件事的答案不一樣，
    # 而 2026-08-24 第一版問錯了那一句，於是當天那個修法對它要修的情境無效。
    if verification.declared_in_file:
        return verification.problem

    # **沒有指令不等於沒有標準。**大部分辦公室工作（剪片、寫文件、做簡報）
    # 沒有單元測試，而 2026-08-24 之前這裡是一道沒有出口的牆：那種案子在
    # 派工之前就被擋下來，而畫面上講的是「請去設定 verify_command」——
    # 一句對剪片的人完全沒有意義的話。
    #
    # 質化驗收標準（`.aicc/project.json` 的 `acceptance`）是另一條路：
    # 幾個維度、每個維度幾個具體案例，交給審查那一格逐條回答。
    #
    # **但一個兩種都沒有的專案照樣不准跑。**「沒宣告」如果可以靜靜地變成
    # 「那就算過吧」，每一次執行都會拿到綠燈，而使用者要到第十次才發現
    # 沒有人在看。
    from app.services import qualitative_acceptance

    rubric = qualitative_acceptance.read(site.orchestrator.project_path)
    if rubric is None:
        return verification.problem

    problems = qualitative_acceptance.problems_with(rubric)
    if problems:
        # **有一份壞掉的標準比沒有更糟**：它看起來像已經設定好了。
        return t("verify.rubric_is_broken", problems="；".join(problems))

    # **沒有人過目的標準不是一把尺。**老闆 2026-08-24：「AI 起草是可以的，
    # 但起草的東西在他過目之前不算一把尺，也不准拿它量出來的結果當證據。」
    #
    # 擋在這裡（派工之前）而不是擋在測試那一步，理由跟這道門存在的理由
    # 完全一樣：現在停，代價是一個對話框；晚一點停，代價是一次已經跑掉的
    # 派工，加上一棵被人動過的工作樹。
    not_counted = qualitative_acceptance.why_it_does_not_count(rubric)
    if not_counted:
        return not_counted
    return ""


def check(site: Site) -> Checked:
    """跑一次專案宣告的檢查，並且蓋一個機器章。

    **成敗兩種都蓋。**只在成功的時候蓋的話，紀錄裡會缺掉每一次失敗的那一段
    ——而那正是事後想回頭看「它到底卡在哪」的人要找的東西。
    """
    orchestrator = site.orchestrator
    site.say(t("step.running_tests"))
    try:
        result = orchestrator.run_tests()
    except VerificationNotDeclared as missing:
        # 上面那道 `declared()` 已經問過了，所以走到這裡有兩種可能。
        #
        # **一、這是一個質化的案子**（剪片、寫文件、做簡報）：它沒有可執行的
        # 檢查，但有一份驗收標準。機器什麼都沒跑，判斷在審查那一格——
        # 把它畫成「尚未設定驗證」是叫一個已經設定好的人再設定一次。
        #
        # **但只有「從來沒宣告過指令」的專案才走這一條。**
        # `run_tests()` 有兩處丟這個例外，而第二處是「指令宣告了，
        # 但根本沒跑起來」（執行檔被刪了、路徑改了）。那一處的註解自己
        # 寫著要 stop and ask，而 2026-08-24 之前這裡兩種一視同仁——
        # 於是一個 `.venv` 不見的 AICC 會**一條測試都沒跑，然後蓋一個
        # 「用驗收標準驗過了」的章**。不是紅燈、不是警告，是一個看起來
        # 完全正常的成功。
        #
        # 判準寫成「有沒有宣告指令」而不是「是哪一種例外」，因為道理是
        # 一句話：**驗收標準不是指令的備胎。**它是給沒有指令的案子走的
        # 另一條路，不是給有指令的案子在指令壞掉時退回去的地方。
        # （老闆 2026-08-24：兩條路各自獨立，兩個都有的專案兩個都跑。）
        from app.services import qualitative_acceptance

        never_declared = not orchestrator.verification().declared_in_file
        rubric = (
            qualitative_acceptance.read(orchestrator.project_path)
            if never_declared
            else None
        )
        # `rubric.approved` 也要問：`declared()` 已經擋過一次，但那道門是
        # 派工**之前**問的，而使用者可以在那之後改動標準（畫面上編得動，
        # 那是刻意的）。**兩道門各問一次不是重複**——中間隔著一整輪派工。
        if (
            rubric is not None
            and not qualitative_acceptance.problems_with(rubric)
            and rubric.approved
        ):
            site.say(t("verify.no_machine_check"))
            # **照樣蓋章。**沒蓋的話，紀錄裡這一段會是空的，而事後回頭看的人
            # 分不出「機器驗過了」跟「這一步根本沒發生」。
            site.stamper.verified(
                deliverable=orchestrator.get_git_diff(),
                status=TESTS_BY_RUBRIC,
                reason=t("verify.by_rubric_reason", count=len(rubric.dimensions)),
            )
            # **記一次「用過了」。**沒有這一段的話 `still_learning` 永遠是
            # `True`，於是「請你自己核對我判得對不對」每一次都出現——而一個
            # 永遠不會消失的提醒，使用者第三次看到就開始跳過它。
            #
            # 記在這裡而不是記在審查那一格：**這一步是「這個專案用質化標準
            # 跑了一次」的唯一入口**，而審查那一格可能跑好幾輪。
            try:
                qualitative_acceptance.record_run(orchestrator.project_path)
            except OSError:
                # 記不起來不該讓這一次執行失敗——最壞的情況是那句提醒多出現
                # 一次，而那比把一次跑完的工作判成失敗好得多。
                pass
            return Checked()

        # **二、宣告是跑到一半消失的**（被改掉、或者檔案被刪了），
        # 或者**標準在派工之後被改了、而改完那一版還沒有人過目**。
        # 答案跟一開始一樣：不是紅燈，是停下來問。
        if rubric is not None:
            not_counted = qualitative_acceptance.why_it_does_not_count(rubric)
            if not_counted:
                return Checked(
                    ending="VERIFY_NOT_CONFIGURED", problem=not_counted
                )
        return Checked(ending="VERIFY_NOT_CONFIGURED", problem=missing.reason)

    site.stamper.verified(
        deliverable=orchestrator.get_git_diff(),
        status=result.status,
        reason=result.reason,
    )

    if result.status == TESTS_TIMED_OUT:
        # 時限到了不是紅燈：**沒有任何東西被證明是壞的**。所以刻意不派人去
        # 修——「修正造成測試失敗的問題」會把這一輪唯一的一次修正機會，花在
        # 一個沒有人有證據的 bug 上。
        return Checked(
            result=result, ending="VERIFY_TIMED_OUT", problem=result.reason
        )

    return Checked(result=result)
