from __future__ import annotations

from pathlib import Path

from app.services.conversation_search import (
    ConversationHit,
    ConversationSearchService,
    normalize_title,
)
from app.services.session_scanner import SessionInfo, SessionMessage


class FakeScanner:
    def scan_claude(self, project_path: Path, include_all: bool = False):
        return [
            SessionInfo(
                provider="claude",
                session_id="claude-1",
                title="你是此專案的開發工程師。AI Control Center wrapper prompt",
                project_path=str(project_path),
                updated_at="2026-08-03T10:00:00+08:00",
                source="claude-local",
                metadata={"title_source": "message_preview"},
            )
        ]

    def scan_codex(self, project_path: Path, include_all: bool = False):
        return [
            SessionInfo(
                provider="codex",
                session_id="codex-1",
                title="KYC MVP 程式開發3",
                project_path="",
                updated_at="2026-08-03T11:00:00+08:00",
                source="codex-app-server",
                metadata={"title_source": "official"},
            )
        ]

    def load_claude_messages(self, session_id: str, project_path: Path):
        return [
            SessionMessage(
                provider="claude",
                session_id=session_id,
                role="user",
                content="任務：重構 KYC 權限流程。限制：不要改資料庫 schema。",
            )
        ]

    def load_codex_messages(self, session_id: str, project_path: Path):
        return [
            SessionMessage(
                provider="codex",
                session_id=session_id,
                role="user",
                content="Please review Root Admin permission policy.",
            )
        ]


def test_conversation_search_collects_all_providers(tmp_path: Path) -> None:
    service = ConversationSearchService(scanner=FakeScanner())  # type: ignore[arg-type]

    hits = service.search(tmp_path, provider="all", query="", limit=20)

    assert [hit.provider for hit in hits] == ["codex", "claude"]
    assert hits[0].display_title == "KYC MVP 程式開發3"
    assert hits[1].display_title == "重構 KYC 權限流程"
    assert hits[0].source_label == "Codex Desktop"
    assert hits[1].resume_status == "可續接"


def test_conversation_search_filters_by_keyword(tmp_path: Path) -> None:
    service = ConversationSearchService(scanner=FakeScanner())  # type: ignore[arg-type]

    hits = service.search(tmp_path, provider="all", query="Root Admin", limit=20)

    assert len(hits) == 1
    assert hits[0].provider == "codex"


def test_render_preview_includes_metadata_and_messages(tmp_path: Path) -> None:
    service = ConversationSearchService(scanner=FakeScanner())  # type: ignore[arg-type]
    hit = ConversationHit(
        provider="claude",
        title="",
        display_title="重構 KYC 權限流程",
        session_id="claude-1",
        updated_at="2026-08-03T10:00:00+08:00",
        project_path=str(tmp_path),
        source="claude-local",
        title_source="message_preview",
        resume_status="可續接",
        score=1,
        snippet="重構 KYC 權限流程",
    )

    preview = service.render_preview(hit, tmp_path)

    # 2026-08-24：廠牌顯示名收成一份（`app/services/providers.py`）之後，
    # 這裡是「Claude Code」不是「Claude」——`conversation_search` 本來自己
    # 寫了一份 `provider_name()` 回短名，那是那個名字的**第四份**。
    from app.services.providers import display_name

    assert f"{display_name('claude')}：重構 KYC 權限流程" in preview
    assert "對話 ID：claude-1" in preview
    assert "使用者:" in preview
    assert "重構 KYC 權限流程" in preview


def test_normalize_title_prefers_official_title() -> None:
    session = SessionInfo(
        provider="codex",
        session_id="thread-1",
        title="提案簡報梳理",
        metadata={"title_source": "official"},
    )

    assert normalize_title(session, []) == "提案簡報梳理"


# ---- AICC 自己的 prompt 不可以被當成使用者寫的對話 -------------------------


