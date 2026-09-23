from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.providers import display_name
from app.services.session_scanner import (
    SessionInfo,
    SessionMessage,
    SessionScanner,
)


def _persona_markers() -> tuple[str, ...]:
    """Every shipped 「你是⋯⋯」 opening, read from where they are defined.

    Hand-copied before, and it had already drifted: ROADMAP P10 added a
    planner persona and a memory-reviewer persona that this list never heard
    of. The consequence is quiet - an AICC run's own prompt gets mistaken for
    a conversation the user wrote, and shows up in the line-up composer with
    「你是此專案的共同記憶審查者。」 as its title.

    Trailing 「。」 dropped so a match survives an edit to the punctuation.
    """
    from app.core.prompt_builder import DEFAULT_PERSONAS

    return tuple(
        sorted(
            {
                persona.identity.rstrip("。").strip()
                for persona in DEFAULT_PERSONAS.values()
                if persona.identity.strip()
            }
        )
    )


# 這三組標記 2026-08-24 搬去 `app/core/prompt_markers.py` 了：它們是
# **拿來比對 AICC 自己寫過的字**，跟介面文字照不同的理由改——散在兩個檔案
# 裡的結果是改了提示詞只會有一邊跟著改。
from app.core.prompt_markers import STOP_MARKERS, TASK_LEAD_INS  # noqa: E402
from app.core.prompt_markers import WRAPPER_MARKERS as _WRAPPER  # noqa: E402
from app.i18n import t

AICC_WRAPPER_MARKERS = _WRAPPER + _persona_markers()

# Kept under the old name because other modules import it.
TASK_MARKERS = TASK_LEAD_INS

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(
        r"\b[A-Za-z0-9_\-]{32,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b"
    ),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{12,})\b"),
    re.compile(r"\b([A-Za-z0-9_\-]{48,})\b"),
)

# 這裡的 key 是**對話的來源**，不是廠牌——`claude-local`、`claude-desktop`、
# `codex-app-server` 是同一個廠牌的不同存放位置。所以它不能直接用
# `providers.display_name()`（那一份回答的是「這個廠牌叫什麼」）。
#
# 但那兩個剛好等於廠牌名的，要跟那一份對齊，不然同一個東西在同一個 app 裡
# 會有兩個名字——`startup_diagnostics` 2026-08-24 之前就寫著「Codex」而
# 這裡寫「Codex CLI」。
# 對話來源的**兩種名字**：短名給表格那一欄，長名給滑過去的提示。
#
# 2026-08-24 之前這是兩張表，在兩個檔案裡：這裡一張（短名），
# `session_panel._session_tooltip` 裡一張（長名）。同一組 key 兩份值，
# 各自演化——而 `session_panel` 那一份**少了三個來源**
#（`claude-desktop`、`claude-task`、`imported-transcript`），
# 那三種的提示會直接把原始代號印給使用者看。
#
# 收成一份，而且**存 key 不存字**（存字的話它會在 import 的當下凍住）。
_SOURCE_KEYS = (
    "claude-local",
    "claude-session",
    "claude-task",
    "claude-desktop",
    "codex-app-server",
    "codex-local-fallback",
    "aicc",
    "imported-transcript",
)


# 這兩個來源**就是那個廠牌本身**，短名不另外存一份。
#
# 本來是各存一份，然後用一條測試守著它們不會漂開——而 i18n 那支棘輪抓到
# 「同一句話兩個 key」的時候才想通：**要對齊的東西就該是同一份**，
# 靠測試守著兩份一樣，是把結構問題降級成紀律問題。
_SAME_AS_PROVIDER = {
    "claude-local": "claude",
    "codex-local-fallback": "codex",
}


def source_label(source: str, *, long: bool = False) -> str:
    """這個來源叫什麼。`long=True` 給提示用的那一種。

    不認得的**回代號本身**（長名回一句「本機紀錄」）——不回空字串，
    因為空字串會讓那一欄變成空白，而空白看起來像「這筆資料壞了」。
    """
    if not long and source in _SAME_AS_PROVIDER:
        return display_name(_SAME_AS_PROVIDER[source])
    if source in _SOURCE_KEYS:
        return t(f"source.{source}.{'long' if long else 'short'}")
    return t("source.unknown.long") if long else (source or "")


