import json
from pathlib import Path

from app.services.session_scanner import SessionScanner
from app.services.session_scanner import SessionInfo


def _write_session_index(appdata: Path, **fields) -> Path:
    """One entry of the Claude desktop app's sidebar index.

    Layout reverse-engineered from app 2.1.221:
    %APPDATA%\\Claude\\claude-code-sessions\\<org>\\<workspace>\\local_<uuid>.json
    """
    folder = appdata / "Claude" / "claude-code-sessions" / "org-1" / "ws-1"
    folder.mkdir(parents=True, exist_ok=True)
    entry = {
        "sessionId": "local_" + str(fields.get("cliSessionId", "x")),
        "titleSource": "user",
        "createdAt": 1786109387725,
        "lastActivityAt": 1786182926862,
        "model": "claude-opus-5",
        "isArchived": False,
        **fields,
    }
    path = folder / f"local_{entry['cliSessionId']}.json"
    path.write_text(json.dumps(entry), encoding="utf-8")
    return path


def _write_codex_threads_db(codex_home: Path, rows: list[dict], columns=None) -> Path:
    """A stand-in for Codex Desktop's ~/.codex/state_<n>.sqlite `threads` table."""
    import sqlite3

    columns = columns or [
        "id", "title", "cwd", "rollout_path",
        "created_at_ms", "updated_at_ms", "archived", "is_pinned",
    ]
    codex_home.mkdir(parents=True, exist_ok=True)
    db = codex_home / "state_5.sqlite"
    con = sqlite3.connect(db)
    # WAL, like the real one. It matters for the read-only test below: opening
    # a WAL database - even read-only - makes SQLite create a -shm beside it,
    # which is exactly the side effect that must not land in Codex's folder.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"CREATE TABLE threads ({', '.join(columns)})")
    for row in rows:
        values = [row.get(c) for c in columns]
        con.execute(
            f"INSERT INTO threads ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' * len(columns))})",
            values,
        )
    con.commit()
    con.close()
    return db


def _write_codex_transcript(codex_home: Path, session_id: str, cwd: Path, prompt: str) -> Path:
    path = codex_home / "sessions" / f"rollout-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "timestamp": "2026-08-08T05:33:39.324Z",
                "payload": {"session_id": session_id, "id": session_id, "cwd": str(cwd)},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {"role": "user", "content": prompt},
            }
        ),
        encoding="utf-8",
    )
    return path


