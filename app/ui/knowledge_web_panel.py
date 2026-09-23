"""Shared-memory panel — renders knowledge_v1.html from .aicc/knowledge/*.md.

Two fields deserve their reasoning written down.

`updated_by` now comes from a real record. `write_memory_file()` writes who
wrote each file into `.aicc/knowledge/.provenance.json` - "codex" for the
orchestrator's automatic memory maintenance, "you" for this panel's manual
editor. Files written before that existed have no entry, and those are still
sent as "unknown" rather than guessed; the page omits the whole "由 X 更新"
clause when it cannot be told.

`context[]` says what the next prompt will carry. 2026-08-21 that stopped being
a budget question: shared memory is sent whole, every file, every run. So this
list has one state left for a file that exists and has content - `full` - and
`context_limit` is 0, which is what tells the page to draw 「共 N 字」 instead of
a 「已用 N / M 字上限」 meter. A meter against a limit that no longer exists
would be the same lie in a new place.
"""
from __future__ import annotations

from app.i18n import t

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal

from app.services.markdown_lite import render_markdown
from app.services.project_knowledge import MEMORY_FILES, ProjectKnowledgeStore
from app.ui.web_panel import WebViewPanel
from app.services import relative_time

# 0 = 沒有字數上限。頁面拿它當旗標：0 的時候畫「共 N 字」，非 0 的時候畫一條
# 「已用 N／M 字上限」的量表。共同記憶不再被截斷，所以量表沒有東西可以量。
NO_MEMORY_LIMIT = 0
STALE_AFTER_DAYS = 30

KIND_LABEL = {
    "PROJECT_BRIEF.md": "memory.brief",
    "ARCHITECTURE.md": "memory.architecture",
    "DECISIONS.md": "memory.decisions",
    "SECURITY_MODEL.md": "memory.security",
    "PRODUCT_REVIEW.md": "memory.review",
    "BUSINESS_MODEL.md": "memory.business",
    "NEXT_STEPS.md": "memory.next",
    "OPEN_QUESTIONS.md": "memory.open",
}


def _relative(moment: datetime) -> str:
    seconds = (datetime.now() - moment).total_seconds()
    return relative_time.describe_age(seconds)


def _summary(text: str) -> str:
    """First real sentence, preferring prose but falling back to a bullet.

    DECISIONS.md is bullets from top to bottom, so skipping every list line
    left it summarised as "（沒有內容）" - a file with 95 lines of content
    described as empty.
    """
    bullet = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">", "|", "```")):
            continue
        if line.startswith(("-", "*", "+")):
            if not bullet:
                bullet = line.lstrip("-*+ ").strip()
            continue
        return line[:80]
    return bullet[:80] if bullet else t("common.no_content_paren")


