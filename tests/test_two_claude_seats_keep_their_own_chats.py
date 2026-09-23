"""兩格同廠牌時，每一格用自己配對的那段對話（ROADMAP P10）。

P10 存在的理由，用它自己的話：

> 原本 `self.claude_session_id` 是單一欄位，兩個 Claude 的位置會共用它——
> 第二個 agent 安靜地接續第一個的對話，「獨立審查者」其實就是作者本人。

orchestrator 那邊修好了。但 `_lineup_for_run()` 把它放回來了一半：那個
provider-keyed 的參數被套到**每一個**傳統座位上，所以——

    陣容頁配的      author=AUTHOR-CHAT   reviewer=REVIEWER-CHAT
    實際跑的        author=AUTHOR-CHAT   reviewer=AUTHOR-CHAT

`REVIEWER-CHAT` 一聲不響地被丟掉。而**兩格都用 Claude 是明確支援的組合**：
陣容頁只警告「共享 context」不禁止，自用驗收清單第 4 項還直接叫使用者這樣試。

那個舊管道一次只帶得動一個 provider 的一段對話，所以它只能替**一格**發言——
就是 `selected_session_id()` 取值的那一格（該 provider 的第一格）。同 provider
的第二格保留自己存的。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.lineup import AgentSlot, Lineup  # noqa: E402
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import declare_verify_command  # noqa: E402

# 預設是 True，而它會蓋掉所有傳統座位的 session——大部分測試要看的是
# 「配對有沒有活下來」，所以明確關掉。
KEEP = AIExecutionSettings(force_new_sessions=False)


def _worker(tmp_path: Path, slots, **kwargs) -> WorkflowWorker:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True, exist_ok=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)
    declare_verify_command(project)
    SessionRegistry(project).save_lineup(Lineup.of(slots))
    kwargs.setdefault("settings", KEEP)
    return WorkflowWorker(project, **kwargs)


def _sessions(worker: WorkflowWorker) -> dict[str, str]:
    return {slot.role: slot.session_id for slot in worker._lineup_for_run()}


# --- 這個檔案存在的理由 ------------------------------------------------------


def test_a_second_claude_seat_does_not_inherit_the_first_ones_chat(
    tmp_path: Path,
) -> None:
    worker = _worker(
        tmp_path,
        [
            AgentSlot("author", "claude", "AUTHOR-CHAT"),
            AgentSlot("reviewer", "claude", "REVIEWER-CHAT"),
        ],
        # 面板只看得到第一格，所以它送上來的就是 author 那一段。
        claude_session_id="AUTHOR-CHAT",
    )

    assert _sessions(worker) == {
        "author": "AUTHOR-CHAT",
        "reviewer": "REVIEWER-CHAT",
    }


def test_the_reviewer_is_not_the_author_reading_their_own_work(
    tmp_path: Path,
) -> None:
    """同一件事，用它真正的後果來講一次。

    這條跟上面那條會一起紅，留著是因為**壞掉的時候要看得懂是什麼壞了**：
    不是「兩個字串不相等」，是獨立審查這件事沒有發生。
    """
    worker = _worker(
        tmp_path,
        [
            AgentSlot("author", "claude", "AUTHOR-CHAT"),
            AgentSlot("reviewer", "claude", "REVIEWER-CHAT"),
        ],
        claude_session_id="AUTHOR-CHAT",
    )
    sessions = _sessions(worker)

    assert sessions["reviewer"] != sessions["author"], (
        "審查者接續了作者的對話——「獨立審查」變成作者看自己寫的東西"
    )


# --- 沒有壞掉的那些 ----------------------------------------------------------


def test_the_classic_pair_still_takes_what_the_panel_selected(
    tmp_path: Path,
) -> None:
    """一 Claude 一 Codex 時，執行中對話頁選的要蓋過存檔。

    那是使用者剛剛在畫面上做的動作，比上一次存的新。
    """
    worker = _worker(
        tmp_path,
        [
            AgentSlot("author", "claude", "OLD-A"),
            AgentSlot("reviewer", "codex", "OLD-C"),
        ],
        claude_session_id="PICKED-A",
        codex_session_id="PICKED-C",
    )

    assert _sessions(worker) == {"author": "PICKED-A", "reviewer": "PICKED-C"}


def test_force_new_sessions_still_clears_both_classic_seats(
    tmp_path: Path,
) -> None:
    """限制了舊管道之後，這個設定要自己講話。

    以前它是靠「`main_window` 把兩個參數清空」達成的，而那條路現在只到得了
    第一格——所以少了這條，第二格會在使用者要求全新對話時偷偷接續舊的。
    """
    worker = _worker(
        tmp_path,
        [
            AgentSlot("author", "claude", "A"),
            AgentSlot("reviewer", "claude", "B"),
        ],
        claude_session_id="",
        settings=AIExecutionSettings(force_new_sessions=True),
    )

    assert _sessions(worker) == {"author": "", "reviewer": ""}


def test_answering_a_question_is_a_resume_not_a_fresh_start(
    tmp_path: Path,
) -> None:
    # 回答 Decision Gate 是接續同一次執行，不可以被當成「開新對話」。
    worker = _worker(
        tmp_path,
        [AgentSlot("author", "claude", "A"), AgentSlot("reviewer", "codex", "C")],
        claude_session_id="A",
        codex_session_id="C",
        user_answer="可以",
        settings=AIExecutionSettings(force_new_sessions=True),
    )

    assert _sessions(worker) == {"author": "A", "reviewer": "C"}


def test_a_newer_seat_keeps_its_own_session(tmp_path: Path) -> None:
    """規劃者不受那個 provider-keyed 管道影響——本來就是這樣，釘住它。

    它跟審查者同樣是 Codex，而舊管道只認 provider；規劃者要是也被套上去，
    就會變成這個檔案在修的那個 bug 的第三格版本。
    """
    worker = _worker(
        tmp_path,
        [
            AgentSlot("planner", "codex", "PLAN-CHAT"),
            AgentSlot("author", "claude", "A"),
            AgentSlot("reviewer", "codex", "REVIEW-CHAT"),
        ],
        claude_session_id="A",
        codex_session_id="REVIEW-CHAT",
    )

    assert _sessions(worker)["planner"] == "PLAN-CHAT"


def test_the_two_definitions_of_the_default_lineup_agree(tmp_path: Path) -> None:
    """沒排過陣容的專案，兩邊講的預設要一樣。

    `_lineup_for_run` 的 docstring 說它「沒排過就回 None，讓 orchestrator 自己
    的預設值當唯一定義」。**寫這條測試時發現那個 None 分支其實走不到**：
    `SessionRegistry.load_lineup()` 在檔案不存在時回的是
    `_default_payload()` 裡的 `Lineup.classic()`，長度永遠 ≥ 2。

    所以「單一定義」其實是兩份。它們今天講的是同一件事，而這條就是在守那件事
    ——真正危險的不是有兩份，是有兩份而且不一樣。
    """
    project = tmp_path / "empty"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)

    from app.core.orchestrator import Orchestrator

    from_registry = SessionRegistry(project).load_lineup()
    engine_default = [(role, provider) for role, provider in Orchestrator.DEFAULT_LINEUP]

    assert [(s.role, s.provider) for s in from_registry] == engine_default