def source_labels() -> dict[str, str]:
    """短名那一份。**用到才算**，不在 import 的時候建好。"""
    return {key: source_label(key) for key in _SOURCE_KEYS}


@dataclass(frozen=True)
class ConversationHit:
    provider: str
    title: str
    display_title: str
    session_id: str
    updated_at: str
    project_path: str
    source: str
    title_source: str
    resume_status: str
    score: int
    snippet: str

    @property
    def source_label(self) -> str:
        return source_label(self.source)


def compact_text(text: str) -> str:
    return " ".join((text or "").replace("\ufeff", "").split()).strip()


def redact(text: str) -> str:
    result = text or ""
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def truncate(text: str, limit: int) -> str:
    text = compact_text(redact(text))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _trim_edges(text: str) -> str:
    return text.strip().strip("# -:：。.!！").strip()


def _is_lead_in(line: str) -> bool:
    return any(
        line.startswith(lead) or line == lead.rstrip("：:")
        for lead in TASK_LEAD_INS
    )


def _task_from_json(text: str) -> str:
    """The task out of a review/memory package, which carries it as JSON.

    Those two prompts hand the model a serialised bundle rather than prose, so
    a prose scan lands in the middle of it: the title came out as
    `把設定頁的儲存列修好。\\n", "claude_success": true, "claude_out…` - the
    task, then the JSON that happened to follow it.
    """
    match = re.search(r'"task"\s*:\s*"((?:[^"\\]|\\.)*)"', text or "")
    if not match:
        return ""
    try:
        value = json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return ""
    # The value is a whole `task.md`, so it carries the user's own heading
    # too - the same 「# 任務」 the prose path already learned to walk past.
    return _first_block(value.split("\n"))


def _first_block(lines: list[str]) -> str:
    """The first non-empty block, minus a heading line if it opens with one."""
    block: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            if block:
                break
            continue
        if not block and _is_lead_in(line):
            continue
        block.append(line)
    return " ".join(block)


