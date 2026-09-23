"""里程碑計畫有沒有真的送到動手的那一格（ROADMAP P3）。

`LongTaskPlanner` 一直在產生 `milestones.json` 與 `milestones.md`，而**沒有任何
東西讀它們**——不是 prompt、不是索引、不是任何畫面。實測方式是跑一次工作流然後
看真正送進 CLI 的字串：檔案出現了，prompt 裡一個字都沒提。

而那份文字是用第二人稱寫給 Claude 的（「Claude should complete work in small
resumable steps」），也就是說它本來就是要被看見的，只是線沒接上。

第二組斷言比第一組重要：**散文式的任務不可以拿到它**。那種任務切出來只有一格，
內容就是把使用者剛剛寫的話再唸一次給已經拿到那句話的模型聽——付 token 講兩遍。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.adapters.cli_runner import CliResult
from app.core.orchestrator import Orchestrator
from app.services.ai_settings import AIExecutionSettings

MARK = "Long Task Milestones"
LISTED = "# 任務\n1. 先改設定頁的儲存列\n2. 再補上回歸測試\n3. 最後更新 README\n"
PROSE = "把設定頁的儲存列修好，它現在會蓋住最後一行內容。"


def prompt_for(tmp_path: Path, task: str, **settings) -> str:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(task, encoding="utf-8")
    orchestrator = Orchestrator(
        project,
        run_id="run-1",
        settings=AIExecutionSettings(
            auto_session_bootstrap=False, **settings
        ),
    )
    seen: list[str] = []

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        # The prompt arrives on stdin, not in argv.
        seen.append(input_text or "")
        return CliResult(
            command=arguments[0],
            success=True,
            return_code=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    orchestrator.run_claude()
    return seen[0]


def test_a_listed_task_hands_the_steps_to_the_author(tmp_path: Path) -> None:
    """The wire that was missing. The plan existed; nobody was shown it."""
    prompt = prompt_for(tmp_path, LISTED)

    assert MARK in prompt
    assert "先改設定頁的儲存列" in prompt
    assert "再補上回歸測試" in prompt


def test_a_prose_task_gets_no_plan(tmp_path: Path) -> None:
    """One milestone is the task read back to the model that already has it.

    This is the half that keeps the feature from being a token tax on every
    ordinary run.
    """
    prompt = prompt_for(tmp_path, PROSE)

    assert MARK not in prompt
    # ...and the task itself is of course still there.
    assert "把設定頁的儲存列修好" in prompt


def test_turning_long_task_mode_off_removes_it(tmp_path: Path) -> None:
    # The settings toggle has to reach this, or it is a switch over a feature
    # that ignores it.
    prompt = prompt_for(tmp_path, LISTED, long_task_mode=False)

    assert MARK not in prompt


def test_the_plan_files_are_still_written(tmp_path: Path) -> None:
    # Wiring the prompt must not replace the artifacts: they are what a person
    # reads afterwards, and `inspect_run --step` shows the prompt anyway.
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(LISTED, encoding="utf-8")
    Orchestrator(
        project,
        run_id="run-1",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
    )

    run_dir = project / ".aicc" / "runs" / "run-1"
    assert (run_dir / "milestones.json").is_file()
    assert (run_dir / "milestones.md").is_file()
