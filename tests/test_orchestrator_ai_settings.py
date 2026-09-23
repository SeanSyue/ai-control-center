from __future__ import annotations

import json
from pathlib import Path

from app.adapters.cli_runner import CliResult
from app.core.orchestrator import BOOTSTRAP_MARKER, Orchestrator
from app.services.ai_settings import AIExecutionSettings


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 任務\n測試設定。\n",
        encoding="utf-8",
    )
    return project


def test_claude_command_uses_explicit_model_and_effort(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    settings = AIExecutionSettings(
        claude_model="sonnet",
        claude_effort="medium",
        claude_max_turns=4,
    )
    orchestrator = Orchestrator(project, settings=settings)
    captured: dict[str, object] = {}

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        captured["arguments"] = list(arguments)
        captured["input_text"] = input_text
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {"result": "done", "session_id": "claude-1"}
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    result = orchestrator.run_claude()

    args = captured["arguments"]
    assert isinstance(args, list)
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--effort") + 1] == "medium"
    assert args[args.index("--max-turns") + 1] == "4"
    assert result.stdout == "done"


def test_new_claude_session_prompt_includes_bootstrap(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    settings = AIExecutionSettings(claude_model="sonnet")
    orchestrator = Orchestrator(project, settings=settings)
    captured: dict[str, object] = {}

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        captured["arguments"] = list(arguments)
        captured["input_text"] = input_text
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {"result": "done", "session_id": "claude-new"}
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    orchestrator.run_claude()

    args = captured["arguments"]
    assert isinstance(args, list)
    prompt = captured["input_text"]
    assert isinstance(prompt, str)
    assert BOOTSTRAP_MARKER in prompt
    assert "AI Control Center Session Bootstrap" in prompt
    assert (project / ".aicc" / "knowledge" / "SESSION_BOOTSTRAP.md").is_file()


def test_existing_claude_session_does_not_repeat_bootstrap(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    orchestrator = Orchestrator(
        project,
        claude_session_id="claude-existing",
    )
    captured: dict[str, object] = {}

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        captured["arguments"] = list(arguments)
        captured["input_text"] = input_text
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {"result": "done", "session_id": "claude-existing"}
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    orchestrator.run_claude()

    args = captured["arguments"]
    assert isinstance(args, list)
    prompt = captured["input_text"]
    assert isinstance(prompt, str)
    assert BOOTSTRAP_MARKER not in prompt
    assert "--resume" in args


def test_bootstrap_can_be_disabled_for_new_claude_sessions(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    settings = AIExecutionSettings(auto_session_bootstrap=False)
    orchestrator = Orchestrator(project, settings=settings)
    captured: dict[str, object] = {}

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        captured["arguments"] = list(arguments)
        captured["input_text"] = input_text
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {"result": "done", "session_id": "claude-no-bootstrap"}
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    orchestrator.run_claude()

    args = captured["arguments"]
    assert isinstance(args, list)
    prompt = captured["input_text"]
    assert isinstance(prompt, str)
    assert BOOTSTRAP_MARKER not in prompt


def test_codex_command_uses_explicit_model_effort_and_read_only(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    settings = AIExecutionSettings(
        codex_model="gpt-5.5",
        codex_effort="medium",
        codex_verbosity="low",
    )
    orchestrator = Orchestrator(project, settings=settings)
    captured: dict[str, object] = {}

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        captured["arguments"] = list(arguments)
        return CliResult(
            command="codex",
            success=True,
            return_code=0,
            stdout=(
                '{"type":"thread.started","thread_id":"codex-1"}\n'
                '{"type":"item.completed","item":'
                '{"type":"agent_message","text":"plan"}}\n'
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    result = orchestrator.run_codex_plan(
        task="規劃一個功能。",
        round_number=0,
        final_round=True,
    )

    args = captured["arguments"]
    assert isinstance(args, list)
    assert args[args.index("-m") + 1] == "gpt-5.5"
    assert 'model_reasoning_effort="medium"' in args
    assert 'model_verbosity="low"' in args
    assert args[args.index("-s") + 1] == "read-only"
    assert result.stdout == "plan"


def test_new_codex_session_prompt_includes_bootstrap(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    orchestrator = Orchestrator(project)
    captured: dict[str, object] = {}

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        captured["arguments"] = list(arguments)
        captured["input_text"] = input_text
        return CliResult(
            command="codex",
            success=True,
            return_code=0,
            stdout=(
                '{"type":"thread.started","thread_id":"codex-new"}\n'
                '{"type":"item.completed","item":'
                '{"type":"agent_message","text":"plan"}}\n'
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    orchestrator.run_codex_plan("Plan Root Admin policy.", final_round=True)

    prompt = captured["input_text"]
    assert isinstance(prompt, str)
    assert BOOTSTRAP_MARKER in prompt
    assert "AI Control Center Session Bootstrap" in prompt
    assert (project / ".aicc" / "knowledge" / "SESSION_BOOTSTRAP.md").is_file()



def test_codex_resume_places_exec_options_before_resume_command(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    settings = AIExecutionSettings(
        codex_model="gpt-5.5",
        codex_effort="low",
        codex_verbosity="low",
    )
    session_id = "019fb70a-1902-7b70-b87e-988090719781"
    orchestrator = Orchestrator(
        project,
        settings=settings,
        codex_session_id=session_id,
    )
    captured: dict[str, object] = {}

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        captured["arguments"] = list(arguments)
        captured["input_text"] = input_text
        return CliResult(
            command="codex",
            success=True,
            return_code=0,
            stdout=(
                '{"type":"thread.started","thread_id":"'
                + session_id
                + '"}\n'
                '{"type":"item.completed","item":'
                '{"type":"agent_message","text":"final plan"}}\n'
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    result = orchestrator.run_codex_plan(
        task="規劃一個功能。",
        previous_plan="初稿",
        claude_feedback="實作建議",
        round_number=1,
        final_round=True,
    )

    args = captured["arguments"]
    assert isinstance(args, list)

    resume_index = args.index("resume")
    sandbox_index = args.index("-s")
    json_index = args.index("--json")
    model_index = args.index("-m")

    assert model_index < resume_index
    assert sandbox_index < resume_index
    assert json_index < resume_index
    assert args[resume_index + 1] == session_id
    assert args[resume_index + 2] == "-"
    assert 'model_reasoning_effort="low"' in args
    assert 'model_verbosity="low"' in args
    assert captured["input_text"] is not None
    assert BOOTSTRAP_MARKER not in str(
        captured["input_text"]
    )
    assert result.stdout == "final plan"


def test_claude_command_uses_auto_permission_and_long_timeout(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    settings = AIExecutionSettings(
        workflow_mode="development",
        claude_model="sonnet",
        claude_effort="medium",
        claude_permission_mode="auto",
        claude_timeout_minutes=180,
        claude_max_turns=40,
    )
    orchestrator = Orchestrator(project, settings=settings)
    captured: dict[str, object] = {}

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        captured["arguments"] = list(arguments)
        captured["timeout_seconds"] = timeout_seconds
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {"result": "done", "session_id": "claude-long"}
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    orchestrator.run_claude()

    args = captured["arguments"]
    assert isinstance(args, list)
    assert args[args.index("--permission-mode") + 1] == "auto"
    assert args[args.index("--max-turns") + 1] == "40"
    assert captured["timeout_seconds"] == 180 * 60


def test_claude_unlimited_settings_omit_turn_limit_and_timeout(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    settings = AIExecutionSettings(
        workflow_mode="development",
        claude_timeout_minutes=0,
        claude_max_turns=0,
    )
    orchestrator = Orchestrator(project, settings=settings)
    captured: dict[str, object] = {}

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        captured["arguments"] = list(arguments)
        captured["timeout_seconds"] = timeout_seconds
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {"result": "done", "session_id": "claude-unlimited"}
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    orchestrator.run_claude()

    args = captured["arguments"]
    assert isinstance(args, list)
    assert "--max-turns" not in args
    assert captured["timeout_seconds"] is None


def test_multiline_claude_question_preserves_options() -> None:
    output = (
        "已完成前置檢查。\n\n"
        "ASK_USER:\n"
        "問題：要採用哪一種資料庫？\n"
        "選項：\n"
        "A. SQLite\n"
        "B. PostgreSQL\n"
        "建議：目前先用 SQLite。\n"
        "END_ASK_USER\n"
        "這段不應包含"
    )

    question = Orchestrator.parse_claude_question(output)

    assert "問題：要採用哪一種資料庫？" in question
    assert "A. SQLite" in question
    assert "B. PostgreSQL" in question
    assert "這段不應包含" not in question


def test_memory_maintenance_writes_reviewed_shared_memory(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    settings = AIExecutionSettings(
        workflow_mode="development",
        auto_memory_maintenance=True,
    )
    orchestrator = Orchestrator(project, settings=settings)
    calls: list[list[str]] = []

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        del timeout_seconds, input_text
        calls.append(list(arguments))
        if arguments[0] == "codex":
            return CliResult(
                command="codex",
                success=True,
                return_code=0,
                stdout=(
                    '{"type":"thread.started","thread_id":"codex-memory"}\n'
                    '{"type":"item.completed","item":'
                    '{"type":"agent_message","text":"'
                    '## PROJECT_BRIEF.md\\n完成 subtract。\\n\\n'
                    '## ARCHITECTURE.md\\n單檔 demo。\\n\\n'
                    '## DECISIONS.md\\n維持 pytest。\\n\\n'
                    '## NEXT_STEPS.md\\n無。\\n\\n'
                    '## OPEN_QUESTIONS.md\\n無。"}}\n'
                ),
                stderr="",
            )
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {
                    "result": json.dumps(
                        {
                            "status": "PASS",
                            "summary": "可寫入",
                            "memory_update": "",
                            "question": "",
                        },
                        ensure_ascii=False,
                    ),
                    "session_id": "claude-memory",
                },
                ensure_ascii=False,
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]

    result = orchestrator.maintain_memory_after_pass(
        claude_result=CliResult("claude", True, 0, "done", ""),
        codex_review=CliResult("codex", True, 0, '{"status":"PASS"}', ""),
        git_diff="diff --git a/demo.py b/demo.py",
        changed_files=["demo.py"],
    )

    assert result["status"] == "UPDATED"
    assert result["files"] == [
        "PROJECT_BRIEF.md",
        "ARCHITECTURE.md",
        "DECISIONS.md",
        "NEXT_STEPS.md",
        "OPEN_QUESTIONS.md",
    ]
    memory = orchestrator.knowledge.list_memory_files()
    assert memory["PROJECT_BRIEF.md"] == "完成 subtract。"
    assert memory["DECISIONS.md"] == "維持 pytest。"
    assert calls[0][0] == "codex"
    assert calls[1][0] == "claude"


def test_memory_maintenance_can_escalate_to_user(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    orchestrator = Orchestrator(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=True,
        ),
    )

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        del timeout_seconds, input_text
        if arguments[0] == "codex":
            return CliResult(
                command="codex",
                success=True,
                return_code=0,
                stdout=(
                    '{"type":"thread.started","thread_id":"codex-memory"}\n'
                    '{"type":"item.completed","item":'
                    '{"type":"agent_message","text":"'
                    '## DECISIONS.md\\n改用外部服務。"}}\n'
                ),
                stderr="",
            )
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {
                    "result": json.dumps(
                        {
                            "status": "ASK_USER",
                            "summary": "涉及外部服務",
                            "memory_update": "",
                            "question": "是否允許改用外部服務？",
                        },
                        ensure_ascii=False,
                    ),
                    "session_id": "claude-memory",
                },
                ensure_ascii=False,
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]

    result = orchestrator.maintain_memory_after_pass(
        claude_result=CliResult("claude", True, 0, "done", ""),
        codex_review=CliResult("codex", True, 0, '{"status":"PASS"}', ""),
        git_diff="diff",
        changed_files=["settings.py"],
    )

    assert result["status"] == "ASK_USER"
    assert result["question"] == "是否允許改用外部服務？"
    assert orchestrator.knowledge.list_memory_files() == {}


def test_memory_maintenance_skips_low_impact_files(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    orchestrator = Orchestrator(
        project,
        settings=AIExecutionSettings(auto_memory_maintenance=True),
    )

    result = orchestrator.maintain_memory_after_pass(
        claude_result=CliResult("claude", True, 0, "ok", ""),
        codex_review=CliResult("codex", True, 0, "PASS", ""),
        git_diff="",
        changed_files=["tests/test_demo.py", "ROADMAP.md"],
    )

    assert result["status"] == "SKIPPED"
    assert "low-impact" in result["summary"]


def test_memory_maintenance_blocks_secret_like_update(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    orchestrator = Orchestrator(
        project,
        settings=AIExecutionSettings(auto_memory_maintenance=True),
    )

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        del timeout_seconds, input_text
        if arguments[0] == "codex":
            return CliResult(
                command="codex",
                success=True,
                return_code=0,
                stdout=(
                    "## DECISIONS.md\n"
                    "Store token=sk-thisshouldnotleak123456\n"
                ),
                stderr="",
            )
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {
                    "result": json.dumps(
                        {
                            "status": "PASS",
                            "summary": "looks ok",
                            "memory_update": "",
                            "question": "",
                        },
                        ensure_ascii=False,
                    ),
                    "session_id": "claude-memory",
                },
                ensure_ascii=False,
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    result = orchestrator.maintain_memory_after_pass(
        claude_result=CliResult("claude", True, 0, "ok", ""),
        codex_review=CliResult("codex", True, 0, "PASS", ""),
        git_diff="diff",
        changed_files=["app/demo.py"],
    )

    assert result["status"] == "ASK_USER"
    assert "possible-secret" in result["summary"]
    assert orchestrator.knowledge.list_memory_files() == {}


def test_decision_gate_can_auto_answer_claude_question(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    orchestrator = Orchestrator(project)
    calls: list[list[str]] = []

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        del timeout_seconds, input_text
        calls.append(list(arguments))
        if arguments[0] == "codex":
            return CliResult(
                command="codex",
                success=True,
                return_code=0,
                stdout=(
                    '{"type":"thread.started","thread_id":"codex-gate"}\n'
                    '{"type":"item.completed","item":'
                    '{"type":"agent_message","text":"'
                    '{\\"decision\\":\\"APPROVED\\",'
                    '\\"reason\\":\\"低風險測試指令\\",'
                    '\\"answer\\":\\"可以執行 pytest\\",'
                    '\\"question\\":\\"\\"}"}}\n'
                ),
                stderr="",
            )
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {"result": "已依 Codex 指示繼續", "session_id": "claude-gate"},
                ensure_ascii=False,
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]

    result = orchestrator.resolve_claude_question_with_codex(
        "是否可以執行 pytest？"
    )

    assert result["action"] == "CONTINUE"
    assert result["claude_result"].stdout == "已依 Codex 指示繼續"
    assert calls[0][0] == "codex"
    assert calls[1][0] == "claude"


def test_decision_gate_escalates_risky_question(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    orchestrator = Orchestrator(project)

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        del timeout_seconds, input_text
        return CliResult(
            command="codex",
            success=True,
            return_code=0,
            stdout=(
                '{"type":"thread.started","thread_id":"codex-gate"}\n'
                '{"type":"item.completed","item":'
                '{"type":"agent_message","text":"'
                '{\\"decision\\":\\"ASK_USER\\",'
                '\\"reason\\":\\"涉及資料庫結構\\",'
                '\\"answer\\":\\"\\",'
                '\\"question\\":\\"是否允許修改 SQLite schema？\\"}"}}\n'
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]

    result = orchestrator.resolve_claude_question_with_codex(
        "要不要修改 SQLite schema？"
    )

    assert result["action"] == "ASK_USER"
    assert result["question"] == "是否允許修改 SQLite schema？"
    assert "資料庫結構" in result["reason"]


def test_planning_prefers_separate_request_file(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    planning_path = project / ".aicc" / "planning-request.md"
    planning_path.write_text("只規劃，不改程式。\n", encoding="utf-8")
    orchestrator = Orchestrator(
        project,
        settings=AIExecutionSettings(workflow_mode="planning"),
    )

    assert orchestrator.task_path == planning_path
