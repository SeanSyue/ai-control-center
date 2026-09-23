from __future__ import annotations

import inspect

from app.ui.session_panel import SessionTimelinePanel


def test_preview_uses_current_title_resolver() -> None:
    source = inspect.getsource(
        SessionTimelinePanel._session_preview_text
    )

    assert "_resolved_display_title" in source
    assert "_clean_display_title" not in source
