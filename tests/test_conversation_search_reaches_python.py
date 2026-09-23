"""搜尋對話的字要真的送到 Python（ROADMAP「目前最大風險」）。

ROADMAP 自己寫著：**最大的風險是使用者能不能快速找到並配對正確的對話**，
找不到的話 AICC 就只是一個複雜的專案匯入器。

而那個搜尋框在這之前是假的。它只改瀏覽器裡的 `DATA.query`、自己用**標題和
專案路徑**過濾一次，`act()` 一次都沒有呼叫——所以：

* Python 收到的 query 永遠是空字串（`collect_state` 甚至寫死 `"query": ""`）。
* `ConversationSearchService.search()` 的 `query` 參數從來沒有被傳過。
* 而 `score_session()` 會比對**最多 80 則訊息的內容**——那整條路沒有人走。

**使用者記得一段對話的方式，通常是裡面談了什麼，不是它那個自動產生的標題。**
實測這個專案：搜「開戶」，內容比對找得到 1 則，標題比對 0 則。
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.conversation_search import ConversationHit  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402
from tests.qt_helpers import wait_until  # noqa: E402

PAGE = Path("app/ui/web/lineup_v1.html")


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    ProjectRegistry.initialize_project(project)
    made = LineupWebPanel(project)
    yield made
    # **刪完要當場把它刪掉。**只呼叫 `deleteLater()` 的話，那個延後刪除會
    # 留在事件佇列裡，等到**下一條測試**在 `_settle()` 裡呼叫
    # `processEvents()` 的時候才執行——也就是在另一個面板掃描到一半的時候，
    # 拆掉一個 QWebEngineView。
    #
    # 那正是 N9 那個偶發硬當的形狀（2026-08-24 量到單獨跑這個檔案十次會當
    # 一次）。**這一段是不是根因還沒有證明**，但一個把延後刪除留給下一條
    # 測試的 fixture，不管根因是什麼都是錯的。
    made.deleteLater()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def _settle(panel, timeout_ms: int = 5000) -> None:
    """等背景那條掃描真的跑完。

    掃描 2026-08-22 搬到背景執行緒——在那之前它在**畫面的執行緒**上讀完使用者
    全部的本機對話（這台機器 28 個檔案，最大 50 MB），讀完之前整個視窗是凍的。

    這一份守的東西沒有變：**搜尋的字必須真的送到 `ConversationSearchService`**。
    變的只是它現在非同步到達，所以要等。

    **等到它真的結束，不是等固定秒數。**等秒數的測試會在慢一點的機器上偶爾
    變紅，而那種紅燈讀起來跟真的壞掉一模一樣——這個專案為那種假紅燈付過錢。
    """
    # **不要自己寫 `while ...: processEvents()`。**那個緊迴圈就是 N9——
    # 2026-08-24 量到它會讓整個行程當掉（290 次裡 8 次），而死法是
    # `abort()`／`Segmentation fault`：沒有 traceback、沒有 Qt 訊息。
    #
    # 原因寫在 `qt_helpers.wait_until` 的說明裡：不 sleep 就不放開 GIL，
    # 背景執行緒拿不到任何 Python 執行時間，而主執行緒在那個迴圈裡不斷
    # 重入 Qt 的事件派送。
    wait_until(
        lambda: not panel._scanning,
        timeout_ms=timeout_ms,
        what="背景掃描結束",
    )


def _spy(panel, monkeypatch) -> list[dict]:
    """Record what the panel asks the search service for."""
    asked: list[dict] = []

    def fake_search(project_path, provider="all", query="", limit=100, **kw):
        asked.append({"provider": provider, "query": query})
        return [
            ConversationHit(
                provider=provider,
                title=f"{provider} 的對話",
                display_title=f"{provider} 的對話",
                session_id=f"{provider}-1",
                updated_at="2026-08-10",
                project_path=str(project_path),
                source="test",
            )
        ]

    monkeypatch.setattr(panel.service, "search", fake_search)
    return asked


# --- 字有沒有送到 -----------------------------------------------------------


def test_the_query_reaches_the_search_service(panel, monkeypatch) -> None:
    asked = _spy(panel, monkeypatch)

    panel.handle_action("set_query", "開戶")
    _settle(panel)

    assert asked, "搜尋完全沒有被呼叫"
    assert {item["query"] for item in asked} == {"開戶"}, asked


def test_both_providers_get_the_same_query(panel, monkeypatch) -> None:
    asked = _spy(panel, monkeypatch)

    panel.handle_action("set_query", "開戶")
    _settle(panel)

    assert {item["provider"] for item in asked} == {"claude", "codex"}, asked


def test_the_query_is_pushed_back_so_the_box_keeps_it(panel, monkeypatch) -> None:
    """重畫之後輸入框不能被清空。

    `collect_state` 原本寫死 `"query": ""`——而那個常數正好掩蓋了「Python
    從來不知道使用者搜了什麼」這件事。

    2026-09-01 這一頁變成**兩個框**（老闆：「兩個框」），所以推回去的是
    `queries`，一欄一個。
    """
    _spy(panel, monkeypatch)

    panel.handle_action("set_query", "開戶")
    _settle(panel)

    assert panel.collect_state()["queries"] == {
        "claude": "開戶",
        "codex": "開戶",
    }, "兩欄共用的那個動作沒有把字推回畫面 —— 掃描跑完框就空了"


def test_searching_the_same_thing_twice_does_not_rescan(panel, monkeypatch) -> None:
    # 一次搜尋量到約 0.7 秒，而重打一樣的字畫面不會有任何不同。
    asked = _spy(panel, monkeypatch)
    panel.handle_action("set_query", "開戶")
    _settle(panel)
    before = len(asked)
    assert before, "第一次就沒問到，下面那條會變成恆真"

    panel.handle_action("set_query", "開戶")
    _settle(panel)

    assert len(asked) == before, asked


def test_clearing_the_box_searches_again(panel, monkeypatch) -> None:
    # 反方向：修法不可以變成「只認第一次」。
    asked = _spy(panel, monkeypatch)
    panel.handle_action("set_query", "開戶")
    _settle(panel)

    panel.handle_action("set_query", "")
    _settle(panel)

    assert asked[-1]["query"] == "", asked


# --- 頁面那一半 --------------------------------------------------------------


def _page_code() -> str:
    source = PAGE.read_text(encoding="utf-8")
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


def test_the_box_actually_calls_back_to_python() -> None:
    code = _page_code()

    # 2026-09-01 這一頁變成**兩個框**（老闆：「兩個框」），所以送的是帶著
    # 「哪一欄」的那個動作。`set_query`（兩欄同一個字）還收得下，
    # 但畫面不再用它——**這裡認的是畫面真的在送的那一個**。
    assert "set_provider_query" in code, (
        "搜尋框還是只改自己的 DATA.queries，沒有送出去"
    )
    assert re.search(r"act\(\s*'set_provider_query'", code), (
        "沒有透過 act() 送 —— 只改瀏覽器裡的那份狀態的話，Python 收到的字"
        "永遠是空的，而 `score_session()` 那條比對到 80 則訊息內容的路"
        "整條沒有人走"
    )


def test_the_box_is_debounced() -> None:
    """每打一個字母搜一次會塞住——量到一次約 0.7 秒。"""
    code = _page_code()

    assert "setTimeout" in code, "沒有 debounce，逐字搜尋會卡住畫面"


def test_the_page_does_not_re_filter_what_python_already_filtered() -> None:
    """接上之後最容易犯的錯：兩邊都篩。

    Python 比對標題、專案路徑**和內容**；頁面如果再用標題篩一次，
    就會把靠內容命中的那幾則藏起來——**等於把剛接上的能力立刻關掉**。
    """
    code = _page_code()
    line = next(
        (l for l in code.splitlines() if "const shown=" in l), ""
    )

    assert line, "找不到列表過濾那一行，這條的切法要跟著改"
    assert "!bridge" in line, (
        "頁面仍然在本地過濾標題，會蓋掉 Python 的內容比對結果"
    )


def test_the_page_says_it_is_still_looking_instead_of_saying_nothing_found() -> None:
    """「還沒找完」與「找不到」不可以長得一模一樣。

    掃描 2026-08-22 搬到背景之後，清單有一段時間是空的。那段時間如果畫的是
    「沒有符合的對話」，使用者會直接放棄——而事實是它還在讀他 50 MB 的
    對話檔。

    既有的「沒有人讀的欄位」那條檢查擋不到這件事：它只問這個欄位有沒有被讀，
    不問畫面上分不分得出來。少了這一條，把「正在找」那一支拿掉，整套照樣全綠
    ——2026-08-22 的負向驗證實測過。
    """
    code = PAGE.read_text(encoding="utf-8")

    assert "正在找本機對話" in code, "掃描中的說法不見了"
    assert "沒有符合的對話" in code, "找不到的說法不見了"

    # 兩句話都在還不夠——它們必須真的**由 scanning 分岔**。各自寫死在不同
    # 地方的話，畫面永遠只會顯示其中一句。
    empty_branch = code[code.index("正在找本機對話") - 400 : code.index("正在找本機對話")]
    assert "scanning" in empty_branch, (
        "「正在找」不是由 scanning 決定的——那代表它要嘛永遠顯示、要嘛永遠不顯示"
    )


# --- 兩個框，一欄一個字 -----------------------------------------------------


def test_each_column_gets_its_own_query(panel, monkeypatch) -> None:
    """老闆 2026-09-01：「兩個框，我不是貼給你示意圖了」。

    他貼的示意圖本來就是兩欄各一塊，而他要找一段對話的時候是先想
    「那是我跟 Claude 還是跟 Codex 談的」。

    共用一個字的話，他在左邊打字**右邊也跟著少**，而畫面上看不出是誰濾的
    ——引擎那一側 `find_projects` 的註解早就寫過同一句話。
    """
    asked = _spy(panel, monkeypatch)

    panel.handle_action("set_provider_query", "claude:開戶")
    _settle(panel)

    got = {item["provider"]: item["query"] for item in asked}
    assert got.get("claude") == "開戶", asked
    assert got.get("codex") == "", (
        f"在 Claude 那個框打字，Codex 那一欄也被同一個字濾了：{asked}"
    )


def test_the_two_queries_are_kept_apart(panel, monkeypatch) -> None:
    """兩欄各打各的，兩個字要同時活著。"""
    asked = _spy(panel, monkeypatch)

    panel.handle_action("set_provider_query", "claude:開戶")
    _settle(panel)
    asked.clear()
    panel.handle_action("set_provider_query", "codex:合約")
    _settle(panel)

    got = {item["provider"]: item["query"] for item in asked}
    assert got == {"claude": "開戶", "codex": "合約"}, (
        f"第二個框把第一個框的字蓋掉了：{got}"
    )


def test_the_page_is_told_both_queries(panel, monkeypatch) -> None:
    """字**由 Python 存**：重畫之後框裡的字要填得回去。

    不然掃描一跑完（約 0.7 秒）框就空了，而他還在看那些結果。
    """
    _spy(panel, monkeypatch)
    panel.handle_action("set_provider_query", "claude:開戶")
    _settle(panel)

    state = panel.collect_state()
    assert state["queries"] == {"claude": "開戶", "codex": ""}, state.get("queries")
    assert "query" not in state, (
        "還在推那個「兩欄共用一個字」的舊欄位 —— 這一頁沒有那種東西，"
        "而一個沒人讀的欄位跟一個忘了接的欄位在推值上長得一模一樣"
    )


def test_an_unknown_vendor_is_refused(panel, monkeypatch) -> None:
    """認不得的廠牌一律擋，**不准掉進 else**。

    一個引擎不認得的值送回來沒有人接，然後什麼都沒發生，
    而畫面上那個框看起來已經在搜了。
    """
    asked = _spy(panel, monkeypatch)
    panel.handle_action("set_provider_query", "gemini:開戶")

    assert not asked, f"認不得的廠牌竟然觸發了一次搜尋：{asked}"
    assert panel._queries == {"claude": "", "codex": ""}, panel._queries
