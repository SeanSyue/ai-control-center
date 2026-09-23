"""一條線上沒有人能訂驗收標準的時候，**那一輪要說出來**。

## 使用者會看到什麼（沒有這一條的話）

出廠那張「一人」表格（唯一一格的職責是執行）跑完一輪，畫面說「可以拍板」
→「工作流程已完成」，綠的、勾的。整輪執行**沒有任何一句話**說過這一輪
沒有驗收標準。

## 為什麼舊的放行理由是假的

`intake.confirm` 原本掛著一句：「這裡不擋——照舊往下跑，只是拍板檢查會
誠實地說少一個章。」

拍板檢查不會。`requires_for()` 是**從陣容算的**——`criteria_seat` 空的
時候，「驗收標準的章」那一項根本不會被列進要求，鏈子回 READY、收場是
COMPLETED。那句話唯一適用的情境（陣容裡沒有人訂標準），正是它不成立的
情境，而它就掛在那裡當作不用做事的理由。

隔壁那個對稱情境早就做對了：`workflow_worker.py` 遇到「這條線上沒有一格
會複查」時往時間軸寫一筆，理由寫在旁邊——「不准安靜地跳過：不然使用者會
得到一個『沒有人看過但看起來很正常』的結果」。這一份把同一條原則套到
需求確認那一扇門上。

## 這一條擋不住什麼

**它不擋執行。**一人表格照樣跑完、照樣收在 COMPLETED——那是拍過板的
（`DECISIONS.md`：一人表格「沒有人檢核」不必特別解釋）。它守的只有
「有沒有留下痕跡」。畫面上那個綠色的完成標籤還是綠的，要在那一列旁邊
放警示是另一件事。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import (  # noqa: E402
    DUTY_REVIEW,
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    SETUP_INTENT_ONLY,
    cli_result,
    declare_verify_command,
    intake_settled,
    scripted_cli_runner_class,
)

# 這一輪沒有驗收標準這件事，在時間軸上叫什麼。**存 key，不是存那句中文**
# ——畫面要字的時候才 `t()`。
EVENT = "no_criteria_seat"

PASS_REVIEW = (
    '{"status": "PASS", "summary": "看過了，可以", "issues": [], '
    '"instructions": [], "question": ""}'
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 做一個小工具\n", encoding="utf-8")
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    return project


def _run(project: Path, monkeypatch, script):
    QApplication.instance() or QApplication([])
    runner_class = scripted_cli_runner_class(script, auto=SETUP_INTENT_ONLY)
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
            max_fix_attempts=1,
        ),
    )
    completed: list = []
    failed: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed


def _events(project: Path) -> list[dict]:
    path = project / ".aicc" / "timeline" / "timeline.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_a_line_up_with_nobody_to_set_the_bar_leaves_a_trace(
    tmp_path: Path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    出廠「一人」表格跑完一輪，時間軸上要有一筆「這一輪沒有驗收標準」。

    改壞了的樣子：整輪執行從頭到尾一個字都沒提過這件事，而收場是綠色的
    「工作流程已完成」——使用者拿到的是一份沒有標準、沒有人審、看起來很
    正常的結果。
    """
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(
        Lineup.of([AgentSlot("實作", "claude", duty=DUTY_WORK)])
    )

    completed, failed = _run(
        project, monkeypatch, [cli_result('{"result": "做好了"}'), cli_result("1 passed")]
    )

    assert failed == [], failed
    said = [e for e in _events(project) if e["event_type"] == EVENT]
    assert said, (
        "整輪執行沒有留下任何一筆「這一輪沒有驗收標準」。"
        f"時間軸上有的是：{sorted({e['event_type'] for e in _events(project)})}"
    )
    assert said[0]["content"].strip(), "留了一筆，但那一筆是空的"


def test_the_run_still_finishes_it_is_a_note_not_a_gate(
    tmp_path: Path, monkeypatch
) -> None:
    """**留痕不是擋人。**

    這一條守的是上面那一條不准越界：出廠一人表格是拍過板的預設值，
    把它擋掉等於把「打開產品、跟一個 AI 講話」這條路擋掉。

    改壞了的樣子：使用者用出廠設定跑第一次，拿到的是一個停下來的執行。
    """
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(
        Lineup.of([AgentSlot("實作", "claude", duty=DUTY_WORK)])
    )

    completed, failed = _run(
        project, monkeypatch, [cli_result('{"result": "做好了"}'), cli_result("1 passed")]
    )

    assert failed == [], failed
    assert completed, "一人表格整個沒跑起來"
    assert completed[0]["workflow_status"] != "INTAKE_ASKING"


def test_a_line_up_that_does_have_someone_to_set_the_bar_says_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """對照組：**有人訂標準的時候不准也印這一句。**

    少了這一條，把那一行寫成無條件 append 也會讓上面那條變綠——而畫面上
    每一輪都會多一句「這一輪沒有驗收標準」，包括真的訂了標準的那幾輪。
    一條每次都出現的警告等於沒有警告。
    """
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(
        Lineup.of(
            [
                AgentSlot("複查", "codex", duty=DUTY_REVIEW),
                AgentSlot("實作", "claude", duty=DUTY_WORK),
            ]
        )
    )

    completed, failed = _run(
        project,
        monkeypatch,
        [
            intake_settled(),
            cli_result('{"result": "做好了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    said = [e for e in _events(project) if e["event_type"] == EVENT]
    assert not said, (
        "這條線上明明有人訂了驗收標準，時間軸卻說沒有："
        f"{[e['content'] for e in said]}"
    )
