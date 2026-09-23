import json

from app.adapters.cli_runner import CliResult
from app.core.orchestrator import Orchestrator


def test_normalize_claude_json_extracts_session_and_text() -> None:
    raw = CliResult(
        command="claude",
        success=True,
        return_code=0,
        stdout=json.dumps(
            {
                "type": "result",
                "session_id": "claude-session",
                "result": "Completed",
            }
        ),
        stderr="",
    )

    result = Orchestrator._normalize_claude_result(raw)

    assert result.session_id == "claude-session"
    assert result.stdout == "Completed"


def test_normalize_codex_jsonl_extracts_thread_and_final_message() -> None:
    stream = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": '{"status":"PASS","summary":"ok","issues":[],"instructions":[],"question":""}',
                    },
                }
            ),
        ]
    )
    raw = CliResult(
        command="codex",
        success=True,
        return_code=0,
        stdout=stream,
        stderr="",
    )

    result = Orchestrator._normalize_codex_result(raw)

    assert result.session_id == "thread-1"
    assert '"status":"PASS"' in result.stdout
