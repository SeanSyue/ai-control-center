from app.ui.session_panel import SessionTimelinePanel


def test_title_is_extracted_from_aicc_task_wrapper() -> None:
    content = (
        "你是此專案的開發工程師。\n"
        "請完成以下任務：\n"
        "# 通知測試任務\n"
        "修改 demo.py 的空字串處理。"
    )

    title = SessionTimelinePanel._title_from_content(
        content,
        "claude",
    )

    assert title == "通知測試任務"


def test_title_is_extracted_from_json_review_package() -> None:
    content = (
        '{"task":"# 黑底花紋設計\\n'
        '請檢查簡報底圖設計。","test_output":"PASS"}'
    )

    title = SessionTimelinePanel._title_from_content(
        content,
        "codex",
    )

    assert title == "黑底花紋設計"


def test_long_boilerplate_is_not_readable_title() -> None:
    assert not SessionTimelinePanel._is_readable_title(
        "你是此專案的開發工程師。請完成以下任務：修改 demo.py",
        "session-1",
    )


def test_session_panel_explains_pairing_and_title_source() -> None:
    import inspect

    from app.i18n import STRINGS

    source = inspect.getsource(SessionTimelinePanel._build_ui)
    details = inspect.getsource(SessionTimelinePanel._selection_detail)

    assert "sessions.title" in source
    assert "官方名稱" in STRINGS["sessions.help"]
    assert "sessions.scan_all" in source
    assert "名稱來源" in details
