"""NODE5 看得到產出與專案計畫書，但不能改；有問題丟回對應的 node 重新簽核。

老闆 2026-08-24 的原話：

> node5 要可以看到產出與專案計畫書但不能更改，有問題要丟回對應的 node 去處理
> 重新簽核
> 而且**每個 node 都要能看到專案計畫書啊，不然怎麼做專案**

在這之前它兩樣都看不到——只拿得到章。**那樣它做得到「章的總覽」，做不到
「比較」**，而比較正是他給這一格的定義。

這一份打在**那扇門**上（`approval.decide`），不是打在提示詞組裝函式上：
組裝函式收得到參數，不代表有人真的傳給它。2026-08-24 就是這樣——
`build_approval_prompt` 收得下 `deliverable`，而門那一側漏了那一行，
金標快照因為直接呼叫 `run_approval` 所以完全看不到。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.lineup import DUTY_APPROVE, DUTY_WORK, AgentSlot, Lineup
from app.core.steps import Site, approval


@dataclass
class _Result:
    success: bool = True
    return_code: int = 0
    stdout: str = "拍板：可以\n理由：三個章都在，而且都蓋在同一版上。"
    stderr: str = ""


@dataclass
class _FakeOrchestrator:
    lineup: Lineup
    asked: dict = field(default_factory=dict)

    def stored_acceptance_criteria(self) -> str:
        return "1. 存得起來。"

    def run_approval(self, **kwargs) -> _Result:  # noqa: ANN003
        self.asked = kwargs
        return _Result()


@dataclass
class _FakeStamper:
    stamped: list = field(default_factory=list)

    def chain(self):  # noqa: ANN201
        from app.services.approval_chain import ApprovalChain

        return ApprovalChain()

    def readiness(self, deliverable, requires, criteria=""):  # noqa: ANN001
        return None

    def approved(self, **kwargs) -> None:  # noqa: ANN003
        self.stamped.append(kwargs)


def _site(said: list) -> Site:
    lineup = Lineup.of(
        [
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("審閱", "codex", duty=DUTY_APPROVE),
        ]
    )
    return Site(
        orchestrator=_FakeOrchestrator(lineup),
        stamper=_FakeStamper(),
        on_stage=said.append,
    )


DIFF = "--- a/settings.py\n+++ b/settings.py\n+儲存了\n"


def test_node5_is_given_the_thing_it_has_to_compare() -> None:
    """**這一條是這份檔案存在的理由。**

    要它比對「產出跟計畫書對不對得上」，而被比對的那一樣沒給它，它只能
    對著章說「章齊了」——那是機器已經算出來的事，等於那一格白跑。
    """
    said: list = []
    site = _site(said)

    approval.decide(site, DIFF)

    assert site.orchestrator.asked, "根本沒有派核准那一格"
    assert site.orchestrator.asked.get("deliverable") == DIFF, (
        "產出沒有交到 NODE5 手上："
        f"{site.orchestrator.asked.get('deliverable')!r}"
    )


def test_it_still_gets_the_chain_and_the_verdict() -> None:
    """比對是**多**的那一半，不是換掉原本那一半。"""
    site = _site([])
    approval.decide(site, DIFF)

    assert "chain" in site.orchestrator.asked
    assert "readiness" in site.orchestrator.asked


def test_the_prompt_tells_it_not_to_change_anything() -> None:
    """看得到不等於改得動。

    老闆的原話是「可以看到⋯⋯**但不能更改**，有問題要丟回對應的 node 去處理
    重新簽核」。這一格改東西的話，它改的東西上面沒有任何人的章，
    而它自己剛剛才要蓋章說「我看過了」。
    """
    from app.core import prompt_builder

    prompt = prompt_builder.build_approval_prompt(
        task="做一個小工具",
        acceptance_criteria="1. 存得起來。",
        chain="【實作】的執行章",
        readiness="缺 1 個章",
        project_brief="目標：三分鐘整理完錯題。",
        deliverable=DIFF,
    )

    assert "目標：三分鐘整理完錯題。" in prompt
    assert DIFF.strip() in prompt
    assert "不是給你改的" in prompt
    assert "退回去" in prompt


def test_a_project_with_no_brief_says_so_instead_of_going_quiet() -> None:
    """沒有計畫書的時候要講出來，不是留一段空白。

    留空白的話，NODE5 會以為「這個專案就是沒有計畫書要比」——而實際上可能是
    第一格根本沒跑完。
    """
    from app.core import prompt_builder

    prompt = prompt_builder.build_approval_prompt(
        task="做一個小工具",
        acceptance_criteria="1. 存得起來。",
        chain="（沒有章）",
        readiness="缺 3 個章",
    )

    assert "還沒有計畫書" in prompt
    assert "沒有產出可以比對" in prompt


def test_every_node_gets_the_brief_not_just_the_first_one() -> None:
    """老闆：「每個 node 都要能看到專案計畫書啊，不然怎麼做專案」。

    在這之前只有需求確認那一格讀得到它（它是寫的人），後面每一格都在對著
    一份自己沒看過的計畫書做事——而它們交出來的東西看起來完全正常。
    """
    source = Path("app/core/orchestrator.py").read_text(encoding="utf-8")
    assert "_with_project_brief" in source
    # 掛在 `_knowledge_context` 上，所以每一個拿脈絡的派工都會拿到。
    body = source[source.index("def _knowledge_context") :][:600]
    assert "_with_project_brief" in body, (
        "計畫書沒有掛在每一格都會拿到的那條路上"
    )
