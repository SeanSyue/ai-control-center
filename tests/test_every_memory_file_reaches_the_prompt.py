"""共同記憶有八份，畫面說「已就緒」，而實際送到 prompt 的一直不是八份。

這件事修過兩次，前一次修錯了對象：

| 日期 | 狀況 | 修法 |
|---|---|---|
| 2026-08-11 | 照字典順序填、填滿就 `break`，**8 份裡只有 2 份進得去** | 公平配額 + 被剪掉的自己講 |
| 2026-08-21 | 公平配額之下，出廠預設（minimal／6,000）**8 份全部被切成 750 字** | 把截斷整個拿掉 |

第二次的關鍵是：**公平配額把「六份完全不見」換成了「八份各剩三成」。**
看起來好很多，實際上更難發現——一份被切一半的安全規則讀起來跟完整的一模一樣，
而 `SECURITY_MODEL.md` 斷在「執行期狀態（DB、鎖、快取、暫」，「暫存」兩個字被
切成兩半。不是因為那半句不重要，只是因為它排在第 750 個字。

所以這一組現在釘的不是「配額分得公不公平」，是**根本沒有配額**：
八份，全文，一個字都不切。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.project_knowledge import (
    CONTEXT_PROFILES,
    MEMORY_FILES,
    ProjectKnowledgeStore,
)

TITLES = tuple(MEMORY_FILES.values())
# 真實形狀：一份大到自己就吃掉當年整個預算，其餘落差很大。合計約 96k，
# 是瘦身之前 AICC 自己的量——**刻意留著**，因為新規則的意義正是「即使長成
# 這樣也不切」。
REAL_SHAPE = (5_293, 26_424, 21_640, 10_454, 4_684, 12_013, 5_500, 10_738)
# 當年的三個上限。它們現在一個都不該有作用，而這正是要釘住的事。
FORMER_BUDGETS = (6_000, 12_000, 22_000)


def _overhead(title: str) -> int:
    """標題那一行的長度——內容之外的額外量。"""
    return len(f"### {title}\n")


# 一整段（標題 + 內容）的合計，`memory_total_characters` 要對得上這個數字。
TOTAL_WITH_HEADERS = sum(REAL_SHAPE) + sum(_overhead(title) for title in TITLES)


def _store(root: Path, sizes: tuple[int, ...] = REAL_SHAPE) -> ProjectKnowledgeStore:
    project = root / "proj"
    store = ProjectKnowledgeStore(project)
    for file_name, size in zip(MEMORY_FILES, sizes):
        # 內容剛好 size 個字，開頭放檔名讓測試分得出哪一段是誰的，
        # 結尾放一個記號讓測試看得出**最後一句有沒有到**。
        prefix = f"{file_name}:"
        suffix = f":{file_name}尾"
        body = prefix + "字" * max(0, size - len(prefix) - len(suffix)) + suffix
        (store.knowledge_path / file_name).write_text(body, encoding="utf-8")
    return store


def _verdict(context: str, title: str) -> str:
    if f"### {title}\n（這次沒有送出" in context:
        return "named-only"
    if f"### {title}（節錄 " in context:
        return "truncated"
    if f"### {title}\n" in context:
        return "full"
    return "absent"


# --- 這一組存在的理由 --------------------------------------------------------


def test_no_memory_file_is_silently_absent(tmp_path: Path) -> None:
    """第一次修好之前這條會抓到六份不見了。"""
    context = _store(tmp_path).build_memory_context()

    absent = [title for title in TITLES if _verdict(context, title) == "absent"]

    assert absent == [], f"這幾份沒有進 prompt：{absent}"


def test_every_memory_file_arrives_whole(tmp_path: Path) -> None:
    """八份全部是 `full`，而且**最後一個字**也在。

    只檢查「有出現」不夠：被截斷的檔案開頭一模一樣，差別在尾巴。
    """
    context = _store(tmp_path).build_memory_context()

    assert [_verdict(context, title) for title in TITLES] == ["full"] * 8
    for file_name in MEMORY_FILES:
        assert f":{file_name}尾" in context, f"{file_name} 的結尾沒有送到"
    assert "節錄" not in context
    assert "這次沒有送出" not in context


def test_a_96k_memory_set_is_not_cut_at_any_of_the_old_budgets(tmp_path: Path) -> None:
    """合計 96k 的八份，全部進得去。

    這是這次改動的整句話。舊行為在三個上限下分別會送出 6,000／12,000／22,000
    字；現在送出的是全部，所以拿舊上限當下界來釘。
    """
    context = _store(tmp_path).build_memory_context()

    assert len(context) > max(FORMER_BUDGETS)
    assert len(context) >= sum(REAL_SHAPE)


def test_the_biggest_file_no_longer_starves_or_gets_starved(tmp_path: Path) -> None:
    """26k 的架構筆記**整份**都要在。

    舊測試釘的是「它拿到的不能超過均分應得的量」——那是配額時代的公平問題。
    現在沒有要分的東西，該釘的是它有沒有被切。
    """
    store = _store(tmp_path)
    architecture = (store.knowledge_path / "ARCHITECTURE.md").read_text(
        encoding="utf-8"
    )

    context = store.build_memory_context()

    assert architecture in context
    assert len(architecture) > max(FORMER_BUDGETS)


def test_no_profile_cuts_the_memory(tmp_path: Path) -> None:
    """三個設定檔跑同一份 96k 記憶，送出去的記憶內容必須一模一樣。

    `build_context` 當年在配額之外**又**切了一刀
    （`memory_context[: profile.memory_chars]`），而且無聲——連「節錄」標籤都
    在切之前就被切掉了。所以要打的是完整組裝路徑，不是只有
    `build_memory_context()`。
    """
    store = _store(tmp_path)
    memory = store.build_memory_context()

    for name in CONTEXT_PROFILES:
        context = store.build_context("任務", context_profile=name)
        assert memory in context, f"{name} 送出去的共同記憶不是全文"


def test_the_profiles_have_no_memory_limit_to_reintroduce() -> None:
    """設定檔身上不准再有「記憶字數上限」這種欄位。

    釘欄位而不只釘行為，是因為那個欄位一旦回來，下一個人會很自然地把它接回
    組裝路徑上——而接回去的那一天，畫面上不會有任何差別。
    """
    for name, profile in CONTEXT_PROFILES.items():
        fields = set(vars(profile))
        assert "memory_chars" not in fields, name
        assert not [f for f in fields if "memory" in f], (name, fields)


def test_a_file_that_is_empty_or_missing_is_simply_not_there(tmp_path: Path) -> None:
    """沒有內容的檔案不佔位置，也不假裝自己被剪掉了。"""
    store = _store(tmp_path, sizes=(200,) * 8)
    (store.knowledge_path / "SECURITY_MODEL.md").unlink()
    (store.knowledge_path / "NEXT_STEPS.md").write_text("   \n", encoding="utf-8")

    context = store.build_memory_context()

    assert _verdict(context, MEMORY_FILES["SECURITY_MODEL.md"]) == "absent"
    assert _verdict(context, MEMORY_FILES["NEXT_STEPS.md"]) == "absent"
    assert context.count("### ") == 6


# --- 缺口數字：現在恆為 0，而且要是算出來的 ----------------------------------


def test_nothing_is_reported_as_omitted_because_nothing_is(tmp_path: Path) -> None:
    """`omitted_memory_characters` 現在是 0——但要是量出來的 0。

    寫死一個 0 的話，哪天有人又加了一刀，畫面上仍然會是 0，而那正是這個
    專案反覆遇到的形狀。
    """
    store = _store(tmp_path)

    stats = store.context_stats("任務", context_profile="minimal")

    assert stats["omitted_memory_characters"] == 0
    # 前提檢查：真的有一大包記憶在，不是因為沒有東西才等於 0。
    assert stats["memory_characters"] >= sum(REAL_SHAPE)
    assert store.memory_total_characters() == TOTAL_WITH_HEADERS


@pytest.mark.parametrize("profile", tuple(CONTEXT_PROFILES))
def test_the_memory_size_does_not_depend_on_the_profile(
    tmp_path: Path, profile: str
) -> None:
    # 三個設定檔管的是**文件佐證**的廣度，不是共同記憶。
    store = _store(tmp_path)

    stats = store.context_stats("任務", context_profile=profile)

    assert stats["memory_characters"] == len(store.build_memory_context())


def test_the_total_ignores_the_budget_entirely(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.memory_total_characters() == TOTAL_WITH_HEADERS


def test_the_total_skips_files_that_are_not_there(tmp_path: Path) -> None:
    store = _store(tmp_path)
    (store.knowledge_path / "SECURITY_MODEL.md").unlink()

    gone = REAL_SHAPE[3] + _overhead(MEMORY_FILES["SECURITY_MODEL.md"])
    assert store.memory_total_characters() == TOTAL_WITH_HEADERS - gone


# --- 沒有任何呼叫端可以自己決定要切 ------------------------------------------


def test_build_memory_context_takes_no_truncating_argument(tmp_path: Path) -> None:
    """留一個 `max_chars` 參數，就是留一條「呼叫端自己決定要不要切」的路。

    四個舊呼叫端各自寫死 12,000／20,000／40,000／50,000，沒有一個說得出為什麼
    是那個數字，而它們決定的是安全規則被切在第幾個字。
    """
    store = _store(tmp_path, sizes=(200,) * 8)

    with pytest.raises(TypeError):
        store.build_memory_context(max_chars=100)  # type: ignore[call-arg]
