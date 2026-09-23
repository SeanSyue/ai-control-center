"""每一次付費呼叫的 argv 裡，都不准出現含換行的字串。

這條規則不是新的。CliRunner 從 `.cmd` 那一輪起就會擋下多行 argv，訊息也
指名出路（`input_text`）。問題是**它只在真的開得起 cmd.exe 的執行期才會
發生**——單元測試把 CliRunner 換成假的，於是 1224 條測試全綠，而引擎的
第一步 `claude -p <一整份多行 prompt>` 在這台機器上根本送不出去：
Windows 的命令列裝不下換行，claude 解析到的是 `claude.CMD`，cmd.exe 只會
把 prompt 的第一行交給 CLI。

所以這一份存在的理由是「把叮嚀變成機制」：規則被搬到引擎自己這一側
（`Orchestrator._refuse_a_payload_in_argv`），凡是 vendor 呼叫都得經過。
只要有人再把 prompt 塞回 argv，這裡會紅，而且是在假的 CliRunner 底下就紅。

這一份刻意不看「argv 長什麼樣子比較漂亮」，只看兩件事：
1. 送出去的每一個參數都是單行的；
2. 那份多行的 payload 真的整份到了對面（stdin），不是只到第一行。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.adapters.cli_runner import CliResult
from app.core.orchestrator import Orchestrator
from app.core.workflow_worker import WorkflowWorker
from app.services.ai_settings import AIExecutionSettings
from tests.fakes import (
    calls_after_intake,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
    with_intake,
)


# 任務本文刻意是多行，而且**辨識用的那一句放在第二行以後**。只放在第一行的
# 話，「只送出第一行」這個 bug 會照樣通過測試——當初就是這樣沒被看見的。
TASK = (
    "# 任務\n"
    "\n"
    "把設定頁修好。\n"
    "\n"
    "驗收條件：關掉視窗時不准掉資料。\n"
    "備註：AICC-MULTILINE-TAIL 這一句必須整份送到，不能只送第一行。\n"
)


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(TASK, encoding="utf-8")
    declare_verify_command(project)
    return project


def _settings(**overrides) -> AIExecutionSettings:
    base = AIExecutionSettings(
        workflow_mode="development",
        auto_memory_maintenance=False,
        auto_session_bootstrap=False,
        max_fix_attempts=1,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _run_worker(tmp_path: Path, monkeypatch, script, settings=None):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = make_project(tmp_path)

    runner_class = scripted_cli_runner_class(with_intake(script))
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(project, settings=settings or _settings())
    completed: list = []
    failed: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    application.processEvents()
    return completed, failed, calls_after_intake(runner_class.calls)


def _offending_arguments(call: dict) -> list[tuple[int, str]]:
    return [
        (index, item)
        for index, item in enumerate(call.get("arguments") or [])
        if "\r" in item or "\n" in item or "\x00" in item
    ]


PASS_REVIEW = (
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)


def test_a_whole_development_run_puts_nothing_multi_line_in_argv(
    tmp_path: Path, monkeypatch
) -> None:
    """一整輪跑完，每一次呼叫的每一個參數都必須是單行的。

    含 claude（作者）、專案宣告的驗證命令、codex（審查者）三次呼叫。
    """
    script = [
        cli_result('{"result": "done, no questions"}'),
        cli_result("1 passed"),
        cli_result(PASS_REVIEW),
    ]

    completed, failed, calls = _run_worker(tmp_path, monkeypatch, script)

    assert not failed
    assert completed and completed[0]["workflow_status"] == "COMPLETED"
    assert len(calls) == 3

    for call in calls:
        offenders = _offending_arguments(call)
        assert offenders == [], (
            f"{call['arguments'][0]} 收到了含換行的命令列參數 {offenders!r}。"
            "Windows 的命令列裝不下換行，這種參數只會到達第一行——"
            "整段內容要走標準輸入（input_text）。"
        )


def test_both_vendors_receive_the_whole_prompt_on_standard_input(
    tmp_path: Path, monkeypatch
) -> None:
    """而且是整份到，不是「因為沒有換行所以合格」。

    上一條只證明 argv 乾淨；一個把 prompt 截成第一行再塞進 argv 的實作
    也會讓它變綠。這一條看的是對面到底收到什麼。
    """
    script = [
        cli_result('{"result": "done, no questions"}'),
        cli_result("1 passed"),
        cli_result(PASS_REVIEW),
    ]

    _completed, failed, calls = _run_worker(tmp_path, monkeypatch, script)
    assert not failed

    vendor_calls = [
        call for call in calls if call["arguments"][0] in {"claude", "codex"}
    ]
    assert [call["arguments"][0] for call in vendor_calls] == [
        "claude",
        "codex",
    ]

    for call in vendor_calls:
        prompt = call["input_text"]
        vendor = call["arguments"][0]
        assert prompt, f"{vendor} 這次呼叫沒有任何標準輸入——prompt 去哪了？"
        assert "\n" in prompt, (
            f"{vendor} 收到的 prompt 只有一行。這正是 bug 的長相："
            "多行的 payload 被截成第一行還看起來像成功。"
        )
        assert "AICC-MULTILINE-TAIL" in prompt, (
            f"{vendor} 收到的 prompt 少了任務最後一行。"
            "第一行有到不算送到。"
        )


def test_the_p_flag_is_a_flag_and_nothing_follows_it(
    tmp_path: Path, monkeypatch
) -> None:
    """`claude -p` 的下一個 token 必須還是旗標。

    `-p/--print` 是布林旗標，prompt 是 claude 的位置參數；把 prompt 寫在
    `-p` 後面之所以「看起來能動」，只是因為位置參數剛好排在那裡。這條
    斷言守的是形狀本身，不必等到有人送一份多行的才發現。
    """
    script = [
        cli_result('{"result": "done, no questions"}'),
        cli_result("1 passed"),
        cli_result(PASS_REVIEW),
    ]

    _completed, failed, calls = _run_worker(tmp_path, monkeypatch, script)
    assert not failed

    arguments = calls[0]["arguments"]
    assert arguments[0] == "claude"
    assert "-p" in arguments
    after = arguments[arguments.index("-p") + 1]
    assert after.startswith("-"), (
        f"`-p` 後面接了 {after!r}。那個位置不是 prompt 的位置，"
        "prompt 走標準輸入。"
    )
    assert calls[0]["input_text"] not in arguments


class _RunnerThatMustNotBeCalled:
    def __init__(self, working_directory: Path) -> None:
        self.working_directory = working_directory
        self.calls: list[list[str]] = []

    def run(self, arguments, timeout_seconds=300, input_text=None):
        self.calls.append(list(arguments))
        raise AssertionError(
            "引擎把含換行的參數送到 CliRunner 了——它應該在自己這一側就停下來"
        )


def test_the_engine_stops_a_payload_in_argv_before_it_reaches_the_runner(
    tmp_path: Path,
) -> None:
    """守門的位置很重要：在引擎這一側，不是在 CliRunner 那一側。

    CliRunner 的同一條規則只有在真的要開行程時才會發生，而測試永遠把
    CliRunner 換成假的——那條規則因此對整套測試是隱形的。放在這裡，
    假的 runner 也擋不住它。
    """
    project = make_project(tmp_path)
    orchestrator = Orchestrator(project, settings=_settings())
    runner = _RunnerThatMustNotBeCalled(project)
    orchestrator.runner = runner  # type: ignore[assignment]

    with pytest.raises(ValueError) as raised:
        orchestrator._send_prompt_to_vendor(
            ["claude", "-p", "第一行\n第二行"],
            "prompt",
            timeout_seconds=60,
        )

    assert runner.calls == [], "呼叫已經送出去了，擋在這裡就沒有意義"
    message = str(raised.value)
    assert "claude" in message
    assert "標準輸入" in message or "input_text" in message


def test_a_single_line_argument_still_goes_through(tmp_path: Path) -> None:
    """反面：守門的不能嚴到把正常呼叫也擋掉。

    沒有這條，把 `_refuse_a_payload_in_argv` 改成「一律 raise」也會讓
    上面那條變綠。
    """
    project = make_project(tmp_path)
    orchestrator = Orchestrator(project, settings=_settings())
    seen: dict = {}

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        seen["arguments"] = list(arguments)
        seen["input_text"] = input_text
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout="{}",
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    result = orchestrator._send_prompt_to_vendor(
        ["claude", "-p", "--output-format", "json"],
        "多行的\npayload\n在這裡",
        timeout_seconds=60,
    )

    assert result.success is True
    assert seen["arguments"] == ["claude", "-p", "--output-format", "json"]
    # 而 payload 本身多行完全沒關係——它走的不是命令列。
    assert seen["input_text"] == "多行的\npayload\n在這裡"
