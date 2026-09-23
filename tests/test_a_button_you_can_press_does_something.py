"""按得下去的按鈕，一定要有人接。

使用者看到的那個東西：儀表板右上角一顆**正常樣式**的「全部暫停」——不是灰的、
沒有 disabled、滑上去還會亮。按下去畫面一個像素都不變，佇列繼續遞補、專案繼續跑。
他分不出「我按了、它暫停了，只是沒有視覺回饋」跟「這顆根本沒接」。

而它旁邊那兩顆（全部收合／全部展開）是好的。**四顆長得一模一樣**。

> 「還沒接」是可以的，`N8` 的畫面半邊排在頁面設計那一側是拍過板的。
> **「還沒接」被畫成「按得下去」不可以。**

這一份掃的是**出貨的那一份**（`app/ui/web/`），不是設計資料夾：使用者看到的是
產物，而設計檔裡的死按鈕不經過 `build_pages.py` 到不了他面前——到得了的那一刻
這條就看得到了。`.js` 也要掃：`shell.js` 自己畫十六顆按鈕，只掃 `.html` 的話
那十六顆一顆都沒被問過。

## 什麼叫「有人接」

四種都算，因為畫面上真的有四種：

* `onclick=` —— 自己叫。
* `disabled` —— **明著說「這顆現在不能按」**，那是誠實的，正是這一條要的東西。
* `data-<某某>=` 而那個名字在同一個檔案的 JS 裡出現過 —— 事件委派。
  這幾頁的委派長得像 `e.target.closest('[data-act]')`，所以「名字有沒有被讀」
  就是「這顆有沒有被接」。
* `id=` 而那個 id 在同一個檔案裡出現過 —— `document.getElementById`。

## 這條檢查擋不住什麼

* 擋不住「接到了，但 Python 那一側沒有這個動作的分支」。頁面送出
  `data-act="queue_pause"` 而面板的 `handle_action` 不認得它，這一條是綠的
  ——那一半由 `tests/test_the_queue_controls_reach_the_engine.py` 與
  `tests/test_no_field_is_pushed_into_the_void.py` 兩個方向守。
* 擋不住 `data-` 的名字**只在註解裡**出現過。要真的分辨得出來就得剖 JS，
  而一個剖到一半的 JS 剖析器會用更難發現的方式失準。
* 擋不住不是 `<button>` 的可按物件（`<div class="sw">`、`<span class="slot">`）。
  那些的接法各頁不同，統一的判準會變成一張白名單，而白名單會愈長愈長。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "ui" / "web"
SHIPPED_SUFFIXES = (".html", ".js")

BUTTON = re.compile(r"<button\b([^>]*)>", re.IGNORECASE)
ATTR = re.compile(r"([A-Za-z_:][-A-Za-z0-9_:.]*)\s*(?:=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+))?")


def _shipped() -> list[Path]:
    return sorted(p for p in WEB.iterdir() if p.suffix in SHIPPED_SUFFIXES)


def _buttons(path: Path):
    """(行號, 整個開始標籤, 屬性名字) 一顆一顆吐出來。"""
    text = path.read_text(encoding="utf-8")
    for match in BUTTON.finditer(text):
        names = {a.group(1).lower() for a in ATTR.finditer(match.group(1))}
        yield text[: match.start()].count("\n") + 1, match.group(0), names


def _is_wired(path: Path, names: set[str]) -> bool:
    if "onclick" in names or "disabled" in names:
        return True
    # 按鈕標籤本身要從「JS 有沒有讀這個名字」裡拿掉，否則每一顆都會自己證明
    # 自己被接了——`data-act` 出現在那顆按鈕上，就算沒有任何人 closest() 它。
    script = BUTTON.sub("", path.read_text(encoding="utf-8"))
    for name in names:
        if name == "id" and "getElementById" in script:
            return True
        if name.startswith("data-") and (name in script or name[5:] in script):
            return True
    return False


def test_the_scan_really_reaches_every_shipped_page() -> None:
    """前提檢查：掃描範圍就是出貨的那幾份，而且真的比到了按鈕。

    沒有這一條，把範圍縮成一頁、或把正規式打錯一個字，底下那條就會變成恆真
    ——而**恆真的檢查跟真的守得住的檢查，在報告上長得一模一樣**。
    """
    shipped = _shipped()
    expected = {
        p.name for p in WEB.iterdir() if p.suffix in SHIPPED_SUFFIXES
    }

    assert {p.name for p in shipped} == expected, (
        f"掃描範圍跟 {WEB} 底下出貨的檔案對不上：\n"
        f"  少掃了：{sorted(expected - {p.name for p in shipped})}"
    )
    counted = {p.name: len(list(_buttons(p))) for p in shipped}
    assert sum(counted.values()) >= 40, (
        f"整包只比到 {sum(counted.values())} 顆按鈕（{counted}）——"
        "這幾頁遠不止這個數，比較可能是正規式壞了，不是按鈕變少了。"
    )
    for path in shipped:
        if "<button" in path.read_text(encoding="utf-8").lower():
            assert counted[path.name], (
                f"{path.name} 裡有 `<button` 字樣，卻一顆都沒有被比到——"
                "正規式看不懂那一頁的寫法。"
            )


def test_no_button_is_shipped_with_nobody_listening() -> None:
    """按得下去的按鈕，按下去一定會發生**某件事**。

    改壞的樣子：在任何一頁塞一顆 `<button class="btn">全部暫停</button>`。
    使用者看到的：一顆正常樣式的按鈕，按下去畫面一個像素都不變，然後他會
    再按一次，然後開始懷疑畫面上其他每一個東西有沒有生效。
    """
    dead: list[str] = []
    for path in _shipped():
        for line, tag, names in _buttons(path):
            if not _is_wired(path, names):
                dead.append(f"{path.name}:{line}  {tag.strip()}")

    assert not dead, (
        "這幾顆按鈕按下去不會發生任何事：\n  "
        + "\n  ".join(dead)
        + "\n接上去（`onclick` 或一個 JS 真的在讀的 `data-*`），"
        "或者標上 `disabled` 明說它現在不能按，"
        "或者拿掉——**一顆按不動的按鈕，比沒有那顆按鈕更糟**。"
    )