def _golden_prompts() -> list[tuple[str, str]]:
    """Every prompt AICC really sends, read off the golden snapshot.

    The snapshot is generated from the orchestrator itself, so this stays true
    as steps are added - which is the whole point. A hand-written list of
    example prompts here would be a third copy of the same strings.
    """
    import re
    from pathlib import Path

    text = Path("tests/golden/orchestrator_prompts.txt").read_text(
        encoding="utf-8"
    )
    blocks = re.split(
        r"={70,}\n# \[\d+\] role=\S+ event_type=(\S+).*?\n={70,}\n", text
    )
    return [
        (blocks[i], blocks[i + 1])
        for i in range(1, len(blocks) - 1, 2)
    ]


def test_every_prompt_aicc_sends_is_recognised_as_its_own() -> None:
    """Otherwise AICC's own run shows up as a conversation the user wrote.

    `normalize_title` falls back to the raw first line when it does not
    recognise a wrapper, so the line-up composer would offer
    「你是此專案的共同記憶審查者。」 as a conversation to pair with. It looks
    like a real entry and there is nothing on screen to say it is not.

    Checked against the golden snapshot rather than a list of examples,
    because a list of examples is a third copy of the same strings and the
    second copy had already drifted: two personas added by ROADMAP P10 were
    missing from the marker list when this test was written.
    """
    from app.services.conversation_search import is_aicc_wrapper

    prompts = _golden_prompts()
    assert prompts, "讀不到金標快照，這條測試就什麼都沒驗到"

    unrecognised = [
        event_type
        for event_type, body in prompts
        if not is_aicc_wrapper(body)
    ]
    assert unrecognised == [], (
        "這些步驟送出去的 prompt 不會被認出是 AICC 自己的，"
        f"它們的第一行會變成對話標題：{unrecognised}"
    )


def test_a_renamed_seat_is_still_recognised_as_aicc() -> None:
    """ROADMAP P10 Stage D lets a user replace the 「你是⋯⋯」 line.

    Every persona marker becomes useless for that seat, so recognition has to
    survive on the body text. Pinned because the failure is silent and only
    shows up as a strange-looking conversation in the composer weeks later.
    """
    from app.core import prompt_builder
    from app.services.conversation_search import is_aicc_wrapper

    renamed = prompt_builder.build_memory_review_prompt(
        task="做一件事",
        memory_update="## PROJECT_BRIEF.md\n草案\n",
        identity="你是這個案子的合規審查員。",
    )

    assert "你是此專案的共同記憶審查者" not in renamed
    assert is_aicc_wrapper(renamed)


def test_a_conversation_the_user_actually_wrote_is_not_swallowed() -> None:
    # The other direction, and the one that costs more: over-matching would
    # hide the user's own conversations from the composer entirely.
    from app.services.conversation_search import is_aicc_wrapper

    assert not is_aicc_wrapper("幫我看一下這個合約的委任條款有沒有問題")
    assert not is_aicc_wrapper("你是誰？")
    assert not is_aicc_wrapper("我想做一個規劃者的角色扮演遊戲")


def test_a_title_that_is_only_the_persona_line_is_recognised() -> None:
    """The shape this function is actually called with in production.

    `normalize_title` passes the **title**, not the prompt - and a scanned
    title is typically the first line, which is the persona and carries none
    of the body phrases. So the persona markers are the load-bearing half
    there, and the earlier tests all passed whole prompts and never touched
    them (negative control caught it: reverting the marker list to the old
    hand-written two left everything green).
    """
    from app.core.prompt_builder import DEFAULT_PERSONAS
    from app.services.conversation_search import is_aicc_wrapper

    for step, persona in DEFAULT_PERSONAS.items():
        assert is_aicc_wrapper(persona.identity), (
            f"「{persona.identity}」被掃到當標題時不會被認出是 AICC 的"
            f"（步驟 {step}）"
        )
        # And as it really arrives - opening line plus whatever rail follows.
        assert is_aicc_wrapper(persona.opening()), step


