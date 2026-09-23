"""每一種設定的家在哪。**一份清單，不是一句約定。**

老闆 2026-08-21 問的原話：

> 阿那些精靈設定下來的東西寫在哪　**會不會亂跑亂發散亂存**
> 這樣架構會跟影片說的核心理念們完全背道而馳

定案是【設定分五層，一層一個家】＋【每一層一份清單，一支會紅的檢查】
（`DECISIONS` 170、176）。**這個檔案就是那份清單。**

## 為什麼是「依功能分類」而不是「依誰改它」

老闆 2026-08-24：

> 我希望**資料依功能分類**，這樣模組化更直白。

原本的分法是「產品／系統／專案／執行／記憶」——那是**依誰改它、多久改一次**
分的。改成依功能之後，分界線變成「**這份資料在做什麼**」，跟深模組同一個切法：
問「這是什麼」就找得到它住哪，不必先知道它是誰寫的。

## 這份清單擋的是什麼

不是「檔案放錯資料夾」——那個看得出來。擋的是**多一個家**：
某個功能寫了一個誰都沒決定過位置的新檔，而畫面上完全正常。

2026-08-24 量到的那個打錯字（`QSettings("Sean", "AIControlCenter")` 少一個
空格，在登錄檔裡是**另一個位置**）就是這件事的實例：一個面板的展開狀態在
錯的地方躺了不知道多久，沒有任何人發現。

**約定俗成撐不過一次打錯字，而打錯字不會有人發現。**
"""
from __future__ import annotations

from dataclasses import dataclass

# 跟著專案資料夾走：會被 Git 帶走、會被匯出、會被雲端同步。
SCOPE_PROJECT = "project"
# 跟著這台機器上的這個人走：不會跟專案跑出去。
SCOPE_MACHINE = "machine"

# 當掉的時候留下的。刻意跟上面兩個分開：它放在使用者自己找得到的
# `Documents` 底下，而那是**第三個位置**——2026-08-24 掃 `app/` 才發現的。
SCOPE_LOGS = "logs"

SCOPE_LABELS = {
    SCOPE_PROJECT: "scope.project",
    SCOPE_MACHINE: "scope.machine",
    SCOPE_LOGS: "scope.logs",
}


@dataclass(frozen=True)
class Home:
    """一種設定，以及它唯一的家。"""

    # 「這是什麼」——依功能分類的那個功能名的 **key**（`app/i18n.py`）。
    #
    # **存 key 不存字**，而理由不只是翻譯：`project_wizard.WRITES_TO` 拿這個值
    # 當查詢的鍵。存中文的話，介面翻成英文的那一刻那個查詢就斷了——而斷掉的
    # 樣子是精靈說「我不知道這一題寫到哪」，在一個看起來完全正常的畫面上。
    what: str

    @property
    def label(self) -> str:
        """給人看的那一句。**用到才取**，不在 import 的時候凍住。"""
        from app.i18n import t

        return t(self.what)
    # 相對於專案資料夾，或相對於 `%APPDATA%\AIControlCenter`。
    where: str
    scope: str
    # 誰會寫它。空的表示「引擎自己寫，使用者不直接改」。
    written_by: str = ""


