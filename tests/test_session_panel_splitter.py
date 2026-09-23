from __future__ import annotations

import inspect

from app.ui.session_panel import SessionTimelinePanel


def test_session_panel_has_draggable_vertical_splitter() -> None:
    source = inspect.getsource(SessionTimelinePanel._build_ui)

    assert "self.main_splitter = QSplitter(Qt.Vertical)" in source
    assert "self.main_splitter.addWidget(session_card)" in source
    assert "self.main_splitter.addWidget(timeline_pane)" in source
    assert "self.main_splitter.setHandleWidth(10)" in source
    assert "self.timeline_output.setVerticalScrollBarPolicy" in source
