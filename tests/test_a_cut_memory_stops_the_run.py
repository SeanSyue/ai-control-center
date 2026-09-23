"""安全規則被切了的時候，這一輪**不准帶著一句假話跑下去**。

守衛本身（`_assert_safety_memory_intact`）在 CI 裡有牙齒，這件事上一輪就釘住
了。**跑起來沒有。** `Orchestrator._knowledge_context` 用一個 `except Exception`
把 `MemoryTruncatedError` 接住、在時間軸寫一行「專案資料索引讀取失敗」、然後
`return ""` 讓這一輪照跑。而空字串到了 `prompt_builder` 會被換成：

    （尚未建立專案資料索引，請依目前專案檔案判斷。）

**索引明明建好了，被切掉的是安全規則。** 模型收到的是一句與事實不符的話，外加
一句「你自己看著辦」——這比「AI 不知道自己沒資料」更糟，因為它拿到的解釋鼓勵
它繼續做下去，而畫面上什麼都看不出來（那行 `knowledge_error` 沒有任何 UI 畫
它，也沒有任何測試守它）。

同一個例外在另外兩條路（`run_codex_decision_gate`、`run_codex_memory_update`）
則是直接炸成 traceback。**同一個守衛、三條路、兩種行為。**

所以這一組釘的是三件事，而不是「有沒有丟例外」：

1. 被切的時候，這一輪**停下來**——不是帶著空脈絡照跑。
2. 模型**永遠不會收到**「尚未建立專案資料索引」那句話（那句話本身還在，它對
   「真的沒有索引」的專案是對的；錯的是讓截斷走到那裡）。
3. 三條路的答案一樣，而且停下來之後在畫面上是一個 `partial`：**跑完了、不會
   有東西回來、要人處理**，跟「尚未設定驗證」同一族。

截斷是**真的發生**的，不是 `Mock(side_effect=...)`：`_reintroduce_the_750_char_cut`
把 2026-08-21 拿掉的那一刀放回原來的位置，而丟例外的仍然是產品程式碼裡那個
會讀硬碟原文的 `_assert_safety_memory_intact`。一條用假例外的測試守不住
「守衛還在不在」。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.core import prompt_builder
from app.core.orchestrator import Orchestrator
from app.core.workflow_worker import WorkflowWorker
from app.services.ai_settings import AIExecutionSettings
from app.services.project_knowledge import (
    MEMORY_FILES,
    MEMORY_MUST_ARRIVE_WHOLE,
    MemoryTruncatedError,
    ProjectKnowledgeStore,
)
from tests.fakes import declare_verify_command, scripted_cli_runner_class

# 那句假話的原文。寫死在這裡是刻意的：它是這一組要擋的東西，改字的時候應該
# 有人被迫回來看一眼這裡為什麼要擋它。
THE_SENTENCE_THAT_IS_NOT_TRUE = "尚未建立專案資料索引"

SECURITY_RULES = [
    "祕密、token、OAuth、CLI 憑證：不進 Git、不進匯出。",
    "客戶識別資訊：匯出前一律遮蔽。",
    "不准對外發佈：不推 GitHub、不寄訊息、不上傳。",
    "付費 API 要使用者拍板，不准自己開通。",
]
OPEN_QUESTIONS = [
    "Q7 記憶檔誰有權改寫？AI 不准自己動。",
    "Q9 雲端同步的範圍？不准自己決定。",
]


def _long(body: str, size: int = 4_000) -> str:
    """灌到 `size` 字，而且**最後一行保留原本的最後一行**。

    所有的截斷都從尾巴開始吃，所以最後一行還在等於整份都在。灌的水放中間。
    """
    tail = body.strip().splitlines()[-1]
    padding = max(0, size - len(body) - len(tail))
    return f"{body}\n{'補' * padding}\n{tail}"


def make_project(tmp_path: Path, *, declare_verification: bool = True) -> Path:
    """一個八份共同記憶都很長的專案（長到 750 字那一刀切得下去）。"""
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 任務\n把這件事做完。\n", encoding="utf-8"
    )
    store = ProjectKnowledgeStore(project)
    for file_name in MEMORY_FILES:
        if file_name == "SECURITY_MODEL.md":
            body = "\n".join(f"- {rule}" for rule in SECURITY_RULES)
        elif file_name == "OPEN_QUESTIONS.md":
            body = "\n".join(f"- {item}" for item in OPEN_QUESTIONS)
        else:
            body = f"{file_name} 的內容。\n最後一行：{file_name}"
        (store.knowledge_path / file_name).write_text(
            _long(body), encoding="utf-8"
        )
    if declare_verification:
        declare_verify_command(project)
    return project


def _reintroduce_the_750_char_cut(monkeypatch, keep: int = 750) -> None:
    """把 2026-08-21 拿掉的那一刀放回去，位置跟當年一模一樣。

    刻意**不是**直接丟一個假的 `MemoryTruncatedError`：這裡組出來的是一份
    真的被切過的脈絡，而丟例外的是產品程式碼裡那個回頭讀硬碟原文的
    `_assert_safety_memory_intact`。所以哪天有人把那個檢查拔掉，這一組會跟著
    紅，而不是繼續綠著測一個自己造出來的例外。
    """

    def cut_memory(self) -> str:
        sections: list[str] = []
        for file_name, title in MEMORY_FILES.items():
            path = self.knowledge_path / file_name
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                continue
            sections.append(f"### {title}\n{content[:keep]}")
        context = "\n\n".join(sections).strip()
        self._assert_safety_memory_intact(context)
        return context

    monkeypatch.setattr(
        ProjectKnowledgeStore, "build_memory_context", cut_memory
    )


def _no_paid_calls(monkeypatch):
    """任何一次 CLI 呼叫都會讓測試炸——空腳本就是「一次都不准跑」。"""
    runner_class = scripted_cli_runner_class([])
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)
    return runner_class


def _timeline_events(orchestrator: Orchestrator) -> list[dict]:
    path = orchestrator.timeline.timeline_path
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- 1. 這一輪停下來，而不是帶著空脈絡照跑 -----------------------------------


def test_a_cut_memory_stops_the_round_instead_of_emptying_it(
    tmp_path: Path, monkeypatch
) -> None:
    """以前這裡回傳 `""`，而 `""` 在下游是「沒有索引」的意思。"""
    _reintroduce_the_750_char_cut(monkeypatch)
    _no_paid_calls(monkeypatch)
    orchestrator = Orchestrator(make_project(tmp_path))

    with pytest.raises(MemoryTruncatedError) as caught:
        orchestrator._knowledge_context("任何任務")

    # 訊息要講得出是哪一份，不然收到的人不知道要去看什麼。
    assert any(name in str(caught.value) for name in MEMORY_MUST_ARRIVE_WHOLE)


def test_the_round_is_never_told_the_index_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """那句假話是這件事真正的傷口，所以它自己要有一條測試。

    先證明傷口存在（空脈絡確實會變成那句話），再證明截斷這條路走不到那裡。
    只驗第二半的話，哪天有人把那句話改成別的說法，這條測試會繼續綠著，而它
    守的東西早就換了。
    """
    # 傷口：空字串到了組裝端就是那句話。
    lie = prompt_builder.build_work_plan_prompt(
        task="任何任務",
        knowledge_context="",
        output_instruction="請輸出做法。",
    )
    assert THE_SENTENCE_THAT_IS_NOT_TRUE in lie, (
        "這條測試的前提不成立了：空脈絡已經不會變成那句話，"
        "請重新確認下面在守什麼"
    )

    _reintroduce_the_750_char_cut(monkeypatch)
    _no_paid_calls(monkeypatch)
    orchestrator = Orchestrator(make_project(tmp_path))

    # 而截斷走不到那裡：它在拿到脈絡之前就停了。
    with pytest.raises(MemoryTruncatedError):
        orchestrator._knowledge_context("任何任務")


def test_the_timeline_says_the_memory_was_cut_not_that_the_index_failed(
    tmp_path: Path, monkeypatch
) -> None:
    """「專案資料索引讀取失敗」對這件事也是錯的描述——索引好好的。"""
    _reintroduce_the_750_char_cut(monkeypatch)
    _no_paid_calls(monkeypatch)
    orchestrator = Orchestrator(make_project(tmp_path))

    with pytest.raises(MemoryTruncatedError):
        orchestrator._knowledge_context("任何任務")

    events = _timeline_events(orchestrator)
    kinds = [event["event_type"] for event in events]
    assert "memory_truncated" in kinds, f"時間軸沒有留下原因：{kinds}"
    assert "knowledge_error" not in kinds, "截斷被寫成了「索引讀取失敗」"

    cut = next(e for e in events if e["event_type"] == "memory_truncated")
    assert any(name in cut["content"] for name in MEMORY_MUST_ARRIVE_WHOLE)


# --- 2. 三條路的答案要一樣 ---------------------------------------------------


THREE_PATHS = {
    "work_plan": lambda o: o._knowledge_context("任何任務"),
    "decision_gate": lambda o: o.run_codex_decision_gate("要不要刪掉舊檔？"),
    "memory_update": lambda o: o._memory_context(),
}


@pytest.mark.parametrize("path_name", sorted(THREE_PATHS))
def test_all_three_paths_answer_a_cut_memory_the_same_way(
    tmp_path: Path, monkeypatch, path_name: str
) -> None:
    """一條吞、兩條炸，是這個守衛真正的破口。

    吞掉的那條最貴（它讓執行繼續，而且帶著一句假話），但三條不一致本身就是
    問題：下一個人改到其中一條的時候，沒有東西告訴他另外兩條是什麼行為。
    """
    _reintroduce_the_750_char_cut(monkeypatch)
    runner_class = _no_paid_calls(monkeypatch)
    orchestrator = Orchestrator(make_project(tmp_path))

    with pytest.raises(MemoryTruncatedError):
        THREE_PATHS[path_name](orchestrator)

    # 而且是在派工**之前**停的：沒有花掉任何一次付費呼叫。
    assert runner_class.calls == [], f"{path_name} 已經派了工才停"
    assert "memory_truncated" in [
        event["event_type"] for event in _timeline_events(orchestrator)
    ]


def test_an_index_that_simply_failed_still_lets_the_run_continue(
    tmp_path: Path, monkeypatch
) -> None:
    """反方向：**只有截斷**改行為，其他的失敗照舊。

    沒有這一條，上面那幾條可以靠「把 `except Exception` 整個拿掉」通過，
    而那會讓一個索引壞掉的專案從「少了佐證但跑得完」變成「跑不動」——那是
    另一件事，而且是使用者沒有要求的行為改變。
    """
    _no_paid_calls(monkeypatch)
    orchestrator = Orchestrator(make_project(tmp_path))

    def explode(*args, **kwargs):
        raise RuntimeError("索引壞了")

    orchestrator.knowledge.build_context = explode

    assert orchestrator._knowledge_context("任何任務") == ""
    kinds = [event["event_type"] for event in _timeline_events(orchestrator)]
    assert "knowledge_error" in kinds
    assert "memory_truncated" not in kinds


# --- 3. 停下來之後，畫面上是一個要人處理的狀態 -------------------------------


def test_the_worker_ends_the_run_as_something_a_person_must_handle(
    tmp_path: Path, monkeypatch
) -> None:
    """不是 traceback，也不是一個看起來很正常的結局。

    走的是真的 `WorkflowWorker.run()`：它自己建 Orchestrator、自己問這個專案
    怎麼驗、然後在派工前撞上截斷。空的 CLI 腳本保證這一路一次都沒有花錢。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    _reintroduce_the_750_char_cut(monkeypatch)
    runner_class = _no_paid_calls(monkeypatch)
    project = make_project(tmp_path)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
            max_fix_attempts=1,
        ),
    )
    completed: list[dict] = []
    failed: list[str] = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)

    worker.run()

    assert failed == [], f"變成 traceback 了：{failed}"
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "MEMORY_TRUNCATED"
    assert result["review_status"] == "NOT_RUN"
    # 停下來的理由要跟著結果走，不然畫面只能寫一句罐頭。
    assert any(
        name in result["memory_problem"] for name in MEMORY_MUST_ARRIVE_WHOLE
    )
    assert runner_class.calls == [], "停之前已經花錢了"


