"""新電腦接手清單（ROADMAP P5）。

搬過來之後還沒完，而沒做完的那幾件事**沒有一件會自己出聲**：沒有 git 歷史、
LINE 不會通知、第一次執行是全新的對話。它們都是「安靜地少了一塊」。

這份清單刻意不是一份靜態說明文件，而是每一項自己算得出「做完了沒有」：

* **打完勾會自己消失的清單，才不會變成沒有人看的裝飾。** 一份每次都長一樣的
  說明，第二次就沒有人讀了。
* 只有搬過來的專案才有清單（看 `.aicc/handover.json`）。對每個專案都跳一份
  接手指南，等於沒有指南。
* **分不出「本來就沒有」和「沒跟過來」的項目，寧可不列。** 一個專案本來就
  不是 git repo，卻每次都被提醒「歷史沒跟過來」，那條提醒會連帶把旁邊真的
  重要的那條一起訓練成雜訊。所以 `had_git` 這種只有舊機知道的事，是在匯出
  當下記進壓縮檔的，不是在這裡猜的。
"""
from __future__ import annotations

from app.i18n import t

import json
from dataclasses import dataclass
from pathlib import Path

from app.services.line_notifier import LineNotifier
from app.services.project_knowledge import ProjectKnowledgeStore
from app.services.session_registry import SessionRegistry

MARKER_NAME = "handover.json"


@dataclass(frozen=True)
class HandoverStep:
    """接手清單的一項。

    `key` 給畫面當樣式與排序的依據，`title` 是一行標題，`detail` 是「為什麼
    要做」跟「怎麼做」。`done` 是**算出來的**，不是使用者自己打的勾——打勾式
    的清單只證明有人點過那個框。
    """

    key: str
    title: str
    detail: str
    done: bool


