"""安全規則與「不准自己決定的清單」必須完整抵達，不准被任何機制截斷。

判準：**一條規則失效的時候，有沒有東西會變紅？會的叫機制，不會的叫叮嚀。**

在這之前這件事只有叮嚀。`tools/memory_budget.py` 的通過條件是「只要有任何一個
模式拿得到全部就算 OK」，而拿得到全部的是 `strict`（深入）——**出廠預設是
`minimal`（省額度）**。所以它永遠是綠燈，而預設模式下實際送出去的是：

    ### Security Model（節錄 750／1,675 字）
    …cookie、CLI 認證目錄）、執行期狀態（DB、鎖、快取、暫

「暫存」兩個字被切成兩半。`OPEN_QUESTIONS.md` 同樣斷在「Q10 **金鑰**放行程記憶體
還是直接餵給 CLI 的標準輸入？要不要先有花費上」。**清單型的硬界線少一項就等於沒有那一項**，
而少掉的那一項在畫面上看不出來——被截斷的安全規則讀起來跟完整的一模一樣。

這一組是那個會變紅的東西。它打的是**真的組裝函式**，而且比對的是**硬碟上的
原文**，所以任何一層加回來的截斷都會被抓到，不管它加在哪裡：`ContextProfile`
的欄位、`build_memory_context` 裡的配額、`build_context` 裡的切片、或是某個
呼叫端自己傳進來的字數。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.project_knowledge import (
    CONTEXT_PROFILES,
    MEMORY_FILES,
    MEMORY_MUST_ARRIVE_WHOLE,
    MemoryTruncatedError,
    ProjectKnowledgeStore,
)
from app.services.ai_settings import AISettingsStore

# 真實內容的形狀：規則是一條一條的，而**最後一條跟第一條一樣硬**。
SECURITY_RULES = [
    "祕密、token、OAuth、CLI 憑證：不進 Git、不進匯出。",
    "客戶識別資訊：匯出前一律遮蔽。",
    "逐字紀錄不外流：本機搜尋只讀遮蔽片段。",
    "不准對外發佈：不推 GitHub、不寄訊息、不上傳。",
    "付費 API 要使用者拍板，不准自己開通。",
]
OPEN_QUESTIONS = [
    "Q7 記憶檔誰有權改寫？AI 不准自己動。",
    "Q8 預設權限模式要不要改成會停下來問？",
    "Q9 雲端同步的範圍？不准自己決定。",
    "Q10 金鑰放行程記憶體還是直接餵給 CLI 的標準輸入？",
]


def _long(seed: str, size: int) -> str:
    """把一段內容灌到 `size` 字，**結尾保留原本的最後一句**。

    灌水在中間而不是尾巴，是因為所有的截斷都從尾巴開始吃：最後一句還在，
    等於整份都在。
    """
    tail = seed.strip().splitlines()[-1]
    padding = max(0, size - len(seed) - len(tail))
    return f"{seed}\n{'補' * padding}\n{tail}"


def _store(root: Path, size: int = 12_000) -> ProjectKnowledgeStore:
    """一個八份記憶都很長的專案——長到舊的三個上限全都塞不下。"""
    store = ProjectKnowledgeStore(root / "proj")
    for file_name in MEMORY_FILES:
        if file_name == "SECURITY_MODEL.md":
            body = "\n".join(f"- {rule}" for rule in SECURITY_RULES)
        elif file_name == "OPEN_QUESTIONS.md":
            body = "\n".join(f"- {item}" for item in OPEN_QUESTIONS)
        else:
            body = f"{file_name} 的內容。\n最後一行：{file_name}"
        (store.knowledge_path / file_name).write_text(
            _long(body, size), encoding="utf-8"
        )
    return store


# --- 這一組存在的理由 --------------------------------------------------------


@pytest.mark.parametrize("file_name", MEMORY_MUST_ARRIVE_WHOLE)
@pytest.mark.parametrize("profile", tuple(CONTEXT_PROFILES))
def test_the_safety_rules_arrive_verbatim_under_every_profile(
    tmp_path: Path, profile: str, file_name: str
) -> None:
    """三個設定 × 兩份不准截斷的檔案，逐字比對硬碟原文。

    只測一個設定不夠：漏掉的那一格正是會出事的那一格，而會出事的那一格
    （`minimal`）恰好就是出廠預設。
    """
    store = _store(tmp_path)
    on_disk = (store.knowledge_path / file_name).read_text(encoding="utf-8").strip()

    context = store.build_context("任何任務", context_profile=profile)

    assert on_disk in context, f"{profile}：{file_name} 沒有逐字抵達"


@pytest.mark.parametrize("rule", SECURITY_RULES + OPEN_QUESTIONS)
def test_no_rule_in_the_list_goes_missing(tmp_path: Path, rule: str) -> None:
    """清單型的硬界線**少一項就等於失效**，而少的那一項看不出來。

    一條一條釘，不是釘「這份檔案有出現」：舊行為下 `SECURITY_MODEL.md` 也
    「有出現」——出現的是前 750 個字。
    """
    store = _store(tmp_path)

    # 出廠預設，不是最寬鬆的那個設定。
    context = store.build_context(
        "任何任務",
        context_profile=AISettingsStore.profile("省額度").context_profile,
    )

    assert rule in context


def test_the_shipped_default_is_the_one_being_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前提檢查：出廠預設真的是 `minimal`。

    沒有這條，上面那條可以因為預設哪天變成 `strict` 而**通過得毫無意義**
    ——那正是舊的 `memory_budget.py` 出事的方式：它量的是沒人在用的那個模式。

    `APPDATA` 指到 tmp，量的才是**出廠**預設。讀真的那份全域設定會讓這條的
    結果跟著跑測試的人當下選了什麼跑，那種測試紅起來沒有意義、綠起來也沒有。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    assert AISettingsStore.profile("省額度").context_profile == "minimal"
    assert AISettingsStore().load_global().context_profile == "minimal"


def test_a_truncated_safety_rule_raises_instead_of_being_sent(tmp_path: Path) -> None:
    """截斷不是回一個旗標，是炸。

    一個被截斷的安全規則跟完整的長得一模一樣，所以「回傳一個沒有人檢查的
    旗標」等於沒有。這條釘的是 `_assert_safety_memory_intact` 真的有牙齒：
    餵它一段少了東西的脈絡，它必須丟 `MemoryTruncatedError`。
    """
    store = _store(tmp_path)
    whole = store.build_memory_context()

    with pytest.raises(MemoryTruncatedError) as caught:
        store._assert_safety_memory_intact(whole[: len(whole) // 2])

    # 錯誤訊息要講得出是哪一份，不然收到的人不知道要去看什麼。
    assert any(name in str(caught.value) for name in MEMORY_MUST_ARRIVE_WHOLE)


def test_the_check_reads_the_disk_not_the_string_it_was_given(tmp_path: Path) -> None:
    """檢查端不准拿組裝端剛才算出來的東西當答案。

    兩邊讀同一顆硬碟、走兩條路，才擋得住「組裝時就已經切掉了」的情況——
    拿被切過的結果去跟被切過的結果比，永遠會相等。
    """
    store = _store(tmp_path)

    # 一段完全不含安全規則的脈絡：如果檢查是自我比對，這裡不會有事。
    with pytest.raises(MemoryTruncatedError):
        store._assert_safety_memory_intact("## Shared Project Memory\n（空）")


def test_a_project_without_these_files_is_not_a_false_alarm(tmp_path: Path) -> None:
    """檔案不存在不是「被截斷」。

    分不清這兩件事的檢查會在乾淨的新專案上紅，而一條會亂紅的檢查，
    三天之內就會被人加上略過。
    """
    store = ProjectKnowledgeStore(tmp_path / "空專案")
    store.knowledge_path.mkdir(parents=True, exist_ok=True)

    assert store.build_memory_context() == ""
    assert store.build_context("任務") == ""


def test_the_must_arrive_list_actually_names_the_two_that_matter() -> None:
    """清單本身要是對的——它決定了上面每一條測試在守什麼。"""
    assert set(MEMORY_MUST_ARRIVE_WHOLE) == {
        "SECURITY_MODEL.md",
        "OPEN_QUESTIONS.md",
    }
    for file_name in MEMORY_MUST_ARRIVE_WHOLE:
        assert file_name in MEMORY_FILES