class KnowledgeWebPanel(WebViewPanel):
    PAGE = "knowledge_v1.html"

    # `html` is rendered Markdown, already sanitised by markdown_lite against a
    # closed tag whitelist. Escaping it again would print the memory files as
    # raw HTML source. Every other field here goes through the base class's
    # escaping - see escape_state().
    HTML_FIELDS = frozenset({"html"})

    file_opened = Signal(str)          # a path to reveal or open
    file_saved = Signal(str)           # a memory file name that was written
    report_ready = Signal(str)         # readiness report text to display
    create_handoff_requested = Signal(str)   # same shape as KnowledgePanel's

    def __init__(self, project_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.store: ProjectKnowledgeStore | None = None
        self._project: Path | None = None
        self._selected: str | None = None
        if project_path is not None:
            self.set_project(project_path)

    def set_project(self, project_path: Path) -> None:
        self._project = Path(project_path)
        if self.store is None:
            self.store = ProjectKnowledgeStore(self._project)
        else:
            self.store.set_project(self._project)
        self._selected = None
        self.push_state()

    # ---- data -----------------------------------------------------------
    def _memory_dir(self) -> Path | None:
        return None if self._project is None else self._project / ".aicc" / "knowledge"

    def collect_state(self) -> dict:
        folder = self._memory_dir()
        if folder is None or not folder.is_dir():
            return {
                "mode": self._mode,
                "project_name": self._project.name if self._project else "",
                "selected": None,
                "files": [],
                "context_limit": NO_MEMORY_LIMIT,
                "context": [],
            }

        provenance = self.store.read_provenance() if self.store else {}
        files = []
        for name in MEMORY_FILES:
            path = folder / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            updated = datetime.fromtimestamp(path.stat().st_mtime)
            # Only a recorded author counts. A file written before provenance
            # existed, or by something that did not declare itself, stays
            # unknown instead of being attributed to whoever seems likely.
            author = str(provenance.get(name, {}).get("by", "")) or "unknown"
            files.append(
                {
                    "id": name,
                    "name": name,
                    "kind": name.removesuffix(".md").lower(),
                    # `KIND_LABEL` 存的是 key（那張表在模組層，存字會在 import 當下凍住）。
                    "kind_label": t(KIND_LABEL.get(name, "memory.generic")),
                    "updated_rel": _relative(updated),
                    "updated_abs": updated.strftime("%Y-%m-%d %H:%M"),
                    "updated_by": author,
                    "lines": len(text.splitlines()),
                    "summary": _summary(text),
                    "stale": (datetime.now() - updated).days >= STALE_AFTER_DAYS,
                    "html": render_markdown(text),
                    # The raw Markdown, for the editor. `html` is the rendered
                    # form and editing that would hand back HTML to be saved
                    # into a .md file. The page disables its edit button when
                    # this is missing rather than opening an empty editor.
                    "source": text,
                }
            )

        selected = self._resolve_selection([f["id"] for f in files])

        return {
            "mode": self._mode,
            "project_name": self._project.name if self._project else "",
            "selected": selected,
            "files": files,
            "context_limit": NO_MEMORY_LIMIT,
            "context": self._context_plan(folder),
        }

    def _resolve_selection(self, names: list[str]) -> str | None:
        """Which file is selected, agreeing with what the page shows.

        The page selects the first file on its own when Python has not named
        one, and `save` sends the edited text rather than the file name - so if
        this side kept `_selected = None`, a save made without first clicking a
        file was silently dropped. Measured in the running app: the memory file
        was unchanged and `.provenance.json` was never written, with no error
        anywhere.

        Resolving here, and storing the answer, is what keeps the two sides
        from disagreeing: every push tells the page what is selected, and this
        is that same value.
        """
        if self._selected not in names:
            self._selected = names[0] if names else None
        return self._selected

    def _context_plan(self, folder: Path) -> list[dict]:
        """What the next prompt will actually carry, and why.

        Every file that exists and has content is sent whole - that is
        `build_memory_context()`'s rule now, and this list is checked against
        the real thing by `test_the_panel_does_not_promise_more_than_is_sent`
        rather than by a comment claiming the two agree. The mirror this used
        to be had already drifted: it still filled in file order and stopped at
        12,000, months after the sending code moved to fair shares.

        `skipped` survives, for the three cases where nothing is sent because
        there is nothing to send (missing / unreadable / empty). `truncated`
        does not: no code path produces it any more, and keeping a state the
        page can paint but the engine can never reach is how a screen starts
        describing a behaviour that no longer exists.
        """
        plan: list[dict] = []
        for name, title in MEMORY_FILES.items():
            path = folder / name
            if not path.is_file():
                plan.append(
                    {"name": name, "chars": 0, "sent_chars": 0,
                     "state": "skipped", "reason": t("file.missing")}
                )
                continue
            try:
                content = path.read_text(encoding="utf-8").strip()
            except OSError:
                plan.append(
                    {"name": name, "chars": 0, "sent_chars": 0,
                     "state": "skipped", "reason": t("file.unreadable")}
                )
                continue
            if not content:
                plan.append(
                    {"name": name, "chars": 0, "sent_chars": 0,
                     "state": "skipped", "reason": t("file.empty")}
                )
                continue

            size = len(f"### {title}\n{content}")
            plan.append(
                {"name": name, "chars": size, "sent_chars": size, "state": "full"}
            )
        return plan

    # ---- actions --------------------------------------------------------
    def handle_action(self, kind: str, arg: str) -> None:
        """Every control the page offers, routed to the same store the native
        panel uses.

        `edit` and `cancel_edit` never arrive here - the page keeps the editor's
        open/closed state itself and only sends `save` with the edited text,
        which is the right split: nothing about "is the editor open" belongs in
        Python, and a round trip per keystroke would be absurd.
        """
        if kind == "select":
            self._selected = arg
            self.push_state()
            return
        if kind == "rescan":
            self.push_state()
            return
        if kind == "open_folder":
            folder = self._memory_dir()
            if folder is not None:
                self.file_opened.emit(str(folder))
            return

        if self.store is None:
            return

        try:
            self._run_action(kind, arg)
        except OSError as error:
            # A store operation that cannot write is a real failure the user
            # has to see - silently doing nothing would look like the button
            # is broken rather than like the disk is full or the file is open.
            self.reject(f"{kind}：{error}")
        self.push_state()

    def _run_action(self, kind: str, arg: str) -> None:
        store = self.store
        if kind == "save":
            # `arg` is the edited Markdown. "you" because this panel's editor is
            # the only way a human writes one of these by hand - see
            # write_memory_file's author whitelist.
            if self._selected:
                store.write_memory_file(self._selected, arg, author="you")
                self.file_saved.emit(self._selected)
        elif kind == "create_files":
            store.ensure_memory_files()
        elif kind == "view_all":
            self._selected = None          # deselect: the page shows all files
        elif kind == "readiness_check":
            self.report_ready.emit(store.format_readiness_report())
        elif kind == "make_handoff":
            self.file_opened.emit(str(store.write_handoff_package()))
        elif kind == "make_bootstrap":
            self.file_opened.emit(str(store.write_session_bootstrap(arg)))
        elif kind == "save_context":
            self.file_opened.emit(str(store.write_task_context(arg)))
        elif kind == "create_handoff_task":
            store.ensure_memory_files()
            store.export_manifest()
            self.create_handoff_requested.emit(store.build_memory_context())
