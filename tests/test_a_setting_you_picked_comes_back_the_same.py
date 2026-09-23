"""你在設定頁按下去的那一格，下一秒推回來的一定是你按的那一個。

使用者看到的那個壞掉的樣子，是這一頁得過三次的病：

* 「Google Drive 同步」按下去亮起來，檔案裡什麼都沒變，下一次任何一次重畫
  它**自己彈回去**——`_apply` 那條 elif 鏈一路走到 `else: return`。
* 「LINE 通知」一模一樣，而它旁邊還印著「已設定」。
* 「權限模式」更糟：三個選項**全部存成全自動**（查表用的鍵跟頁面送的字對不上，
  吃了 `.get()` 的預設值），而同一頁上印著「客戶或 KYC 專案請維持『每次問』」。
  他在客戶專案上選了「每次問」，拿到的是全自動。

三個的共同形狀是**同一個**：頁面畫得出一個控制項，而引擎那一側沒有它的分支
（或有分支但查表落空）。落到 `else` 的那一下在畫面上跟「按了、存好了」**長得
一模一樣**——樂觀更新讓格子當場就變了，而下一次推狀態才把真相推回來，
那時候他已經在看別的東西了。

頁面自己其實有偵測這件事的機制（`checkEcho()` 比對「我剛送出的」與「推回來的」），
但它只寫進 console，而 App 的 `QWebEnginePage` 沒有覆寫
`javaScriptConsoleMessage`——實測 PySide6 6.11.1 下那句話連 stderr 都到不了。
**一個沒有人接的訊號，跟一個沒有發的訊號，在畫面上一模一樣。**

所以這一份不靠 console：它問頁面「你畫得出哪些控制項」，然後一格一格送進
`SettingsWebPanel.handle_action()`，再問 `collect_state()` 推回來的是什麼。

## 這條檢查擋不住什麼

* 擋不住「存進去的值有沒有真的改變程式的行為」。`permission` 存成
  `acceptEdits` 而 CLI 收到別的東西，這一份看不出來——那是
  `test_permission_mode_means_what_it_says.py` 在守的。
* 擋不住那一列**該長什麼樣子**。`NEVER_PUSHED` 只問「Python 有沒有推」，
  不問推出去的那一列排版對不對——那是 `tools/verify_web_pages.py` 的事。
* 擋不住 console 那條管道本身還是斷的。接它要改 `app/ui/web_panel.py`
  （`javaScriptConsoleMessage`），見 `docs/WHY.md` 同名那一則。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.cloud_sync import CloudSyncConfigStore  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.settings_web_panel import SettingsWebPanel  # noqa: E402

PAGE = Path(__file__).resolve().parents[1] / "app" / "ui" / "web" / "settings_v1.html"

# 頁面把一個變更送出來的六種寫法。六種都要收，因為**漏掉哪一種，那一種底下的
# 控制項就是這條檢查看不到的**——而看不到的那幾格正是會掉進 `else` 的那幾格。
#
#   set('claude_model',this.value)     下拉選單直接叫
#   seg('effort',[...])                分段按鈕（自己產 data-set）
#   sw('drive_sync')                   開關（自己產 data-toggle）
#   data-set="concurrency"             手寫的 data-set
#   data-toggle="…"                    手寫的 data-toggle
#   on_action('set:gate_on', …)        陣列那一格不走 set()，自己送
#
# `data-set="${key}"` 這種模板寫法**故意比不到**（`[a-z_]+` 排除掉 `$`）：
# 它的鍵在上面那一層的 `seg(...)`／`sw(...)` 已經收過了，兩邊都收會多出一個
# 叫 `key` 的假控制項。
SENDING_SHAPES = (
    r"\bset\('([a-z_]+)'",
    r"\bseg\('([a-z_]+)'",
    r"\bsw\('([a-z_]+)'",
    r'data-set="([a-z_]+)"',
    r'data-toggle="([a-z_]+)"',
    r"on_action\('set:([a-z_]+)'",
)


def _keys_the_page_can_send() -> set[str]:
    text = PAGE.read_text(encoding="utf-8")
    found: set[str] = set()
    for pattern in SENDING_SHAPES:
        found |= set(re.findall(pattern, text))
    return found


# 這一格送什麼過去，推回來應該變成什麼。
#
# 每一格挑的都是**跟出廠值不一樣**的值，而且底下有一條前提檢查在釘這件事：
# 送一個本來就是那個值的東西過去，`else: return` 也會「通過」——那是這條檢查
# 最容易變成假綠燈的方式。
PICKED = {
    "claude_model": ("claude-haiku-4-5", "claude-haiku-4-5"),
    "codex_model": ("gpt-5", "gpt-5"),
    "effort": ("low", "low"),
    "planning_rounds": ("3", 3),
    # 2026-08-27 加的。這兩個回合數各自只有一條序列讀得到，設定頁靠
    # `workflow_mode` 挑要畫哪一列——但**兩列都要收得下**，
    # 因為他隨時可以把序列切過去。
    "max_fix_attempts": ("2", 2),
    # 頁面的字是 low／medium／high，存進去的是 concise／standard／detailed。
    # 推回來的必須是頁面的字，不然那三格一個都不會亮。
    "verbosity": ("high", "high"),
    # 同上：頁面送 ask，存進去是 default。這一格錯過一次，而錯的方向是
    # 「三個都存成全自動」。
    "permission": ("ask", "ask"),
    "concurrency": ("4", 4),
    "profile": ("client", "client"),
    "long_task": ("false", False),
    "drive_sync": ("true", True),
    "gate_on": ("delete,security", ["delete", "security"]),
}

# 頁面畫得出來、**而 Python 一次都沒推過**的那幾格，以及為什麼。
#
# 這是另一種病，不是上面那一種。沒推的鍵在 `rowIf` 的 `has(key)` 是 false，
# 那一列**整列不出現**——所以他看到的不是「按了沒事」，是**「這個設定不存在」**。
# 「05 — DONE　怎麼算做完了」這一段今天在畫面上是空的（`section()` 沒有列就
# 連標題一起藏），而 `DECISIONS` 說「兩個都空的專案不准開始跑」——他會被擋，
# 而擋他的那個設定他在設定頁上找不到。
#
# 頁面自己有偵測這件事：`rowIf` 會 `MISSING.push(key)`，收尾 `console.warn`
# 一整串。但 App 的 `QWebEnginePage` 沒有覆寫 `javaScriptConsoleMessage`，
# 實測 PySide6 6.11.1 下那句話連 stderr 都到不了，也沒有 remote debugging
# port 可以開 DevTools。**一個沒有人接的訊號，跟一個沒有發的訊號，在畫面上
# 一模一樣**——所以那份名單改放在這裡：它在 console 裡沒有人讀，在這裡少一個
# 多一個都會有人被叫醒。
#
# 每一列都要寫理由。沒有理由的白名單會愈長愈長，而長到某一天就沒有人記得
# 「這一列當初為什麼可以不接」。
NEVER_PUSHED = {
    "verify_command": (
        "「跑一個指令」。引擎讀得到它（`project_verification.read_verify_command`），"
        "但那是**唯讀**的：整包程式沒有任何一支寫 `.aicc/project.json` 的 "
        "`verify_command`。要接上去得先決定「一行指令怎麼變成 argv」——"
        "引擎收的是已經切好的參數陣列，不是一條 shell 指令。"
    ),
    "verify_success_rule": (
        "「跑完什麼樣子算過了」。同一個檔、同一個問題，而且多一條："
        "**「引擎不准內建判準」是拍過板的**，所以這一格不能給預設值，"
        "也不能在使用者填錯的時候安靜地倒向一個。"
    ),
    "verify_timeout": (
        "「最多等它多久」。頁面收的是分鐘，檔案裡是 `verify_timeout_seconds`，"
        "而「逾時不准發明預設值」同樣是拍過板的。"
    ),
}


def _panel(tmp_path: Path) -> SettingsWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir(parents=True)
    ProjectRegistry.initialize_project(project)
    # Drive 同步那一格沒有選過資料夾就打不開（`_set_drive_sync` 會拒絕並說原因），
    # 而那是對的行為——所以這裡先給它一個資料夾，讓這條檢查驗的是「開得起來的
    # 時候有沒有真的存進去」，不是那個拒絕。
    store = CloudSyncConfigStore()
    settings = store.load()
    settings.root_path = str(tmp_path / "drive")
    settings.enabled = False
    store.save(settings)
    panel = SettingsWebPanel(project)
    panel._page_ready = True
    return panel


def test_every_control_the_page_can_draw_is_listed_here() -> None:
    """頁面新增一個控制項，這一份沒跟上就要紅。

    沒有這一條，`PICKED` 會慢慢變成一份**只涵蓋當初那幾格**的清單，而新加的
    那一格——正是還沒有人替它寫 `_apply` 分支的那一格——不會被任何人問到。
    `drive_sync` 與 `line_notify` 就是這樣進來的。

    改壞的樣子：在 `settings_v1.html` 加一個 `sw('新開關')`，這條會紅並指名
    那個鍵；而**不加這條的話，那顆新開關按下去什麼都不會發生，畫面上看不出來**。
    """
    on_screen = _keys_the_page_can_send()

    assert on_screen, (
        f"從 {PAGE.name} 一個控制項都沒有比到——比對規則自己壞了，"
        "而它壞掉的樣子是這一整份檔案恆真。"
    )
    stale = set(NEVER_PUSHED) - on_screen
    assert not stale, (
        f"`NEVER_PUSHED` 還留著頁面上已經沒有的控制項：{sorted(stale)}\n"
        "那一列不見了（或改名了），而這裡還替它留著一個豁免——"
        "**一個沒有對應物的豁免，會在下一次有人剛好用同一個名字時無聲生效**。"
    )
    assert on_screen - set(NEVER_PUSHED) == set(PICKED), (
        "設定頁畫得出來的控制項跟這一份對不上：\n"
        f"  頁面有、這裡沒問的：{sorted(on_screen - set(NEVER_PUSHED) - set(PICKED))}\n"
        f"  這裡問了、頁面沒有的：{sorted(set(PICKED) - on_screen)}\n"
        "頁面多一格就要在 `PICKED` 加一列（並確認 `_apply` 真的有它的分支）；"
        "如果那一格今天還接不上去，寫進 `NEVER_PUSHED` 並說出理由——"
        "**不准兩邊都不寫**，那正是 `drive_sync` 當初進來的方式。"
    )


def test_a_row_the_page_can_draw_but_python_never_pushes_is_named_here(
    tmp_path: Path,
) -> None:
    """頁面畫得出來、Python 沒推的那幾格，名單在這裡對得起來。

    使用者看到的那個壞掉的樣子跟上面那幾條**不一樣**：不是「按了沒事」，
    是**那一列整列不出現**，於是他以為那個設定不存在。今天真的在發生——
    「05 — DONE　怎麼算做完了」那一段一列都沒有推，`section()` 沒有列就連
    標題一起藏，所以他在設定頁上找不到「怎麼算做完了」；而引擎那一側說
    「兩個都空的專案不准開始跑」，他會被擋在一個他找不到的設定上。

    頁面自己有偵測（`rowIf` 的 `MISSING` → `console.warn`），但那句話在這支
    App 裡到不了任何地方（沒有覆寫 `javaScriptConsoleMessage`、沒有 remote
    debugging port）。這一條就是把那份名單從死掉的 console 搬到會叫人的地方。

    改壞的樣子：在 `collect_state()` 推一個 `verify_command` 出去而 `_apply`
    沒有它的分支——那一列會開始顯示，而按下去什麼都不會存。

    ## 這條擋不住什麼

    擋不住「那一格接上去之後存得對不對」。接上去它就會離開 `NEVER_PUSHED`
    進到 `PICKED`，由上面那條 round-trip 接手。
    """
    on_screen = _keys_the_page_can_send()
    pushed = set(_panel(tmp_path).collect_state())

    invisible = sorted(key for key in on_screen if key not in pushed)

    assert invisible == sorted(NEVER_PUSHED), (
        "設定頁畫得出來、而 Python 一次都沒推過的控制項，跟 `NEVER_PUSHED` "
        "對不上：\n"
        f"  沒推、也沒有記在這裡的：{sorted(set(invisible) - set(NEVER_PUSHED))}\n"
        f"  記在這裡、其實已經推了的：{sorted(set(NEVER_PUSHED) - set(invisible))}\n"
        "沒推的那一列在畫面上整列不出現——使用者讀到的是「這個設定不存在」，"
        "而頁面唯一的抗議（`MISSING` 的 console.warn）在這支 App 裡沒有人收得到。\n"
        "已經推了的請移去 `PICKED`；還接不上去的請寫進 `NEVER_PUSHED` 並說明理由。"
    )


@pytest.mark.parametrize("key", sorted(PICKED))
def test_a_setting_you_picked_comes_back_the_same(key: str, tmp_path: Path) -> None:
    """按下去的那一格，推回來的是你按的那一個——不是彈回舊值。

    改壞的樣子：把 `_apply` 裡任何一格的 `elif` 拿掉，它就會落到最後那個
    `else: return`。畫面上：那一格亮起來、什麼都沒存、下一次重畫自己彈回去。
    """
    panel = _panel(tmp_path)
    sent, expected = PICKED[key]

    before = panel.collect_state()[key]
    assert before != expected, (
        f"`{key}` 出廠就已經是 {expected!r} 了，所以送一個一樣的值過去，"
        "就算引擎那一側完全沒有這一格的分支也會「通過」。\n"
        "請在 `PICKED` 換一個跟出廠值不同的選項——**這條前提檢查就是這份"
        "檔案不會變成假綠燈的那一半**。"
    )

    panel.handle_action(f"set:{key}", sent)
    after = panel.collect_state()[key]

    if isinstance(expected, list):
        assert sorted(after) == sorted(expected), (
            f"`{key}`：送出 {sent!r}，推回來的是 {after!r}"
        )
        return
    assert after == expected, (
        f"`{key}`：送出 {sent!r}，推回來的卻是 {after!r}（原本是 {before!r}）。\n"
        "使用者看到的是那一格自己彈回去，或者停在一個他沒有選過的值上。"
    )


# 頁面的字 → 檔案裡真的會被寫成什麼（最後原封不動送進 `--permission-mode`）。
PERMISSION_ON_DISK = {
    "ask": "default",
    "edits": "acceptEdits",
    "auto": "auto",
}


@pytest.mark.parametrize("choice", sorted(PERMISSION_ON_DISK))
def test_the_permission_you_chose_is_the_one_written_to_disk(
    choice: str, tmp_path: Path
) -> None:
    """權限模式三個選項各問一次，而且要問到**檔案裡那個值**。

    這一格跟其他格不一樣，因為它錯的方向有代價：他在客戶專案上選「每次問」，
    拿到的是全自動，而同一頁上還印著「客戶或 KYC 專案請維持『每次問』」。
    2026-08-24 之前那張對照表兩處都錯，實測結果是**三個選項全部存成全自動**。

    只問「推回來的字一不一樣」不夠——那是這一份最容易長出來的假綠燈：
    `_reverse()` 查不到的時候會回一個預設值 `"ask"`，而 `normalized()` 認不得
    的值也倒向 `"default"`。**兩個預設剛好對得起來**，於是一張整個錯掉的
    對照表在「每次問」這一格上會round-trip 成功。所以這裡多問一句：
    寫進 `.aicc` 的那個字是不是 CLI 收得懂的那一個。

    改壞的樣子：把 `PERMISSION` 改回 `{"ask": "ask", "acceptEdits": "edit", …}`。
    """
    panel = _panel(tmp_path)

    panel.handle_action("set:permission", choice)

    assert panel.collect_state()["permission"] == choice, (
        f"畫面上那三格：選了 {choice!r}，推回來的是 "
        f"{panel.collect_state()['permission']!r}——他會看到自己沒選的那一格亮著"
    )
    on_disk = panel.store.load_project().claude_permission_mode
    assert on_disk == PERMISSION_ON_DISK[choice], (
        f"他選了 {choice!r}，檔案裡寫的卻是 {on_disk!r}"
        f"（應該是 {PERMISSION_ON_DISK[choice]!r}）。\n"
        "這個字會原封不動送進 `--permission-mode`：畫面說「每次問」，"
        "AI 拿到的可能是全自動。"
    )