def _task_paragraph(text: str) -> str:
    """The block after a lead-in line, up to the next blank line.

    Structural rather than 「找到下一個停止關鍵字」. The stop-marker list was a
    hand-written set of four, and AICC's own prompts have at least six more
    headings after the task (「先前提出的問題：」「Codex 審查摘要：」
    「Codex 產生的共同記憶更新草案：」…), so titles ran on and swallowed the
    next section. A blank line is what actually separates them, in every
    prompt, without anybody maintaining a list.
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not _is_lead_in(line):
            continue
        # The task may sit on the same line as the lead-in, or in the block
        # under it. Same-line first, because 「任務：把設定頁修好」 is how a
        # person writes it and the block form is how AICC's templates do.
        for lead in TASK_LEAD_INS:
            if line.startswith(lead) and line[len(lead):].strip():
                return line[len(lead):].strip()
        # A second lead-in inside the block is the user's own heading: AICC
        # says 「請完成以下任務：」 and then pastes a `task.md` that starts
        # `# 任務`. Keeping it produced 「任務 把設定頁修好」 - the word 「任務」
        # in the title being the heading it walked past.
        block = _first_block(lines[index + 1:])
        if block:
            return block
    return ""


def extract_task_title(text: str, limit: int = 72) -> str:
    for candidate in (_task_paragraph(text), _task_from_json(text)):
        cleaned = compact_text(candidate)
        # Trimming happens **last and unconditionally** (`_trim_edges` below),
        # which is what stops the sentence-ending 「。」 in front of a stop
        # marker from surviving into the title: 「建構 KYC 審核流程。 限制：⋯」
        # used to come out as 「建構 KYC 審核流程。」 because the trim ran once,
        # before the cut, and nothing tidied the new end afterwards.
        #
        # So the order here does not matter, and a negative control confirmed
        # it: moving the trim back to the front changes no result. That is the
        # point - the fix was making the trim final, not reordering two steps.
        #
        # The cut used to require 11 characters before the marker. That is a
        # length, standing in for 「切完還剩得下東西」 - and it is wrong for a
        # short task: 「任務：把設定頁修好。限制：⋯」 is only 6 characters of
        # task, so nothing was cut and the whole line became the title. Say
        # the actual condition instead.
        for stop in STOP_MARKERS:
            stop_index = cleaned.find(stop)
            if stop_index <= 0:
                continue
            if len(_trim_edges(cleaned[:stop_index])) > 2:
                cleaned = cleaned[:stop_index]
        cleaned = _trim_edges(cleaned)
        # Two characters of prose is not a title; it is punctuation that
        # survived. Falling through gets a truncated first line instead,
        # which at least came from something the user typed.
        if len(cleaned) > 2:
            return truncate(cleaned, limit)

    return truncate(compact_text(text), limit)


def is_aicc_wrapper(text: str) -> bool:
    compact = compact_text(text)
    return any(marker in compact[:260] for marker in AICC_WRAPPER_MARKERS)


def normalize_title(session: SessionInfo, messages: list[SessionMessage]) -> str:
    title = compact_text(session.title)
    title_source = str(session.metadata.get("title_source", ""))
    # Sources that are the name rather than a guess at it. session_index is the
    # Claude desktop app's own sidebar entry; it currently survives anyway, via
    # the wrapper check below, but only because real titles happen not to look
    # like AICC wrapper prompts. Being on this list is the difference between
    # "authoritative" and "not rejected yet".
    if title and title_source in {
        "session_index",
        "official",
        "task_subject",
        "manual",
    }:
        return truncate(title, 72)

    if title and not is_aicc_wrapper(title):
        return truncate(title, 72)

    for message in messages:
        if message.role != "user":
            continue
        candidate = extract_task_title(message.content)
        if candidate:
            return candidate

    fallback = title or f"{provider_name(session.provider)} {session.session_id[:8]}"
    return truncate(fallback, 72)


def searchable_text(
    session: SessionInfo,
    messages: list[SessionMessage],
) -> str:
    parts = [
        session.title,
        session.project_path,
        session.source,
        str(session.metadata.get("preview", "")),
        str(session.metadata.get("task_subject", "")),
    ]
    parts.extend(message.content for message in messages[:80])
    return "\n".join(parts)


def score_session(
    query_terms: list[str],
    session: SessionInfo,
    messages: list[SessionMessage],
) -> int:
    if not query_terms:
        return 1

    haystack = searchable_text(session, messages).casefold()
    score = 0
    for term in query_terms:
        lowered = term.casefold()
        if lowered in compact_text(session.title).casefold():
            score += 12
        if lowered in str(session.project_path).casefold():
            score += 5
        occurrences = haystack.count(lowered)
        score += min(occurrences, 8)
    return score


def best_snippet(
    query_terms: list[str],
    session: SessionInfo,
    messages: list[SessionMessage],
) -> str:
    candidates = [str(session.metadata.get("preview", "")), session.title]
    candidates.extend(message.content for message in messages if message.role == "user")
    candidates = [candidate for candidate in candidates if compact_text(candidate)]
    if not candidates:
        return ""

    if not query_terms:
        return truncate(extract_task_title(candidates[0]) or candidates[0], 180)

    for candidate in candidates:
        compact = compact_text(candidate)
        lowered = compact.casefold()
        for term in query_terms:
            index = lowered.find(term.casefold())
            if index >= 0:
                start = max(0, index - 48)
                end = min(len(compact), index + 132)
                return truncate(compact[start:end], 220)
    return truncate(candidates[0], 180)


def resume_status(session: SessionInfo) -> str:
    if session.source in {
        "claude-local",
        "claude-session",
        "codex-app-server",
        "codex-local-fallback",
    }:
        return t("search.resumable")
    return t("search.background_only")


# （這裡本來有第二個 `source_label`，跟上面那個同名——那是 2026-08-24
# 收兩張表的時候留下來的。同名的第二個定義會蓋掉第一個，而症狀是無限遞迴。
#
# 也本來有一個 `provider_name()` 回「Claude」／「Codex」——**廠牌顯示名的
# 第四份**，而且沒有任何地方叫它。顯示名的唯一來源是
# `app/services/providers.py`。）


class ConversationSearchService:
    def __init__(self, scanner: SessionScanner | None = None) -> None:
        self.scanner = scanner or SessionScanner()

    def search(
        self,
        project_path: Path,
        provider: str = "all",
        query: str = "",
        limit: int = 100,
        include_snippets: bool = True,
        should_stop=None,
    ) -> list[ConversationHit]:
        """`should_stop` 是**唯一**能讓這一趟提早回來的東西。

        沒有它的時候，這支函式一旦開始就一定跑完：底下那個迴圈會把使用者
        每一個本機對話檔讀進來（這台機器 41 個，claude 3.0 秒、codex 6.7 秒）。
        呼叫端在背景執行緒裡跑它，而 `QThread.quit()` **叫不動一條正卡在
        長 slot 裡的執行緒**——事件迴圈根本沒在轉，`quit()` 只是排隊等這一趟
        跑完。關視窗的收尾等不到，就會放著一條還在跑的執行緒讓
        `~QApplication` 撞上去，Qt 直接中止行程（0xC0000409）。

        所以拿掉這兩個檢查、或把它們挪到迴圈外面（只在換 provider 的時候看
        一次），代價不是「關得慢一點」，是**關掉視窗會當機**——而且畫面、
        測試報告全都一模一樣，只有離開碼不一樣。

        檢查放在**每一個 session 之前**而不是每個 provider 之前：一個檔案最大
        50 MB，那是這裡能做到的最細顆粒；每個 provider 看一次的話，最壞情況
        還是要等完整整一邊。
        """
        providers = ("claude", "codex") if provider == "all" else (provider,)
        query_terms = [term for term in re.split(r"\s+", query.strip()) if term]
        hits: list[ConversationHit] = []

        for item_provider in providers:
            if should_stop is not None and should_stop():
                break
            sessions = (
                self.scanner.scan_claude(project_path, include_all=True)
                if item_provider == "claude"
                else self.scanner.scan_codex(project_path, include_all=True)
            )
            for session in sessions:
                if should_stop is not None and should_stop():
                    break
                messages = self.load_messages(session, project_path)
                score = score_session(query_terms, session, messages)
                if query_terms and score <= 0:
                    continue

                hits.append(
                    ConversationHit(
                        provider=item_provider,
                        title=truncate(session.title, 96),
                        display_title=normalize_title(session, messages),
                        session_id=session.session_id,
                        updated_at=session.updated_at,
                        project_path=session.project_path,
                        source=session.source,
                        title_source=str(session.metadata.get("title_source", "")),
                        resume_status=resume_status(session),
                        score=score,
                        snippet=(
                            best_snippet(query_terms, session, messages)
                            if include_snippets
                            else ""
                        ),
                    )
                )

        return sorted(
            hits,
            key=lambda hit: (hit.score, hit.updated_at),
            reverse=True,
        )[:limit]

    def load_messages(
        self,
        session: SessionInfo | ConversationHit,
        project_path: Path,
    ) -> list[SessionMessage]:
        if session.provider == "claude":
            return self.scanner.load_claude_messages(session.session_id, project_path)
        return self.scanner.load_codex_messages(session.session_id, project_path)

    def render_preview(
        self,
        hit: ConversationHit,
        project_path: Path,
        max_messages: int = 8,
    ) -> str:
        messages = self.load_messages(hit, project_path)
        lines = [
            f"{display_name(hit.provider)}：{hit.display_title}",
            f"來源：{hit.source_label}",
            f"狀態：{hit.resume_status}",
            f"最後更新：{hit.updated_at or '-'}",
            f"對話 ID：{hit.session_id}",
        ]
        if hit.project_path:
            lines.append(f"專案路徑：{hit.project_path}")
        if hit.snippet:
            lines.extend(["", t("search.matching_part"), hit.snippet])

        lines.append("")
        lines.append(t("sessions.latest_exchange"))

        shown = 0
        for message in messages[-max_messages:]:
            content = truncate(message.content, 900)
            if not content:
                continue
            role = t("search.speaker.user") if message.role == "user" else provider_name(hit.provider)
            lines.append("")
            lines.append(f"{role}:")
            lines.append(content)
            shown += 1

        if shown == 0:
            lines.append(t("search.nothing_to_preview"))

        return "\n".join(lines)


def collect_hits(
    project_path: Path,
    provider: str,
    query: str,
    limit: int,
    include_snippets: bool,
) -> list[ConversationHit]:
    return ConversationSearchService().search(
        project_path=project_path,
        provider=provider,
        query=query,
        limit=limit,
        include_snippets=include_snippets,
    )