def handover_marker(project_path: Path) -> dict | None:
    """搬家紀錄，沒有就回 None（代表這個專案不是搬過來的）。"""
    path = Path(project_path) / ".aicc" / MARKER_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _documents_step(project: Path) -> HandoverStep | None:
    """文件索引有沒有真的跟過來。

    比對的是 `.aicc/knowledge/manifest.json`（匯出當下記下的文件清單）跟現在
    索引裡的數量。**光看「現在有幾份」分不出「沒搬過來」和「本來就沒有」**，
    而這兩者要講的話完全相反。

    這一條在 2026-08-10 修掉 WAL 那個 bug 之前會一直是紅的：當時打包出去的
    索引結構完整但內容是空的。留著它是因為舊版 AICC 匯出的壓縮檔還在外面。
    """
    store = ProjectKnowledgeStore(project)
    manifest_path = store.knowledge_path / "manifest.json"
    try:
        expected = len(
            json.loads(manifest_path.read_text(encoding="utf-8")).get(
                "documents", []
            )
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    if expected == 0:
        # 舊機本來就沒有文件，沒有東西該跟過來。
        return None

    try:
        actual = len(store.list_documents())
    except Exception:
        actual = 0
    return HandoverStep(
        key="documents",
        title=t("handover.rebuild_index"),
        detail=(
            f"舊機有 {expected} 份文件，這台只讀得到 {actual} 份。"
            "在文件頁按「掃描專案」重建索引；在那之前，執行時不會帶到專案資料。"
            if actual < expected
            else f"{expected} 份文件都在。"
        ),
        done=actual >= expected,
    )


def _git_step(project: Path, marker: dict) -> HandoverStep | None:
    if not marker.get("had_git"):
        return None
    present = (project / ".git").is_dir()
    return HandoverStep(
        key="git",
        title=t("handover.reconnect_git"),
        detail=(
            t("handover.git_note")
            if not present
            else t("handover.git_done")
        ),
        done=present,
    )


def _line_step(marker: dict) -> HandoverStep | None:
    if not marker.get("line_notify_configured"):
        return None
    try:
        configured = LineNotifier().is_configured()
    except Exception:
        configured = False
    return HandoverStep(
        key="line",
        title=t("handover.line_credentials"),
        detail=(
            t("handover.line_note")
            if not configured
            else t("handover.line_done")
        ),
        done=configured,
    )


def _sessions_step(project: Path) -> HandoverStep:
    """第一次執行會是全新的對話。

    這一條沒有「要去做什麼」——它是講一件已經發生的事：舊機的 session id 在
    匯入時被清掉了（清掉是對的，那些 id 在這台機器上不存在）。留它在清單裡是
    因為**「模型不記得上次談過什麼」如果沒有人先講，會被當成故障**。
    """
    has_session = any(
        slot.session_id for slot in SessionRegistry(project).load_lineup()
    )
    return HandoverStep(
        key="sessions",
        title=t("handover.new_sessions"),
        detail=(
            t("handover.sessions_note")
            if not has_session
            else t("handover.sessions_done")
        ),
        done=has_session,
    )


def _decisions_step(marker: dict) -> HandoverStep | None:
    """舊機的決策紀錄沒有跟過來，而那件事沒有人會自己發現。

    `decisions` 不在 `ALLOWED_AICC_PREFIXES`（雲端白名單也一樣），而那是**刻意
    的政策**：稽核紀錄只從會驗證它、也會講清楚界線的那道門出去，也就是決策
    紀錄頁自己的匯出。這一條不是要改那個政策，是要讓它被講出來。

    為什麼非講不可：新機的雜湊鏈從 GENESIS 重新開始，所以之後從這台匯出給
    稽核的檔案會顯示「N 筆通過驗證」——**完整、可信，而且少了搬家前的全部**。
    那份檔案的用途正是拿給別人看，而它不會自己說「還有更早的在另一台機器上」。

    跟 `_documents_step` 同一個作法：跟舊機記下的數字比。**只看「這台現在有
    幾筆」分不出「沒搬過來」和「本來就沒有」**，而這兩者要講的話完全相反。
    """
    expected = marker.get("decision_entries")
    if not isinstance(expected, int) or expected <= 0:
        # 舊版的 handover.json 沒有這個欄位，舊機沒有紀錄時也是 0。
        # 兩種都不該生出一條沒有內容的提醒。
        return None
    return HandoverStep(
        key="decisions",
        title=t("handover.decisions_stay"),
        detail=(
            f"舊機有 {expected} 筆決策紀錄，**刻意沒有放進這個包**——稽核紀錄"
            "只從決策紀錄頁自己的匯出出去，那道門會驗證雜湊鏈也會講清楚它"
            "證明得了什麼。這台的紀錄會從頭開始，所以之後從這台匯出的檔案"
            "不會包含搬家前的部分。要完整的話，去舊機匯出一份收好。"
        ),
        # 沒有「做完」的狀態：這是一件已經發生的事，不是待辦。
        # 標成未完成會讓清單永遠收不起來。
        done=True,
    )


def handover_steps(project_path: Path) -> tuple[HandoverStep, ...]:
    """這個專案搬過來之後還剩什麼要做，做完的也一起回。

    回空的 tuple 有兩種意思，而它們對畫面來說是同一件事（不要顯示清單）：
    這個專案不是搬過來的，或者搬過來要做的事這台機器上都不適用。
    「全部做完了」則會回傳全部 `done=True` 的項目——**那跟「沒有清單」不一樣**，
    畫面可以說一句「都好了」再收起來。
    """
    project = Path(project_path)
    marker = handover_marker(project)
    if marker is None:
        return ()

    steps = [
        _documents_step(project),
        _git_step(project, marker),
        _line_step(marker),
        _sessions_step(project),
        _decisions_step(marker),
    ]
    return tuple(step for step in steps if step is not None)


def outstanding(steps: tuple[HandoverStep, ...]) -> tuple[HandoverStep, ...]:
    return tuple(step for step in steps if not step.done)
