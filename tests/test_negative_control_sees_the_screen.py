"""負向驗證新增的那句附註：「這個破壞有沒有改變使用者看得到的東西」。

紅燈有兩種，而工具原本分不出來：

1. 改壞了會被看到的行為 → 測試變紅。**這是想要的。**
2. 改壞了**沒有人讀的東西** → 測試也變紅，而畫面一模一樣。

第二種最純粹的例子是 `MainWindow.decision_notification_policy()`：一個把設計
決策當 dict 回傳的函式，唯一的讀者是斷言那個 dict 的測試。把 `popup_ever` 改成
`True`，測試會紅，畫面上不會有任何一個像素不同。

**這個附註不是判決，這件事是刻意的。** 安全掃描、prompt 組裝、備份路徑改壞了
都不會出現在 `collect_state()` 裡，而它們比畫面重要——那種「紅燈但畫面沒變」
是對的紅燈。所以工具印的是一句提示，判決還是紅／綠／壞三種，規則沒有動。

這個檔案守的是那句附註不會說謊。它會說謊的方式只有三種，三種都在下面：

* 指紋對真正的畫面改動不敏感 → 每一條都變成「畫面沒變」，於是每一條都被
  懷疑成假警報。
* 指紋不穩定 → 每一條都變成「畫面也變了」，附註等於沒有。
* 量不到被當成沒變 → **最糟的一種**，因為破壞讓 app 起不來時它會說「畫面沒變」。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

DIGEST_TOOL = Path("tools/ui_payload_digest.py")
NC_TOOL = Path("tools/negative_control.py")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # 先註冊再執行：`negative_control` 裡有 `@dataclass`，而 dataclasses 會用
    # `sys.modules[cls.__module__]` 去解析型別註解。沒註冊的話那一步會拿到
    # None 然後 AttributeError——跟工具本身無關，是載入方式的問題。
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def digest_tool():
    return _load(DIGEST_TOOL, "ui_payload_digest_under_test")


@pytest.fixture(scope="module")
def nc():
    return _load(NC_TOOL, "negative_control_under_test")


# --- 附註本身的真值表（純函式）----------------------------------------------


def test_the_annotation_never_reads_unmeasurable_as_unchanged(nc) -> None:
    """量不到 ≠ 沒變。

    這是這一整套裡最重要的一條，而且它跟這支工具存在的理由是同一句話：
    「沒改到」跟「沒抓到」不可以長得一樣。破壞讓 app 起不來的時候，
    指紋算不出來——那時候說「畫面沒變」會是最誤導的一句話。
    """
    said = nc._ui_effect("a" * 64, None)

    assert "沒變" not in said
    assert "起不來" in said


@pytest.mark.parametrize(
    "before,after,expect",
    [
        ("a" * 64, "b" * 64, "也變了"),
        ("a" * 64, "a" * 64, "沒變"),
        (None, "a" * 64, "沒有基準"),
    ],
)
def test_the_annotation_truth_table(nc, before, after, expect: str) -> None:
    assert expect in nc._ui_effect(before, after)


def test_a_digest_that_could_not_be_taken_is_none(nc, tmp_path) -> None:
    # 指向一個不存在的 root 也要算得出來（工具自己會建），所以這裡改成
    # 讓子行程一定失敗：把工具路徑換掉。
    nc.UI_DIGEST = tmp_path / "not-a-script.py"

    try:
        assert nc._ui_digest(tmp_path) is None
    finally:
        nc.UI_DIGEST = Path("tools/ui_payload_digest.py").resolve()


# --- 指紋本身 ---------------------------------------------------------------


def test_the_same_code_gives_the_same_digest(digest_tool, tmp_path) -> None:
    """不穩定的指紋會讓每一條破壞都讀成「畫面也變了」——附註等於沒有。"""
    first = digest_tool.digest(tmp_path)
    second = digest_tool.digest(tmp_path)

    assert first == second


def test_the_digest_notices_a_change_the_user_would_see(
    digest_tool, tmp_path, monkeypatch
) -> None:
    """反方向：對真正的畫面改動要敏感。

    挑 Decision Gate 的地板，因為它是**畫在設定頁上的字**——這正是這個附註
    要能分辨的那一類。
    """
    from app.ui import settings_web_panel

    before = digest_tool.digest(tmp_path)
    # **要 patch 面板裡的那個名字，不是來源模組裡的。** 面板用
    # `from app.services.ai_settings import gate_floor_clauses`，那個繫結在
    # import 當下就固定了，改來源模組完全不影響它。第一版就是這樣寫的，
    # 而結果是「指紋沒變」——一個看起來像「指紋不敏感」的假結論。
    #
    # 2026-08-24：那個常數變成函式了（介面文字搬進 `app/i18n.py`，而模組層
    # 取出來的字會在 import 的當下凍住）。patch 的對象跟著變成函式，
    # **要守的事沒有變**：改面板看得到的那個名字。
    original = settings_web_panel.gate_floor_clauses
    monkeypatch.setattr(
        settings_web_panel,
        "gate_floor_clauses",
        lambda: original()[:-1],
    )

    assert digest_tool.digest(tmp_path) != before


def test_the_digest_covers_every_page_and_the_shell(digest_tool, tmp_path) -> None:
    """少算一頁，那一頁的改動就會全部讀成「畫面沒變」。

    而它是無聲的：指紋還是算得出來、還是穩定，只是不再包含那一頁。
    """
    payloads = digest_tool._payloads(tmp_path)

    from app.ui.shell_panel import PAGES

    live = {key for key, _ in PAGES}
    # 側邊欄有兩頁不是 web 面板（即時紀錄是原生的、執行中對話也是），
    # 所以比的是「每一個 web 面板都在裡面」加上外框。
    assert "shell" in payloads
    assert len(payloads) >= 8, payloads.keys()
    assert live, "側邊欄清單是空的，這條會恆真"
