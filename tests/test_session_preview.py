from app.services.session_scanner import SessionMessage
from app.ui.session_panel import SessionTimelinePanel


def test_preview_extracts_task_from_aicc_wrapper() -> None:
    messages = [
        SessionMessage(
            provider="claude",
            session_id="abc123",
            role="user",
            content=(
                "你是此專案的開發工程師。請完成以下任務：\n"
                "# 任務\n修改 demo.py 的空字串處理。"
            ),
        ),
        SessionMessage(
            provider="claude",
            session_id="abc123",
            role="assistant",
            content="已完成修改並通過測試。",
        ),
    ]

    preview = SessionTimelinePanel._render_session_preview(
        provider="claude",
        title="未命名的 Claude 對話",
        stamp="2026/07/31 12:00",
        session_id="abc123",
        messages=messages,
    )

    assert "修改 demo.py 的空字串處理" in preview
    assert "已完成修改並通過測試" in preview
    assert "你是此專案的開發工程師" not in preview


def test_preview_handles_empty_session() -> None:
    preview = SessionTimelinePanel._render_session_preview(
        provider="codex",
        title="未命名的 Codex 審查",
        stamp="時間不明",
        session_id="codex123",
        messages=[],
    )

    assert "找不到可讀取的對話內容" in preview