def _isolate_codex_home(tmp_path: Path, monkeypatch) -> Path:
    codex_home = tmp_path / "codex"
    codex_home.mkdir(exist_ok=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    # `codex` is not on PATH in CI, so scan_codex takes the local-file path.
    # Forced rather than assumed: on a machine that does have it, these tests
    # would otherwise be exercising the app-server instead.
    monkeypatch.setattr(
        "app.services.session_scanner.CodexAppServerClient.list_threads",
        lambda self, cwd: (_ for _ in ()).throw(OSError("codex not in PATH")),
    )
    return codex_home


def _isolate_appdata(tmp_path: Path, monkeypatch) -> Path:
    """Point APPDATA somewhere empty.

    Without this the scanner reads the developer's real session index while the
    tests run. Nothing failed because of it - the fake session ids here match
    none of the real ones - but a test whose result depends on what is in the
    author's own Claude sidebar is not isolated, it is lucky.
    """
    appdata = tmp_path / "appdata"
    appdata.mkdir(exist_ok=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    return appdata


def test_scanner_parses_claude_jsonl(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    history = home / ".claude" / "projects" / "encoded" / "session-1.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "sessionId": "session-1",
                        "cwd": str(project),
                        "timestamp": "2026-07-31T01:00:00+00:00",
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": "Fix the importer",
                        },
                    }
                ),
                json.dumps(
                    {
                        "sessionId": "session-1",
                        "cwd": str(project),
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Done"}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    scanner = SessionScanner()
    sessions = scanner.scan_claude(project)
    messages = scanner.load_claude_messages("session-1", project)

    assert sessions[0].session_id == "session-1"
    assert sessions[0].title == "Fix the importer"
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[-1].content == "Done"


def test_scanner_prefers_named_claude_session_over_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    history = home / ".claude" / "projects" / "encoded" / "session-2.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(
        json.dumps(
            {
                "sessionId": "session-2",
                "cwd": str(project),
                "sessionName": "KYC MVP程式開發3",
                "timestamp": "2026-07-31T01:00:00+00:00",
                "type": "user",
                "message": {
                    "role": "user",
                    "content": (
                        "你是此專案的開發工程師。\n"
                        "請完成以下任務：\n"
                        "整理文件索引。"
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    scanner = SessionScanner()
    sessions = scanner.scan_claude(project)

    assert sessions[0].title == "KYC MVP程式開發3"
    assert sessions[0].metadata["title_source"] == "official"


def test_scanner_can_include_claude_sessions_from_other_projects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    current_project = tmp_path / "current"
    other_project = tmp_path / "other"
    current_project.mkdir()
    other_project.mkdir()
    history = home / ".claude" / "projects" / "encoded" / "session-3.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(
        json.dumps(
            {
                "sessionId": "session-3",
                "cwd": str(other_project),
                "sessionName": "舊的 Claude 對話",
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Continue the existing work",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    scanner = SessionScanner()

    assert scanner.scan_claude(current_project) == []
    sessions = scanner.scan_claude(current_project, include_all=True)

    assert sessions[0].session_id == "session-3"
    assert sessions[0].title == "舊的 Claude 對話"
    assert sessions[0].metadata["scan_scope"] == "all"


def test_scanner_uses_claude_task_subject_for_derived_desktop_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    session_id = "session-desktop"
    sessions_dir = home / ".claude" / "sessions"
    tasks_dir = home / ".claude" / "tasks" / session_id
    sessions_dir.mkdir(parents=True)
    tasks_dir.mkdir(parents=True)
    (sessions_dir / "2012.json").write_text(
        json.dumps(
            {
                "pid": 2012,
                "sessionId": session_id,
                "cwd": str(project),
                "startedAt": "1785746527429",
                "entrypoint": "claude-desktop",
                "name": "claude-33",
                "nameSource": "derived",
            }
        ),
        encoding="utf-8",
    )
    (tasks_dir / "12.json").write_text(
        json.dumps(
            {
                "id": "12",
                "subject": "Broader UX review pass",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    scanner = SessionScanner()
    sessions = scanner.scan_claude(project)

    assert sessions[0].session_id == session_id
    assert sessions[0].title == "Broader UX review pass"
    assert sessions[0].source == "claude-session"
    assert sessions[0].metadata["title_source"] == "task_subject"


def test_scanner_takes_the_title_from_the_desktop_session_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Measured 2026-08-07: the title was wrong for every real session on this
    # machine. Reading the first 160 lines of a transcript and keeping the
    # first title-ish value it finds cannot be made right by adding keys -
    # Claude Code re-emits the title throughout the file and the *last* one is
    # authoritative (one 9,000-line transcript carried 655 of them).
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    appdata = _isolate_appdata(tmp_path, monkeypatch)

    history = home / ".claude" / "projects" / "encoded" / "abc-123.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(
        json.dumps(
            {
                "sessionId": "abc-123",
                "cwd": str(project),
                "timestamp": "2026-07-31T01:00:00+00:00",
                "type": "user",
                "message": {"role": "user", "content": "幫我看看這個 roadmap 和…"},
            }
        ),
        encoding="utf-8",
    )
    _write_session_index(appdata, cliSessionId="abc-123", title="系統搬遷", cwd=str(project))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    sessions = SessionScanner().scan_claude(project)

    assert sessions[0].title == "系統搬遷"
    assert sessions[0].metadata["title_source"] == "session_index"


def test_session_index_beats_a_derived_process_handle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # ~/.claude/sessions/<pid>.json is a live-process registry, and its `name`
    # is a handle. One real session showed as `aicc-4a` and was ranked
    # `official`, so it outranked the conversation's actual name.
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    appdata = _isolate_appdata(tmp_path, monkeypatch)

    sessions_dir = home / ".claude" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "4021.json").write_text(
        json.dumps(
            {
                "pid": 4021,
                "sessionId": "abc-456",
                "cwd": str(project),
                "entrypoint": "claude-desktop",
                "name": "aicc-4a",
                "nameSource": "derived",
            }
        ),
        encoding="utf-8",
    )
    _write_session_index(appdata, cliSessionId="abc-456", title="系統開發", cwd=str(project))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    sessions = SessionScanner().scan_claude(project)

    assert sessions[0].title == "系統開發"
    assert sessions[0].metadata["title_source"] == "session_index"


def test_derived_names_are_never_treated_as_user_titles() -> None:
    # The old guard only rejected `derived` when the text also matched
    # `claude-<digits>` - a guess at what handles look like, and `aicc-4a`
    # walked straight through it. The file states the provenance; the shape of
    # the string is not evidence.
    is_user = SessionScanner._is_user_named_claude_title

    assert is_user("aicc-4a", "derived") is False
    assert is_user("claude-33", "derived") is False
    assert is_user("任何名字", "derived") is False
    assert is_user("系統開發", "user") is True
    assert is_user("", "user") is False


def test_scanner_still_reads_the_transcript_when_there_is_no_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # The index is reverse-engineered and undocumented. If it moves, goes away,
    # or has no entry for a session, scanning has to carry on exactly as before
    # rather than losing the conversation.
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    _isolate_appdata(tmp_path, monkeypatch)   # empty: no index at all

    history = home / ".claude" / "projects" / "encoded" / "abc-789.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(
        json.dumps(
            {
                "sessionId": "abc-789",
                "cwd": str(project),
                "timestamp": "2026-07-31T01:00:00+00:00",
                "type": "user",
                "message": {"role": "user", "content": "Fix the importer"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    sessions = SessionScanner().scan_claude(project)

    assert sessions[0].title == "Fix the importer"
    assert sessions[0].metadata["title_source"] == "message_preview"


def test_an_unreadable_index_entry_does_not_take_scanning_down(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    appdata = _isolate_appdata(tmp_path, monkeypatch)

    folder = appdata / "Claude" / "claude-code-sessions" / "org-1" / "ws-1"
    folder.mkdir(parents=True)
    (folder / "local_broken.json").write_text("{not json", encoding="utf-8")
    (folder / "local_empty.json").write_text(json.dumps({"cliSessionId": "abc-999"}), encoding="utf-8")
    _write_session_index(appdata, cliSessionId="abc-999", title="真的標題", cwd=str(project))

    history = home / ".claude" / "projects" / "encoded" / "abc-999.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(
        json.dumps(
            {
                "sessionId": "abc-999",
                "cwd": str(project),
                "timestamp": "2026-07-31T01:00:00+00:00",
                "type": "user",
                "message": {"role": "user", "content": "prompt preview"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    sessions = SessionScanner().scan_claude(project)

    assert sessions[0].title == "真的標題"


def test_codex_title_comes_from_the_desktop_thread_list(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Codex transcripts carry no title at all - the only record types are
    # session_meta, event_msg and response_item, none with a title field - so
    # the first user message was not a shortcut, it was the only thing there.
    # That is why a bare Windows path was showing up as a conversation name.
    project = tmp_path / "project"
    project.mkdir()
    codex_home = _isolate_codex_home(tmp_path, monkeypatch)
    session_id = "019fdfdc-d41a-7bb1-bec0-1f867344bfa9"

    _write_codex_transcript(
        codex_home, session_id, project, "C:\\Users\\example\\Claude\\AICC Read 我把系統…"
    )
    _write_codex_threads_db(
        codex_home,
        [
            {
                "id": session_id,
                "title": "系統開發",
                "cwd": f"\\\\?\\{project}",
                "rollout_path": str(codex_home / "sessions" / f"rollout-{session_id}.jsonl"),
                "created_at_ms": 1786109391000,
                "updated_at_ms": 1786167123000,
                "archived": 0,
                "is_pinned": 0,
            }
        ],
    )

    sessions = SessionScanner().scan_codex(project)

    assert sessions[0].title == "系統開發"
    assert sessions[0].metadata["title_source"] == "session_index"


def test_codex_extended_length_cwd_still_matches_the_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Codex stores cwd as `\\?\C:\...`. Compared literally against a project
    # path it never matches, so every Codex conversation would look like it
    # belongs to some other project and be filtered out of the default view.
    project = tmp_path / "project"
    project.mkdir()
    codex_home = _isolate_codex_home(tmp_path, monkeypatch)
    session_id = "019fdfdc-aaaa-bbbb-cccc-ddddeeeeffff"

    # No cwd in the transcript, so the only source for it is the index.
    path = codex_home / "sessions" / f"rollout-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"session_id": session_id}}),
        encoding="utf-8",
    )
    _write_codex_threads_db(
        codex_home,
        [{"id": session_id, "title": "頁面設計", "cwd": f"\\\\?\\{project}",
          "rollout_path": str(path), "created_at_ms": 1, "updated_at_ms": 2,
          "archived": 0, "is_pinned": 0}],
    )

    sessions = SessionScanner().scan_codex(project)   # project-scoped, not include_all

    assert [s.title for s in sessions] == ["頁面設計"]


def test_codex_falls_back_to_the_prompt_when_the_thread_list_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    codex_home = _isolate_codex_home(tmp_path, monkeypatch)
    _write_codex_transcript(codex_home, "no-index-1", project, "Fix the importer")

    sessions = SessionScanner().scan_codex(project)

    assert sessions[0].title == "Fix the importer"
    assert sessions[0].metadata["title_source"] == "message_preview"


def test_codex_thread_list_survives_a_narrower_schema(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # The table belongs to a running application and its layout is
    # reverse-engineered, so the columns read are intersected with what the
    # table actually has. Selecting the full wish-list instead would raise on
    # any schema that has dropped one - and the title would be lost along with
    # the columns that were never needed for it.
    project = tmp_path / "project"
    project.mkdir()
    codex_home = _isolate_codex_home(tmp_path, monkeypatch)
    _write_codex_transcript(codex_home, "schema-1", project, "Fix the importer")
    _write_codex_threads_db(
        codex_home,
        [{"id": "schema-1", "title": "窄表也要能讀"}],
        columns=["id", "title"],        # no cwd, rollout_path or timestamps
    )

    sessions = SessionScanner().scan_codex(project)

    assert sessions[0].title == "窄表也要能讀"


def test_codex_thread_list_without_a_title_column_degrades_quietly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    codex_home = _isolate_codex_home(tmp_path, monkeypatch)
    _write_codex_transcript(codex_home, "schema-2", project, "Fix the importer")
    _write_codex_threads_db(
        codex_home,
        [{"id": "schema-2", "cwd": str(project)}],
        columns=["id", "cwd"],          # no `title` column at all
    )

    sessions = SessionScanner().scan_codex(project)

    assert sessions[0].title == "Fix the importer"


def test_reading_the_codex_thread_list_does_not_touch_codex_own_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # It belongs to a running app. The read goes through a copy, and this is
    # the assertion that keeps it that way - opening the original read-only
    # still lets SQLite create a -shm beside it.
    project = tmp_path / "project"
    project.mkdir()
    codex_home = _isolate_codex_home(tmp_path, monkeypatch)
    _write_codex_transcript(codex_home, "ro-1", project, "prompt")
    db = _write_codex_threads_db(
        codex_home,
        [{"id": "ro-1", "title": "唯讀測試", "cwd": str(project), "rollout_path": "",
          "created_at_ms": 1, "updated_at_ms": 2, "archived": 0, "is_pinned": 0}],
    )
    before = db.read_bytes()
    siblings_before = sorted(p.name for p in codex_home.iterdir())

    sessions = SessionScanner().scan_codex(project)

    assert sessions[0].title == "唯讀測試"
    assert db.read_bytes() == before, "Codex 的資料庫被改動了"
    assert sorted(p.name for p in codex_home.iterdir()) == siblings_before, (
        "在 Codex 的資料夾旁邊留下了檔案"
    )


def test_session_index_outranks_every_other_title_source() -> None:
    rank = SessionScanner._title_quality_rank

    def item(source: str) -> SessionInfo:
        return SessionInfo(
            provider="claude",
            session_id="s",
            title="t",
            project_path="",
            created_at="",
            updated_at="",
            source="claude-local",
            metadata={"title_source": source},
        )

    assert rank(item("session_index")) > rank(item("official"))
    assert rank(item("official")) > rank(item("message_preview"))


def test_load_claude_messages_falls_back_to_task_preview(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    session_id = "task-only-session"
    tasks_dir = home / ".claude" / "tasks" / session_id
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "1.json").write_text(
        json.dumps(
            {
                "id": "1",
                "subject": "Add contract file attachments",
                "description": "Add contract_attachments table.",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    messages = SessionScanner().load_claude_messages(session_id, project)

    assert messages[0].role == "user"
    assert "Add contract file attachments" in messages[0].content
    assert "completed" in messages[0].content


def test_deduplicate_prefers_better_title_over_newer_prompt() -> None:
    older_named = SessionInfo(
        provider="claude",
        session_id="same-session",
        title="Broader UX review pass",
        updated_at="2026-08-03T01:00:00+00:00",
        metadata={"title_source": "task_subject"},
    )
    newer_prompt = SessionInfo(
        provider="claude",
        session_id="same-session",
        title="你是此專案的實作顧問。現在是計畫發想階段...",
        updated_at="2026-08-03T02:00:00+00:00",
        metadata={"title_source": "message_preview"},
    )

    result = SessionScanner._deduplicate_sessions([newer_prompt, older_named])

    assert result[0].title == "Broader UX review pass"


def test_scanner_finds_codex_thread_name() -> None:
    scanner = SessionScanner()

    title = scanner._find_title(
        {
            "id": "thread-1",
            "name": "",
            "thread": {"conversationTitle": "提案簡報流程"},
            "preview": "你是此專案的 reviewer",
        }
    )

    assert title == "提案簡報流程"
