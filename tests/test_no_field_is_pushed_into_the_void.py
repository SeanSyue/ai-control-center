"""Python 推給畫面的每一個欄位，畫面都要真的讀（或者列進待接清單）。

這個專案到目前為止找到的真 bug，超過一半是同一個形狀：

> **算出來了、存下來了、推給畫面了，而畫面沒有人讀它。**

沒有人讀不會壞掉，會**看起來很合理**——畫面用它自己那份推論、自己那份寫死的
清單，給出一個在今天的資料下剛好正確的答案。這種東西不會有人回報，因為它
看起來是對的；等它變錯的時候，唯一會發現的方式是有人剛好去查證。

2026-08-11 一天之內撞到兩次，而且第二次那個欄位的旁邊就寫著它為什麼存在：

* `lineup.max_slots` 曾經被拿掉，頁面改用 `sequence.executed_slots` 推座位上限
  ——那個不等式恆真，「＋ 加一格」於是永遠是關的。
* `settings.gate_floor` 一直有推，而設定頁畫的是自己硬寫的四筆。Python 那邊的
  註解寫著「放在這裡就是為了讓頁面不用自己留一份，自己留一份會漂移」，
  而頁面自己留了一份，其中兩行的字面已經跟 prompt 對不上了。

所以這條把整類問題變成會變紅的東西：跑每一個面板的 `collect_state()`，
把推出去的欄位名拿去對那一頁的程式碼（**剝掉註解**——描述一個欄位的散文
跟讀取它長得一樣）。

`KNOWN_UNREAD` 是**待接清單，不是豁免清單**。它兩個方向都會紅：

* 多了一個沒人讀的欄位 → 有人推了東西出去而畫面忽略它。
* 清單上的欄位其實已經被讀了 → 這筆債還完了，清單自己過期了。

第二個方向才是這張清單不會腐爛的原因。一張只會變長的豁免清單，
跟沒有這條檢查是一樣的。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402

WEB = Path("app/ui/web")

# 推出去、畫面還沒接的欄位，一筆一個理由。
#
# **目前是空的，而空是這張清單的正常狀態。** 它上一次有東西是
# ROADMAP P10 的 Stage D／E（每格自訂身分、整組存成模式）：後端做完、
# 欄位在推、畫面元件還沒回來。2026-08-11 接上之後，`test_the_debt_list_
# _has_not_gone_stale` 這八條同時變紅，清單才被清掉——**不是有人記得回來刪，
# 是它自己要求被刪**。那正是這張清單跟一張豁免清單的差別。
KNOWN_UNREAD: dict[str, dict[str, str]] = {
    "shell.js": {
        "shell.run.group":
            "**這一次收場要使用者動哪一樣東西**（五組之一，`app/core/outcomes.py` "
            "算出來的）。顏色只回答「要不要你動」，這一欄回答「動哪一樣」。"
            "推出去了，`shell.js` 一個字都沒讀——**而且它躲過這條守衛很久**："
            "外框同時推三個叫 `group` 的東西，而側邊欄那一個有被讀，"
            "於是收成名字之後它替另外兩個擋掉了。2026-08-24 改成看路徑才露出來。"
            "規格要寫進交接給頁面設計。",
        "shell.activity[].group":
            "同上，執行中橫幅那一列的收場分組。`shell.js` 的 `activityHTML()` "
            "只用 `id`／`name`／`state`／`stage`／`focused`，而那份檔案自己第 34 行的"
            "註解列的也正是那五個——**畫面那一側的合約裡根本沒有 `group`**。",
    },
    "lineup_v1.html": {
        # 框裡的形狀（老闆 2026-08-22：「有分支或沒分支無所謂，應該要是在一個
        # 方框內，有需要才往下增加分支，然後才把方框垂直距離增加放下第二條
        # 線」）。**引擎這一側 2026-08-27 做完了，畫面元件還沒回來**——這一輪
        # 的界線就是「只做引擎那一側的資料與規則，畫面不動」，因為頁面設計
        # 明講「在資料長出來之前，畫面畫什麼都是編的」。
        #
        # **這一批是這張清單的正常用途**：引擎做完、欄位在推、畫面還沒接。
        # 老闆 2026-08-27 的原話：「把選 claude 和 codex 的地方和 **AI 執行
        # 設定的地方**，像是網站一樣和工作線放在一個頁面」。
        #
        # 那 22 個控制項今天長在一個原生 Qt 面板裡，畫面碰不到。這一輪把
        # **引擎那一半**做完；畫面那一段由頁面設計接，接上去的那一輪
        # `test_the_debt_list_has_not_gone_stale` 會要求刪掉這幾行——
        # **不是有人記得回來刪**。
        #
        # 第一版推的是 `values`／`choices`／`spins`／`toggles` 四棵平行子樹，
        # 同一個設定名在三棵裡各出現一次：**80 個葉子有 65 個撞名**，
        # 還害一個畫面真的有讀的欄位（`sequence.label`）被判成孤兒。
        # 改成一格一筆之後剩 14 個，只有 `text` 還撞名——
        # 那不是守衛太嚴，是原本的形狀讓「這個名字指誰」答不出來。
        # **登記的是路徑，不是名字**（既有那幾筆也是）。
        # 2026-08-27 老闆兩件：「我希望正在執行的 node 有呼吸特效」、
        # 「也要能夠讓使用者在該回覆的 node 上面有一個按鍵去回覆」。
        # 引擎這一半做完了，畫面那一段由頁面設計接。
        "lineup.duty_choices":
            "「這一輪」那個下拉的選項（`id` 送回引擎、`text` 畫在畫面上）。"
            "**跟著序列走**：計畫序列裡只有兩項，所以他選不到一個引擎不會派工的東西。"
            "老闆 2026-08-27：「能不能把這一輪 XX 的 XX 用下拉式選單就好?」"
            "接上去的那一輪，「職責」那一列要整個拿掉——**兩列講同一件事**。",
        # `id`／`text` 現在有兩組（這一輪的選項、設定那邊的選項），
        # 所以逐路徑指認——那是這條守衛自己的規矩：
        # 兩個叫同一個名字的東西不是同一件事。
        # 最上面那個框：**搜專案**（老闆 2026-08-27：「我希望做的是搜尋專案，
        # 邏輯上比較接近 AICC、codex、claude 的專案資料夾」）。
        # 在這之前那個框同時搜對話標題**與**專案路徑——一個框回答兩個問題。
        "lineup.duty_choices[].id": "送回引擎的值（`set_slot_duty` 收的那個）。",
        "lineup.duty_choices[].text": "畫在畫面上的字（就是「這一輪：⋯」那一句）。",
        "lineup.run_settings.controls[].options[].id": "這個選項存進設定的值。",
        "lineup.run_settings.controls[].options[].text": "這個選項畫在畫面上的字。",
        "lineup.task.text": "他打的那段字。",

        # 2026-09-01 第二批清掉八筆（`run_settings` 整段、`.summary`／`.expanded`
        # ／`.profiles`／`.controls`＋`[].kind`／`[].options`／`[].suffix`）：
        # AI 執行設定那一段搬進陣容頁了（老闆 2026-08-27：「AI 執行設定那邊
        # 一樣吃到頁面」）。留在清單上的只剩**選項那兩個葉子**
        # （`options[].id` 與 `[].text`）——這條守衛只取陣列的第一筆當樣本，
        # 而那一筆的下拉在指紋那一側被中和掉了，所以它一輩子看不到它們。
        #
        # 2026-09-01 第一批清掉八筆（`running`／`waiting`＋`.question`／`drafts`／
        # `task`／`projects`＋`.typed`＋`.found`）：呼吸特效、回覆三入口、
        # 搜尋專案那一列都接上去了，而 `test_the_debt_list_has_not_gone_stale`
        # 自己要求刪掉它們——**不是有人記得回來刪**。
        # 2026-08-27 那四筆（`layer`／`upstream`／`rows`／`shape_problems`）**已經清掉**：
        # 頁面接上去了，而 `test_the_debt_list_has_not_gone_stale` 自己要求刪掉它們
        # ——**不是有人記得回來刪**。這張清單現在對這一頁沒有欠款。
    },
    "dashboard_v5.html": {
        "queue_paths":
            "N8 佇列那五個控制項。畫面上顯示的是名字，而「把這一列移除」"
            "要送得回一個認得出來的東西——名字會撞。**跟 `queue` 逐格對齊**。"
            "規格在 `AICC-視覺概念/交接給頁面設計-2026-08-24.md` §六，"
            "他們接上去的那一輪這一行要拿掉。",
        "queue_paused":
            "同上。暫停是一個**狀態**不是一個動作：那顆按鈕要顯示「恢復」，"
            "而佇列那幾列要看得出來它們不會自己開始。",
    },
}


# **撞名的欄位，人指認過哪一條路徑真的被讀。**
#
# 只有在同一個名字出現在兩條以上路徑的時候才需要——那時候
# `"group" in code` 這種比對回答不了「讀的是哪一個」。
CONFIRMED_READ: dict[str, dict[str, str]] = {
    "shell.js": {
        "shell.pages[].group": "`p.group && prev.group !== p.group` → 畫分隔線（`shell.js:112`）",
        "shell.pages[].label": "`<span class=\"t\">${p.label}</span>`（`shell.js:115`）",
        "shell.project": "`const p = s.project || {}`（`shell.js:125`）",
        "shell.project.name": "`x.name === p.name ? ' selected' : ''`（`shell.js:131`）與 `p.name`（317）",
        "shell.projects[].id": "`<option value=\"${x.id}\">`（`shell.js:131`）",
        "shell.projects[].name": "同上，`${x.name}` 是選項文字",
        "shell.gate.project": "`${g.project} · 需要你的判斷`（`shell.js:252`）",
        "shell.drive.label": "`<span class=\"drive\">${s.drive.label}</span>`（`shell.js:160`）",
        "shell.run.state": "`const state = run.state || 'idle'`（`shell.js:127`、208）",
        "shell.run.stage": "`run.stage`（`shell.js:211`）",
        "shell.run.label": "`${run.label || RUN_LABEL[state] || state}`（`shell.js:156`）",
        "shell.usage.state": "`s.usage.state`（`shell.js:319`）",
        "shell.activity[].id": "`data-shell-go=\"${a.id}\"`（`shell.js:278`）",
        "shell.activity[].name": "`<b class=\"n\">${a.name}</b>`（`shell.js:279`）",
        "shell.activity[].state": "`class=\"it ${a.state || ''}\"`（`shell.js:277`）",
        "shell.activity[].stage": "`<span class=\"s\">${a.stage || ''}</span>`（`shell.js:280`）",
    },
    "knowledge_v1.html": {
        "files[].name": "`<h4 class=\"mono\">${f.name}</h4>`（`knowledge_v1.html:256`）",
        "context[].name": "`<span class=\"nm\">${c.name}</span>`（333）與 title（318）",
    },
    "settings_v1.html": {
        "gate_options[].label": "`row(o.label, ...)`（`settings_v1.html:231`）",
        "gate_floor[].label": "`fact(f.label, ...)`（`settings_v1.html:233`）",
    },
    "roadmap_v1.html": {
        "phases[].title": "`<h2>${p.title}</h2>`（`roadmap_v1.html:140`）與軌道上的 `<small>`（119）",
        "phases[].items[].title": "`<h4>${it.done?TICK:''}${it.title}</h4>`（`roadmap_v1.html:145`）",
    },
    # 2026-09-01 AI 執行設定搬進陣容頁。這幾條的名字（`label`／`id`／`text`
    # ／`mode`）在這一頁**各有兩三條路徑**，所以字串比對答不出「這個名字
    # 指誰」——那是這條守衛自己的規矩：兩個叫同一個名字的東西不是同一件事。
    # 逐路徑指認在這裡。
    "lineup_v1.html": {
        "sequence.mode": "`const mode = seq.mode || ''`：開發序列給下拉、"
                         "計畫序列鎖定（老闆 2026-08-27 拍板兩者是不同功能）",
        "sequence.label": "工作線那一列的「開發序列／計畫序列」小字",
        "mode": "`html[data-mode]`：日夜。整頁的底色跟著它",
        "lineup.run_settings.controls[].label": "`<span class=\"k\">${esc(c.label)}</span>`"
            "：那一格自己的名字。沒有它，22 格會變成一排原始鍵名",
        "lineup.run_settings.profiles[].id": "`data-rprofile=\"${esc(pf.id)}\"`"
            "：按下去送回引擎的**代號**（不准送那三個中文字）",
        "lineup.run_settings.profiles[].text": "那顆鈕上的字（省額度／標準／深入）",
        # 2026-09-01 搜尋對話改成**兩個框**（老闆：「兩個框，我不是貼給你
        # 示意圖了」）。`claude`／`codex` 這兩個名字從此各有兩條路徑
        # （那一欄的對話清單、那一欄的搜尋字），所以逐路徑指認。
        "claude": "`const all=d[p]||[]`，`p` 是 `['claude',…]` 那一列字面值"
                  "（`paintLists`）：Claude 那一欄的對話清單",
        "codex": "同上，Codex 那一欄的對話清單",
        "queries.claude": "`QS[p]` 決定那一欄有沒有在搜尋、`qs[pv]` 把字填回"
                          "`#convqC`：Claude 那個框裡的字",
        "queries.codex": "同上，Codex 那個框（`#convqX`）",
    },
}


# 外框的讀取端跨兩個檔案：版面與所有動作在 `shell.js`，而 `mode` 是
# `shell.html` 自己的 render() 讀的（它設 documentElement 的 data-mode）。
# 只看其中一個會冤枉一個真的有被讀的欄位——而一條會誤報的守衛，
# 下一個人的處理方式通常是把它關掉。
READERS = {"shell.js": ["shell.js", "shell.html"]}


def _page_code(name: str) -> str:
    """那一頁真正會執行的程式碼。

    註解一定要剝掉：這些頁面的開頭就寫著完整的資料契約，每一個欄位名都在
    上面。拿整份檔案做字串比對的話，**一個從來沒有人讀的欄位也會通過**，
    而那正是這條檢查要抓的東西。
    """
    source = "\n".join(
        (WEB / part).read_text(encoding="utf-8") for part in READERS.get(name, [name])
    )
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"<!--.*?-->", "", source, flags=re.S)
    # `://` 排除掉，否則一個網址會把它後面整行真的程式碼一起吃掉。
    return re.sub(r"(?<!:)//[^\n]*", "", source)


def _leaf_paths(value, prefix: str, out: dict[str, str]) -> dict[str, str]:
    """狀態裡每一個欄位的**路徑** → 名字（含巢狀與陣列第一筆）。

    ## 為什麼是路徑，不是一堆名字

    2026-08-24 之前這裡收的是 `set[str]`——只有名字。而外框同時推兩個
    叫 `group` 的東西：

    | 路徑 | 是什麼 | 畫面讀不讀 |
    |---|---|---|
    | `shell.pages[].group` | 側邊欄分組（哪幾項之間畫一條線）| ✅ `p.group` |
    | `shell.run.group` | **這一次收場要他動哪一樣**（五組之一）| ❌ 一個字都沒讀 |

    收成一個 set 之後兩個都變成字串 `"group"`，而其中一個**被讀到就替
    另一個擋掉了**。於是那個「收場要他動哪一樣」從推出去的那天起就沒有人
    讀，而這條守衛是綠的。

    這正是這條守衛存在的理由所擋的那一種（`shell.activity` 那次），
    **它自己被同一個病躲過去了**。名字會撞——`name`、`label`、`status`、
    `state`、`id` 都是。路徑不會。
    """
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else key
            out[path] = key
            _leaf_paths(nested, path, out)
    elif isinstance(value, list) and value:
        _leaf_paths(value[0], f"{prefix}[]", out)
    return out


def _leaf_names(value, out: set[str]) -> set[str]:
    """只要名字的那一版。**留著給前提檢查用**，判斷孤兒不准用它。"""
    out.update(_leaf_paths(value, "", {}).values())
    return out


def _shell_fields(project: Path) -> set[str]:
    """外框推的欄位。它不是 `*_web_panel`，所以要另外抓。

    這個缺口不是假設出來的：`shell.activity`（ROADMAP P4 執行中橫幅）從
    2026-08-10 資料層完成起就一直在推，而 `shell.js` 一個字都沒讀——**整個
    功能在畫面上不存在**，而這條守衛第一版看不到它，因為外框的狀態是
    `MainWindow._shell_state()` 組的，不在那七個面板裡。

    帶一個等人中的專案進來，否則 `activity` 會是空陣列，裡面那幾個欄位
    （`state`／`stage`／`focused`）一個都不會被檢查——**而空的容器會讓這條
    檢查看起來有在檢查**。

    所以那件事在這裡被**斷言**，不是只寫在上面這段話裡。負向驗證抓到過：
    把這個 session 拿掉，整個檔案照樣全綠，而外框那一項從此只檢查了外殼。
    一段描述前提的註解，跟一條保證那個前提成立的斷言，中間差的正是這個。
    """
    from app.ui.main_window import MainWindow

    window = MainWindow(project)
    try:
        session = window._session_for(project.resolve())
        session.pending_ask_result = {"review_question": "要不要刪掉舊檔？"}
        state = window._shell_state()
        strip = state["shell"]["activity"]
        assert strip and all(
            key in strip[0] for key in ("id", "name", "state", "stage", "focused")
        ), f"外框這一項沒有真的產出橫幅內容，檢查到的只是外殼：{strip}"
        return _leaf_paths(state, "", {})
    finally:
        window.deleteLater()


def _credentials_panel():
    """憑證入口。**它不進側邊欄**，但它推的欄位一樣要有人讀。

    它的預設狀態是「還沒問過這台機器」，那時候整個 `setup` 都不推（不推
    的理由見那個面板：沒有 `state` 的列會被頁面算成不擋人，於是畫面會編
    出一句「可以開始了」）。所以這裡先把那兩列量出來——**用 `row_for()`
    真的組一份**，不是手寫一份假的：手寫的話，欄位名打錯這條就看不見了。
    """
    from app.services.startup_diagnostics import Check, Level
    from app.ui.credentials_web_panel import CredentialsWebPanel, row_for

    panel = CredentialsWebPanel()
    panel._providers = [
        row_for(Check("claude", "Claude Code", Level.BLOCKED, "尚未登入"), "signed_out"),
        row_for(Check("codex", "Codex CLI", Level.OK, "0.147.0"), "ok"),
    ]
    panel._checked_at = 0.0
    return panel


def _panels(project: Path):
    from app.ui.audit_web_panel import AuditWebPanel
    from app.ui.dashboard_web_panel import DashboardWebPanel
    from app.ui.documents_web_panel import DocumentsWebPanel
    from app.ui.knowledge_web_panel import KnowledgeWebPanel
    from app.ui.lineup_web_panel import LineupWebPanel
    from app.ui.roadmap_web_panel import RoadmapWebPanel
    from app.ui.settings_web_panel import SettingsWebPanel

    # Dashboard 與 Roadmap 不吃專案路徑：一個看全部專案，一個看 AICC 自己的
    # ROADMAP。這裡照它們真正的建構方式建，不要為了迴圈好寫而改它們。
    made = [
        AuditWebPanel(project),
        DocumentsWebPanel(project),
        KnowledgeWebPanel(project),
        LineupWebPanel(project),
        SettingsWebPanel(project),
        DashboardWebPanel(),
        RoadmapWebPanel(),
        _credentials_panel(),
    ]
    for panel in made:
        # 陣容頁的 collect_state 會去掃這台機器上所有本機對話。這條檢查問的是
        # 「推了哪些欄位」，不是「掃得到什麼」——而掃到的東西會讓它變成非決定性的。
        if hasattr(panel, "_scanned"):
            panel._scanned = True
            panel._hits = {"claude": [], "codex": []}
    return made


@pytest.fixture(scope="module")
def pushed(tmp_path_factory):
    """{頁面: 推出去的欄位名}，整個模組只建一次面板。

    `APPDATA` 在建面板的時候換掉，建完就換回去。**這個 fixture 是 module
    scope，用不到 function scope 的 `monkeypatch`**，所以還原要自己寫。

    2026-08-21 之前這裡是一行 `os.environ["APPDATA"] = ...` 沒有還原，於是這個
    模組跑完之後，**整個 pytest 行程剩下的每一支測試**看到的 APPDATA 都是這裡
    的暫存資料夾，而不是 `conftest.py` 的沙箱。沒有人會因此變紅——兩個都是暫存
    資料夾，寫進哪一個都不會出事——所以它安靜地活了下來，直到
    `test_your_real_config_stays_yours.py` 開始問「現在指到哪」。
    """
    tmp = tmp_path_factory.mktemp("void")
    previous = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(tmp / "appdata")
    try:
        QApplication.instance() or QApplication([])
        project = tmp / "project"
        (project / ".aicc").mkdir(parents=True)
        ProjectRegistry.initialize_project(project)

        out = {}
        for panel in _panels(project):
            out[panel.PAGE] = _leaf_paths(panel.collect_state(), "", {})
        # 外框的程式碼在 shell.js，不在 shell.html——`shell.html` 只是兩個 view
        # 的載入點，畫面全部由 shell.js 畫。對 html 檢查會每一條都紅。
        out["shell.js"] = _shell_fields(project)
        return out
    finally:
        if previous is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = previous


def test_every_page_reported_something(pushed) -> None:
    # 前提檢查：面板建不起來或 collect_state 回空的話，底下每一條都會恆真。
    assert len(pushed) == 9, pushed.keys()
    for page, paths in pushed.items():
        assert len(paths) >= 3, f"{page} 只推了 {paths}"


def test_two_fields_with_the_same_name_are_counted_separately(pushed) -> None:
    """**同名不同路徑的欄位不准互相擋掉。**

    外框推兩個 `group`（側邊欄分組、收場要他動哪一樣）。收成名字的話，
    被讀到的那一個會替沒被讀的那一個擋掉——而這條守衛就是為了抓那種事
    才存在的。
    """
    shell = pushed["shell.js"]
    groups = sorted(path for path, name in shell.items() if name == "group")
    assert len(groups) >= 2, (
        "外框那兩個 `group` 收成一個了，代表這裡又退回只看名字：" f"{groups}"
    )


def test_no_field_is_pushed_that_the_page_never_reads(pushed) -> None:
    """推出去的每一個欄位，畫面那一側都要有人讀。

    ## 判斷分兩級，而第二級是 2026-08-24 補的

    **名字只出現在一條路徑上**：畫面的程式碼裡有那個名字就算讀了。
    這是原本的做法，而它對絕大多數欄位是對的。

    **名字出現在兩條以上的路徑上**：純字串比對**不算數**。
    `"group" in code` 是 `True`，但那不告訴你它讀的是**哪一個** `group`
    ——而外框剛好推兩個（側邊欄分組、收場要他動哪一樣），其中一個
    被讀到就替另一個擋掉了。這種要逐路徑登記在 `CONFIRMED_READ`
    或 `KNOWN_UNREAD`，**由人指認**。

    為什麼不做得更聰明：畫面大量用間接取值（`seg('effort', …)` 裡面才
    `state[name]`），所以「這一行讀的是哪個物件」沒有辦法用樣式比對算出來。
    量過了——想用取值語法（`.name`／`['name']`）過濾的話，14 個真的有被讀
    的欄位會被誤判成孤兒。**一條會誤報的守衛比沒有更貴。**
    """
    orphans = {}
    for page, paths in pushed.items():
        code = _page_code(page)
        allowed = KNOWN_UNREAD.get(page, {})
        confirmed = CONFIRMED_READ.get(page, {})
        by_name: dict[str, list[str]] = {}
        for path, name in paths.items():
            by_name.setdefault(name, []).append(path)

        missing = []
        for path, name in sorted(paths.items()):
            if path in allowed or path in confirmed:
                continue
            if len(by_name[name]) > 1:
                # 撞名的：字串比對不算數，要逐路徑指認。
                missing.append(path)
                continue
            if name in allowed:
                continue
            if name not in code:
                missing.append(path)
        if missing:
            orphans[page] = missing

    assert not orphans, (
        f"這些欄位推給畫面了，而畫面的程式碼裡沒有人讀：{orphans}。\n"
        "推出去沒人讀不會壞掉，會**看起來很合理**——畫面會用它自己那份推論"
        "給出一個在今天的資料下剛好正確的答案。要嘛接上，要嘛不要推，"
        "要嘛列進 KNOWN_UNREAD 並寫下理由。"
    )


def test_the_debt_list_has_not_gone_stale(pushed) -> None:
    """待接清單上的東西，要真的還沒被接。

    這是這張清單不會爛掉的那一半。少了它，`KNOWN_UNREAD` 只會愈長愈長，
    而且**接上之後也不會有人回來刪**——下一個人讀到的會是一張說謊的債務清單。

    ## ⚠️ 這一半對「撞名的欄位」問不出來，而那一點要寫出來

    `shell.run.group` 被接上的那一天，畫面上會多一行 `run.group`——但
    `"group" in code` **從第一天就是 `True`**（側邊欄那個 `p.group`），
    所以純字串比對答不了「這筆債還完了沒」。

    退而求其次：撞名的那幾條路徑**每一條都要被指認過**（`KNOWN_UNREAD`
    或 `CONFIRMED_READ`）。那擋得住「有人加了第四個 `group` 而沒有回來看
    這一組」，**擋不住**「頁面接上了但沒有人回來把它從債務清單搬走」。
    後面那一件目前沒有機制，只有交接檔裡那句「接上去的那一輪跟我說一聲」。

    **不用 parametrize。** 清單空的時候 parametrize 會產生一個
    `SKIPPED [1] got empty parameter set`，而清單空正是它最該乾脆通過的時候。
    一個跳過的測試在報告上跟一個通過的測試分不出來，這個專案已經有一條
    「零跳過」的底線。
    """
    stale = []
    for page, fields in KNOWN_UNREAD.items():
        if page not in pushed:
            stale.append(f"{page}：沒有面板在推它的狀態了，整組該刪")
            continue
        code = _page_code(page)
        paths = pushed[page]
        by_name: dict[str, list[str]] = {}
        for path, name in paths.items():
            by_name.setdefault(name, []).append(path)

        for field in sorted(fields):
            if field not in paths:
                stale.append(f"{page}.{field}：已經不推了，這一筆該刪")
                continue
            name = paths[field]
            if len(by_name[name]) == 1:
                # 名字不撞：字串比對就問得出「還完了沒」。
                if name in code:
                    stale.append(f"{page}.{field}：畫面已經在讀了，這筆債還完了")
                continue
            # **名字撞了：這一半問不出來，而問不出來要講出來。**
            # `shell.run.group` 還完的時候畫面上會多一行 `run.group`，而
            # `"group" in code` 從第一天就是 True，所以它答不了。
            # 退而求其次：撞名的那幾條**每一條都要被指認過**（不是在這張
            # 清單上，就是在 `CONFIRMED_READ` 上）。少一條就代表有人加了
            # 新的同名欄位而沒有回來看這一組。
            unaccounted = [
                sibling
                for sibling in by_name[name]
                if sibling not in fields
                and sibling not in CONFIRMED_READ.get(page, {})
            ]
            if unaccounted:
                stale.append(
                    f"{page}：`{name}` 這個名字有 {len(by_name[name])} 條路徑，"
                    f"而這幾條沒有人指認過：{unaccounted}"
                )

    assert not stale, (
        f"待接清單過期了：{stale}。\n"
        "留著的話下一個人會以為這些事還沒做——一張說謊的債務清單，"
        "比沒有清單更花時間。"
    )
