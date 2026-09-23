r"""他把五格取成自己的名字，**引擎每一步都還找得到人**。

## 2026-08-25 掃出來的：九個地方寫死出廠名字

使用者從陣容頁把座位改名（`set_slot_role`，同一天才做出來的控制項）之後，
引擎有九處直接拿 `ROLE_AUTHOR` / `ROLE_REVIEWER` 這種字面值去派工。實測的
下場，逐字：

> 這個專案的陣容裡沒有「reviewer」這一格，但工作流要派工給它。
> 目前排的是：node1、node2、node3、node4、node5。請到陣容頁重新編組。

**而那是在五格全部跑完、複查回 PASS、章都齊了之後才炸的**（維護共同記憶
那一步，出廠預設開著）。五次派工的額度已經花完，收場是「執行失敗」加一整坨
traceback。

`workflow_worker._roles_with()` 早就在依職責挑人了，而 `orchestrator.py`
沒有跟上——**同一個修法只落在一邊，另一邊照舊**。

## 為什麼這一支不逐行檢查程式碼

逐行檢查「有沒有出現 ROLE_AUTHOR」會誤報：那個常數當**預設參數**用是對的
（呼叫端會傳真名進來）。所以這一支問的是**行為**：拿一份五格自訂名字的陣容，
每一個派工入口都叫一次，看它挑不挑得出人。

新增一個入口而忘記依職責挑的話，這裡不會自己涵蓋它——所以底下另外有一條
`test_every_dispatch_entry_point_is_listed_here`，它會在派工入口變多的時候紅。
"""
from __future__ import annotations

import ast
import inspect
import io
import textwrap
import tokenize
from pathlib import Path

import pytest

from app.core.lineup import (
    DUTY_APPROVE,
    DUTY_PLAN,
    DUTY_REVIEW,
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.orchestrator import Orchestrator

NEWLINE = chr(10)

# 老闆的五節點測試：五格，**全部是他自己取的名字**。
HIS_FIVE = Lineup.of(
    [
        AgentSlot("node1", "claude", "c1", "", "你是需求釐清者。", DUTY_PLAN),
        AgentSlot("node2", "claude", "c2", "", "你是動手的那一位。", DUTY_WORK),
        AgentSlot("node3", "codex", "x1", "", "你是複查者。", DUTY_REVIEW),
        AgentSlot("node4", "codex", "x2", "", "你是第二位複查者。", DUTY_REVIEW),
        AgentSlot("node5", "claude", "c3", "", "你是拍板的人。", DUTY_APPROVE),
    ]
)


def _prose_lines(source: str) -> set[int]:
    """註解與 docstring 的行號。**它們提到那個名字不算違規。**

    第一版沒有這一段，於是 `run_claude` 那句在解釋「以前寫死 ROLE_AUTHOR
    會怎樣」的註解被判成違規——**一段在講「為什麼不要那樣寫」的話被當成
    那樣寫了**。

    這個專案為同一個形狀付過三次學費（2026-08-24 一天之內）：
    `tests/test_your_real_settings_stay_yours.py` 有一份同樣的
    `_prose_lines`，而它的檔頭寫著「**誤報會教下一個人把這條檢查當成
    狼來了**」。第四次。
    """
    out: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            out.add(token.start[0])
    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        if not isinstance(body[0], ast.Expr):
            continue
        first = body[0].value
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.update(
                range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1)
            )
    return out

class _Fake:
    """只有 `lineup` 的假 orchestrator——`seat_with_duty` 只讀那一個欄位。"""

    def __init__(self, lineup: Lineup) -> None:
        self.lineup = lineup


def _pick(lineup: Lineup, duty: str, fallback: str) -> str:
    return Orchestrator.seat_with_duty(_Fake(lineup), duty, fallback=fallback)


@pytest.mark.parametrize(
    "duty,expected",
    [
        (DUTY_PLAN, "node1"),
        (DUTY_WORK, "node2"),
        # 兩格複查的時候挑**最後一格**：後面的看過前面的，
        # 跟 `chain_checker` 挑驗鏈那一格同一套。
        (DUTY_REVIEW, "node4"),
        (DUTY_APPROVE, "node5"),
    ],
)
def test_every_duty_finds_the_seat_he_named(duty: str, expected: str) -> None:
    """**這一條是這份檔案存在的理由。**"""
    assert _pick(HIS_FIVE, duty, "沒有人") == expected


