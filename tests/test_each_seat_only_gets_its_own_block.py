"""派工的時候，每一格只收到「底座＋自己那塊」。

定案原文（2026-08-21）：**不變式：不准全量下發。**
老闆 2026-08-23 講了兩次：「不就是寫成檔案讓 node 去看嗎???」「短指標是對的」。

2026-08-23 之前：**八份全文，每一格一模一樣，每一次派工都送**——
29,343 字、約 9,781 token，而五節點跑一次就是五次。

## 這一份守的三件事

1. **底座逐字送。**限制它的那幾份不准只給指標——AI 不會主動去讀一份限制
   自己的檔案。
2. **其餘不逐字送。**否則「分三層」只是換一個說法。
3. **要點名。**只給索引不點名，等於把「AI 可能不去讀」那個風險原封不動留著。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import rule_layers
from app.core.lineup import VALID_DUTIES
from app.services.project_knowledge import (
    MEMORY_FILES,
    PROJECT_RULES_FILE,
    ProjectKnowledgeStore,
)

BIG = "決定：這一條很長，長到看得出它有沒有被整份塞進去。" * 40


@pytest.fixture()
def store(tmp_path: Path) -> ProjectKnowledgeStore:
    made = ProjectKnowledgeStore(tmp_path)
    made.knowledge_path.mkdir(parents=True, exist_ok=True)
    for name in MEMORY_FILES:
        (made.knowledge_path / name).write_text(
            f"# {name}\n\n{BIG}", encoding="utf-8"
        )
    return made


def test_the_safety_rules_still_arrive_word_for_word(store) -> None:  # noqa: ANN001
    """**底座逐字送。**這一條是分層之後最容易掉的東西。"""
    for duty in VALID_DUTIES:
        sent = store.build_dispatch_context(duty)
        for name in rule_layers.BASE_FILES:
            body = (store.knowledge_path / name).read_text(
                encoding="utf-8"
            ).strip()
            assert body in sent, f"{duty} 那一格收到的 {name} 不是全文"


def test_the_big_ones_are_not_pushed_any_more(store) -> None:  # noqa: ANN001
    """**其餘不逐字送。**不然「分三層」只是換一個說法。"""
    for duty in VALID_DUTIES:
        sent = store.build_dispatch_context(duty)
        body = (store.knowledge_path / "DECISIONS.md").read_text(
            encoding="utf-8"
        ).strip()
        assert body not in sent, f"{duty} 那一格還是收到 DECISIONS.md 全文"


def test_the_seat_is_told_which_ones_to_read(store) -> None:  # noqa: ANN001
    """**要點名。**只給索引不點名，那個風險原封不動留著。"""
    for duty in VALID_DUTIES:
        sent = store.build_dispatch_context(duty)
        for name in rule_layers.read_before_starting(duty):
            assert name in sent, f"{duty} 那一格沒有被點名要讀 {name}"
        assert "開工之前要先讀" in sent


def test_two_duties_do_not_get_the_same_thing(store) -> None:  # noqa: ANN001
    """不同職責收到的東西要真的不一樣。

    改壞的樣子：`read_before_starting` 對每一種職責回同一份——那樣「角色積木」
    這個詞在程式裡存在，而實際行為跟全量下發一模一樣。
    """
    plan = store.build_dispatch_context("plan")
    work = store.build_dispatch_context("work")
    assert plan != work
    assert "NEXT_STEPS.md" in plan.split("開工之前要先讀")[1]
    assert "ARCHITECTURE.md" in work.split("開工之前要先讀")[1]


def test_the_pointer_never_names_a_file_that_is_not_there(tmp_path: Path) -> None:
    """索引上不准出現這個專案沒有的檔案。

    列了不存在的檔案，AI 會去讀、讀不到、然後開始猜為什麼——**那一輪就浪費
    在一個我們自己造出來的謎題上**。
    """
    made = ProjectKnowledgeStore(tmp_path)
    made.knowledge_path.mkdir(parents=True, exist_ok=True)
    for name in rule_layers.BASE_FILES:
        (made.knowledge_path / name).write_text("底座", encoding="utf-8")
    (made.knowledge_path / "DECISIONS.md").write_text("定案", encoding="utf-8")

    sent = made.build_dispatch_context("work")
    assert "DECISIONS.md" in sent
    assert "ARCHITECTURE.md" not in sent, "點名了一個這個專案沒有的檔案"
    assert "BUSINESS_MODEL.md" not in sent


def test_the_users_own_rules_are_pushed_word_for_word(tmp_path: Path) -> None:
    """使用者訂的規矩跟底座同一種東西：**不准做的事**，所以逐字送。"""
    made = ProjectKnowledgeStore(tmp_path)
    made.knowledge_path.mkdir(parents=True, exist_ok=True)
    for name in rule_layers.BASE_FILES:
        (made.knowledge_path / name).write_text("底座", encoding="utf-8")
    rule = "這個專案不准碰 vendor/，而且測試要跑兩次。"
    (made.knowledge_path / PROJECT_RULES_FILE).write_text(rule, encoding="utf-8")

    assert rule in made.build_dispatch_context("work")


def test_the_document_path_is_untouched(store) -> None:  # noqa: ANN001
    """給**人**看的那條路沒有變。

    交接包、啟動脈絡、文件頁的預覽都走 `build_memory_context()`——那些是他
    自己要讀的東西，不是派工。**分層只動派工那條路。**
    """
    full = store.build_memory_context()
    for name in MEMORY_FILES:
        body = (store.knowledge_path / name).read_text(
            encoding="utf-8"
        ).strip()
        assert body in full, f"給人看的那一份少了 {name}"


def test_it_actually_saves_something(store) -> None:  # noqa: ANN001
    """省不到就是白做。

    沒有這一條，上面每一條都可以靠「底座＝八份」通過——而那正是這一輪要
    消滅的東西。
    """
    full = len(store.build_memory_context())
    sent = len(store.build_dispatch_context("work"))
    assert sent < full / 2, f"派工還是送了 {sent}／{full} 字"