def test_the_window_paints_it_as_a_run_that_needs_a_person(
    tmp_path: Path, monkeypatch
) -> None:
    """`partial`，不是 `error`，更不是安靜地看起來像跑完了。

    走真的 `on_completed`：決定這個結局屬於哪一族的是它，而注意力條讀的正是
    它寫下的東西。這也是「跑完了但要人處理」那一族唯一不會被收尾抹掉的狀態。
    """
    from app.ui.main_window import MainWindow

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = make_project(tmp_path, declare_verification=False).resolve()
    other = tmp_path / "另一個專案"
    (other / ".aicc").mkdir(parents=True)
    (other / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")

    window = MainWindow(other.resolve())
    window._session_for(project)

    window.on_completed(
        project,
        {
            "workflow_status": "MEMORY_TRUNCATED",
            "review_status": "NOT_RUN",
            "memory_problem": (
                "SECURITY_MODEL.md 沒有完整進入 prompt：硬碟上 1,675 字，"
                "組出來的脈絡裡找不到全文。"
            ),
        },
    )

    session = window._session_for(project)
    assert session.last_outcome is not None, "這個結局沒有被記下來"
    assert session.last_outcome[1] == "partial", session.last_outcome

    entries = window._shell_state()["shell"]["activity"]
    assert [entry["name"] for entry in entries] == [project.name], (
        f"停在那裡等人處理的專案不在注意力條上：{entries}"
    )
    assert entries[0]["state"] == "partial"
    # 一行字要說得出去看哪一份，不然「共同記憶不完整」等於沒說。
    assert "SECURITY_MODEL.md" in entries[0]["stage"]

    window.deleteLater()