def test_a_missing_duty_falls_back_instead_of_guessing_a_seat() -> None:
    """排不到的時候用呼叫端給的退路，**不是自己挑一格代打**。

    挑一格代打的話，那一格會拿到使用者從來沒有授權給它的工作——而畫面上
    看起來一切正常。（同一句話 `steps/building.py` 已經寫過一次。）
    """
    only_work = Lineup.of([AgentSlot("node1", "claude", "", "", "", DUTY_WORK)])
    assert _pick(only_work, DUTY_APPROVE, "finisher") == "finisher"
    assert _pick(only_work, DUTY_REVIEW, "") == ""


def test_the_screen_says_what_each_seat_does_this_round() -> None:
    """五格自訂名字，**每一格都拿得到這一輪的工作**。

    改壞的樣子就是它 2026-08-25 之前的樣子：`SEQUENCE_JOBS` 用出廠名字當鍵，
    於是 `jobs` 是空的、`executed_slots` 是 0，而陣容頁把**每一格**都畫成
    虛線紅框加「不會被執行」——**同一時間引擎照跑照付錢**。
    """
    duties = tuple(slot.duty for slot in HIS_FIVE)
    development = Orchestrator.sequence_for("development", duties=duties)
    assert development["executed_slots"] == 5, development
    assert set(development["jobs"]) == {"0", "1", "2", "3", "4"}

    # 計畫序列裡「擬做法」那一種沒有第二件事可做——這一條序列本身就是在擬。
    # 「核准」那一種也不會跑：拍板那一關（`workflow_worker._approval_phase`）
    # 只長在開發序列那條路上，而 `_run_planning` 從頭到尾沒有碰過它
    # （2026-08-27 拿掉那一行）。
    #
    # 少那兩格是對的，**而少五格不是**——那才是這一條在守的東西。
    # 「表上的每一種真的會被派工」由
    # `test_the_plan_sequence_only_promises_seats_it_really_dispatches.py` 守，
    # 它是跑一輪之後拿引擎自己的紀錄去比，不是在這裡再抄一份數字。
    planning = Orchestrator.sequence_for("planning", duties=duties)
    assert planning["executed_slots"] == 3, planning
    assert "0" not in planning["jobs"]   # 擬做法
    assert "4" not in planning["jobs"]   # 核准


def test_the_two_columns_never_print_the_same_word() -> None:
    """職責欄與工作欄不准印出同一個字。

    2026-08-25 之前：一格「擬做法」職責的位子，職責欄與工作欄都印「擬做法」。
    三人模式排出來的座位更直接——**名字**就叫「執行」，而職責欄也是「執行」。

    同一列上重複的字沒有告訴使用者任何事，只會讓他以為畫面壞了。
    """
    from app.i18n import t  # noqa: PLC0415

    # 職責欄的四個字**住在頁面裡**（`AICC-視覺概念/lineup_v1.html` 的
    # `const DUTY`），所以這裡寫死它們是刻意的：這條檢查要的就是
    # 「引擎這一側的字不准跟那四個撞」。頁面改了字這條不會紅——
    # 那一半寫在交接檔裡請他們一起守。
    duty_words = {"擬做法", "執行", "複查", "核准"}
    jobs = [
        t(key) for key in (
            "job.plan", "job.build", "job.review",
            "job.approve", "job.read_plan", "job.write_plan",
        )
    ]
    for job in jobs:
        assert job not in duty_words, (
            f"工作欄印的「{job}」跟職責欄的四個字之一一模一樣"
        )
    seats = [t(k) for k in ("role.worker", "role.reviewer", "role.approver", "role.planner")]
    for seat in seats:
        assert seat not in duty_words, (
            f"出廠座位**名字**叫「{seat}」，跟職責欄的字一模一樣——"
            "名字是人的稱呼，職責是工作的種類"
        )