def test_a_scanned_session_titled_with_a_wrapper_gets_the_task_instead(
    tmp_path,
) -> None:
    """End to end through `normalize_title`, which is the only caller.

    A recognised wrapper makes it look past the title for the real task; an
    unrecognised one becomes the conversation's name in the composer.
    """
    from app.services.conversation_search import normalize_title
    from app.services.session_scanner import SessionInfo, SessionMessage

    session = SessionInfo(
        provider="claude",
        session_id="abc12345",
        title="你是這件事的規劃者。",
        project_path=str(tmp_path),
        updated_at="2026-08-10T01:00:00",
        metadata={"title_source": "local"},
    )
    messages = [
        SessionMessage(
            provider="claude",
            session_id="abc12345",
            role="user",
            content="任務：把設定頁的儲存列修好。",
        )
    ]

    assert normalize_title(session, messages) == "把設定頁的儲存列修好"


def test_every_aicc_prompt_shape_cleans_down_to_the_same_task() -> None:
    """The title a person sees, from all eleven prompt shapes.

    Before this, the same task produced eleven different titles, and most of
    them ran on into whatever section followed:

        review        「\n把設定頁的儲存列修好。\n", "claude_success": true, "c…」
        fix           「任務 把設定頁的儲存列修好。 Codex 審查摘要： 測試未通過。 發現的…」
        user_answer   「任務 把設定頁的儲存列修好。 先前提出的問題： 要不要一併改資料庫結構？…」

    Three separate causes, all of them the same shape of mistake - a list
    somebody had to remember to update:

    * `「任務：」` was matched anywhere in the blob, so it hit the instruction
      sentence 「請完成以下任務：」 instead of the heading below it;
    * the stop-marker list had four entries and AICC's prompts have at least
      six more headings after the task;
    * two prompts carry the task inside a JSON bundle, which no prose scan
      was ever going to survive.
    """
    import re
    from pathlib import Path

    from app.services.conversation_search import extract_task_title

    text = Path("tests/golden/orchestrator_prompts.txt").read_text(
        encoding="utf-8"
    )
    blocks = re.split(
        r"={70,}\n# \[\d+\] role=\S+ event_type=(\S+).*?\n={70,}\n", text
    )
    titles = {
        blocks[i]: extract_task_title(blocks[i + 1])
        for i in range(1, len(blocks) - 1, 2)
    }

    assert len(titles) >= 11, "金標快照讀不完整，這條就什麼都沒驗到"
    assert set(titles.values()) == {"把設定頁的儲存列修好"}, titles


def test_a_task_written_on_the_same_line_still_works() -> None:
    # How a person writes it, as opposed to how AICC's templates do.
    from app.services.conversation_search import extract_task_title

    assert (
        extract_task_title("任務：把合約的委任條款看一下")
        == "把合約的委任條款看一下"
    )


def test_the_title_stops_at_the_next_section() -> None:
    from app.services.conversation_search import extract_task_title

    assert (
        extract_task_title("# 任務\n把設定頁修好\n\n限制：不要動 Python")
        == "把設定頁修好"
    )


def test_a_conversation_with_no_task_marker_keeps_its_own_words() -> None:
    # The fall-through has to stay useful: most conversations in the library
    # are the user's own and have no marker at all.
    from app.services.conversation_search import extract_task_title

    assert (
        extract_task_title("幫我看一下這個合約的委任條款有沒有問題")
        == "幫我看一下這個合約的委任條款有沒有問題"
    )


def test_a_stop_marker_on_the_same_line_leaves_no_stray_full_stop() -> None:
    """How a person writes it: no blank line between the task and its limits.

    Found by negative control - every other case here has a blank line, so the
    block ends before the stop-marker scan matters and the order of "cut" and
    "trim punctuation" made no difference. It makes one here: trimming first
    leaves the 「。」 that was sitting in front of 「限制：」.
    """
    from app.services.conversation_search import extract_task_title

    assert (
        extract_task_title("任務：把設定頁修好。限制：不要動 Python 的部分")
        == "把設定頁修好"
    )