HOMES: tuple[Home, ...] = (
    # --- 這個專案的（跟著專案資料夾走：Git、匯出、雲端同步都會帶走）--------
    Home(
        "home.project.what_and_how",
        ".aicc/project.json",
        SCOPE_PROJECT,
        "written_by.project_json",
    ),
    Home(
        "home.project.people",
        ".aicc/sessions.json",
        SCOPE_PROJECT,
        "written_by.sessions",
    ),
    Home(
        "home.project.rules",
        ".aicc/knowledge/PROJECT_RULES.md",
        SCOPE_PROJECT,
        "written_by.rules",
    ),
    Home(
        "home.project.memory",
        ".aicc/knowledge/",
        SCOPE_PROJECT,
        "",  # 那八份＋索引。AICC 跑完之後自己維護，精靈不碰。
    ),
    Home(
        "home.project.this_run",
        ".aicc/runs/",
        SCOPE_PROJECT,
        "",  # run.json／timeline.jsonl／milestones.json。引擎寫完就唯讀。
    ),
    Home(
        "home.project.this_task",
        ".aicc/task.md",
        SCOPE_PROJECT,
        "",  # 派工當下寫的，下一次會被蓋掉。
    ),
    Home(
        "home.project.this_result",
        ".aicc/workflow-result.json",
        SCOPE_PROJECT,
        "",
    ),
    Home(
        "home.project.runtime_lock",
        ".aicc/runtime/",
        SCOPE_PROJECT,
        "",  # workflow.lock。開兩個視窗撞在一起的時候靠它。
    ),
    Home(
        "home.project.sync_state",
        ".aicc/sync-state.json",
        SCOPE_PROJECT,
        "",
    ),
    Home(
        "home.project.handover",
        ".aicc/handover.json",
        SCOPE_PROJECT,
        "",
    ),
    Home(
        "home.project.gitignore",
        ".aicc/.gitignore",
        SCOPE_PROJECT,
        "",  # AICC 開專案的時候寫一次。
    ),
    Home(
        "home.project.timeline",
        ".aicc/timeline/",
        SCOPE_PROJECT,
        "",  # timeline.jsonl。跨每一次執行的那一條，跟 `runs/<這一次>/` 底下
        #     那份同名的不一樣——**那份只有那一次**。
    ),
    Home(
        "home.project.decisions",
        ".aicc/decisions/",
        SCOPE_PROJECT,
        "",  # decision-log.jsonl。每一筆扣上一筆的雜湊，中間被抽掉看得出來。
    ),
    Home(
        "home.project.conflicts",
        ".aicc/conflicts/",
        SCOPE_PROJECT,
        "",  # 同步撞車時兩個版本都留，**不自動挑一個**。
    ),
    Home(
        "home.project.imported",
        ".aicc/imported-context/",
        SCOPE_PROJECT,
        "",  # 匯入的對話紀錄。跟 AICC 自己產生的分開放。
    ),
    # --- 這台機器的（跟著這個人走，不會跟專案跑出去）----------------------
    Home(
        "home.machine.ai_settings",
        "ai-settings.json",
        SCOPE_MACHINE,
        "written_by.ai_settings",
    ),
    Home(
        "home.machine.presets",
        "lineup-presets.json",
        SCOPE_MACHINE,
        "written_by.presets",
    ),
    Home(
        "home.machine.cli_paths",
        "cli-paths.json",
        SCOPE_MACHINE,
        "written_by.cli_paths",
    ),
    Home(
        "home.machine.projects",
        "projects.json",
        SCOPE_MACHINE,
        "written_by.projects",
    ),
    Home(
        "home.machine.ui",
        r"登錄檔 HKCU\Software\Sean\AI Control Center",
        SCOPE_MACHINE,
        "written_by.ui",
    ),
    # --- 當掉的時候留下的（第三個位置，2026-08-24 掃出來才發現）-----------
    Home(
        "home.logs.crash",
        r"Documents\AI Control Center\logs\crash.log",
        SCOPE_LOGS,
        "",  # 當機處理器。刻意放在使用者找得到的地方，不是 %APPDATA%。
    ),
)


def homes_in(scope: str) -> tuple[Home, ...]:
    return tuple(h for h in HOMES if h.scope == scope)


def home_for(what: str) -> Home | None:
    """這個功能住哪。查不到回 `None`——**不猜一個位置出來**。

    猜出來的位置會真的被寫進去，而那正是「亂跑亂發散亂存」本人。
    """
    for home in HOMES:
        if home.what == what:
            return home
    return None


def project_entries() -> tuple[str, ...]:
    """專案資料夾底下合法的東西，**相對於 `.aicc/`**。

    目錄留著結尾的斜線：`runs/` 底下有 `run.json`、`timeline.jsonl`⋯⋯
    那些不必逐一列，**但 `.aicc/` 底下多一個誰都沒決定過的東西要看得出來**。
    """
    prefix = ".aicc/"
    return tuple(
        h.where[len(prefix):]
        for h in HOMES
        if h.scope == SCOPE_PROJECT and h.where.startswith(prefix)
    )


def machine_files() -> tuple[str, ...]:
    r"""`%APPDATA%\AIControlCenter` 底下所有合法的檔（登錄檔那一項不算檔案）。"""
    return tuple(
        h.where
        for h in HOMES
        if h.scope == SCOPE_MACHINE and h.where.endswith(".json")
    )