# 每一個「這一步派給誰」的入口。**新增一個就要加進來。**
DISPATCH_ENTRY_POINTS = (
    "run_claude",
    "run_claude_fix",
    "run_claude_with_user_answer",
    "run_codex_decision_gate",
    "run_codex_memory_update",
    "run_claude_memory_review",
    "run_codex_plan",
    "run_claude_plan_review",
    "run_work_plan",
    # 這六個是 `test_the_list_of_entry_points_has_not_gone_stale` 幫我補的
    # ——我手寫的清單漏了它們，而漏掉的入口一條檢查都沒有。
    "retry_step",
    "run_approval",
    "run_codex_review",
    "run_consult",
    "run_intake",
    "run_work_intent",
)


@pytest.mark.parametrize("name", DISPATCH_ENTRY_POINTS)
def test_no_dispatch_entry_point_hands_a_factory_name_to_the_engine(
    name: str,
) -> None:
    """派工那一行的第一個參數，**不准是一個出廠名字的常數**。

    **這一條掃的是原始碼，而上面那幾條量的是行為。**兩種都要：
    行為那幾條證明今天的路走得通，這一條擋的是明天有人再寫一行
    `self._run_role_prompt(ROLE_AUTHOR, ...)`——那一行今天不會讓任何
    行為測試變紅，因為沒有測試會走到它。

    第一版問的是「這支方法收不收 `role` 參數」，而 `retry_step` 兩個都不是
    （它從紀錄裡拿 `match.role`，那是真名，完全正確）——**一條會誤報的
    檢查比沒有更貴**，所以改成直接看那一行傳了什麼。
    """
    source = textwrap.dedent(inspect.getsource(getattr(Orchestrator, name)))
    prose = _prose_lines(source)
    for number, line in enumerate(source.splitlines(), start=1):
        if "ROLE_" not in line or number in prose:
            continue
        # **只剩一種寫法是對的：預設參數（`role: str = ROLE_X`）。**
        #
        # `seat_with_duty(..., fallback=ROLE_X)` 2026-08-27 之前也算對，而
        # 那是這條檢查上最大的一個洞：退回一個出廠名字**就是**這條檢查要擋
        # 的那件事，只是晚一行發生。他改過名的陣容裡沒有 `author`，所以
        # 「退回 author」跟「直接寫 author」到達下游時一模一樣——
        #
        #   NotImplementedError: 這個專案的陣容裡沒有「author」這一格⋯⋯
        #
        # 那一天四處全部換成 `required_seat_with_duty()`（排不到就丟
        # `NoSeatWithDuty`，由呼叫端收成一個講得出理由的結局），所以這個
        # 例外沒有人在用了——**趁沒有人用的時候把門關上**，不然下一個人
        # 抄的是舊寫法，而這條檢查會替他背書。
        #
        # 預設參數留著是對的：呼叫端會傳真名進來（`steps/building.py` 那幾
        # 支都是），而 `retry_step` 從紀錄裡拿 `match.role`。
        allowed = ": str = ROLE_" in line
        assert allowed, (
            f"`{name}` 第 {number} 行直接用了一個出廠名字："
            + line.strip()
            + "。使用者改過名的陣容在這裡會收到「這個專案的陣容裡沒有那一格」，而它通常炸在整輪的最後。"
            + "只有一種寫法是對的：預設參數（呼叫端傳真名進來）。"
            + "排不到那一種職責的時候用 required_seat_with_duty()，"
            + "由呼叫端決定收場，不要退回一個出廠名字。"
        )


def test_the_list_of_entry_points_has_not_gone_stale() -> None:
    """上面那張清單漏掉一個入口的話，那個入口一條檢查都沒有。

    掃 `orchestrator.py` 裡每一個叫 `_run_role_prompt` 的方法，
    跟那張清單比。**漏掉的會在這裡紅，不是安靜地不被檢查。**
    """
    source = Path("app/core/orchestrator.py").read_text(encoding="utf-8-sig")
    callers: set[str] = set()
    current = ""
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("def "):
            current = stripped[4:].split("(")[0]
        elif "_run_role_prompt(" in stripped and current:
            callers.add(current)
    missing = callers - set(DISPATCH_ENTRY_POINTS) - {"_run_role_prompt"}
    assert not missing, (
        f"這幾個派工入口不在清單上：{sorted(missing)}——"
        "加進 `DISPATCH_ENTRY_POINTS`，不要放著"
    )
