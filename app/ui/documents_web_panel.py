"""Documents panel — renders documents_v1.html from the project's index.

`hits` is the part worth explaining. The design brief asks for "AI 實際會讀到的
片段" rather than the whole file, and that is exactly what
`ProjectKnowledgeStore.search("")` returns: with no query it falls back to the
most recent chunks, which is the set a prompt would actually carry. So the
preview is not a sample of the document - it is the excerpt the model gets.

Filtering and search happen in the page (per the handoff), so Python pushes the
whole list once and does not participate in either.
"""
from __future__ import annotations

from app.i18n import STRINGS, t

import html
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.services.cloud_transcript_import import (
    CloudTranscriptImporter,
    TranscriptImportResult,
)
from app.services.project_knowledge import KnowledgeDocument, ProjectKnowledgeStore
from app.ui.web_panel import WebViewPanel
from app.services import relative_time

# Keyed on what `document_update_status()` actually returns, checked against
# that function rather than against the page's vocabulary. It previously held a
# key `"changed"` - the page's word for this state, not the store's - so the
# real value `"updated"` matched nothing and fell through to the default. The
# values are (page badge key, text); the two sides use different words for the
# same state and this dict is the only place they are allowed to differ.
STATUS_LABEL = {
    "current": ("indexed", "documents.state.indexed"),
    "updated": ("changed", "documents.state.stale"),
    "missing": ("failed", "documents.state.missing_index"),
    "source_missing": ("failed", "documents.state.missing_source"),
}


def _unknown_status(status: str) -> tuple[str, str]:
    """What to draw for a status this panel has no translation for.

    Not `("indexed", status)`, which is what it used to be. That default is how
    both of this file's bugs reached the screen: a missing translation came out
    as a confident mint 已索引, so the *absence* of an answer looked exactly
    like the healthy answer. An unrecognised state is a real state - the honest
    rendering is 「有事，而且我不知道是什麼事」, carrying the raw value so it
    can be looked up.
    """
    return "changed", f"狀態未知（{status}）"


MAX_HITS_PER_DOC = 3


@dataclass(frozen=True)
class _Unreadable:
    """「這一格讀不到」——一個**跟空值長得不一樣**的型別。

    這支檔案裡的三個 `except` 本來各自回 `{}`、`{"stale": 0, "dead": 0}`、
    `""`。那三個值在畫面上分別是「沒有相關片段」「沒有東西要處理」「沒有話
    要說」——**每一個都是一句斷言**，而真相是「我沒讀到」。

    用型別而不是用旗標，是因為旗標會被下一個人 `.get(key, 好的預設)` 掉：
    只要回的東西跟正常值同型，遲早會有人直接用它。
    """

    detail: str


def _describe(error: BaseException) -> str:
    """把例外壓成一行給畫面用的字。

    只有型別與訊息，沒有 traceback：這一行會出現在使用者眼前，而
    `no such table: documents` 正是那句最有用的話——它說得出「索引不在了」，
    而不是「這個專案沒有文件」。
    """
    return f"{type(error).__name__}: {error}"

# 證據清單的兩個欄位：這段話，和它憑什麼在這裡。
WHY_LABEL = {
    "keyword": "documents.why.hit",
    "substring": "documents.why.contains",
}
WHY_NO_MATCH = "documents.why.fallback"


def _why(chunk) -> str:
    """One line saying what put this excerpt in the context."""
    if chunk.reason == "recent" or not chunk.matched_terms:
        # Deliberately not 「最近更新」 on its own: that reads like a property
        # of the excerpt. What the user needs to know is that no matching
        # happened at all, which is a statement about the *selection*.
        return t(WHY_NO_MATCH)
    key = WHY_LABEL.get(chunk.reason)
    label = t(key) if key else chunk.reason
    return f"{label}：{'、'.join(chunk.matched_terms[:4])}"


def _highlight(text: str, terms) -> str:
    """Escape for the page, then mark the words that were matched on.

    Order matters and is the whole trick: escaping after inserting the marks
    would escape the marks too, and marking before escaping would let a term
    containing `<` smuggle markup in. So the text is escaped first and the
    `<mark>` tags are wrapped around already-escaped needles.

    Nothing is highlighted when nothing was matched - that case gets the
    sentence from `_why` instead. Highlighting arbitrary words on a `recent`
    excerpt would be theatre, and worse, it would make the one selection that
    is not evidence look like the best-supported one on the page.

    One pass over one alternation, not one pass per term. Marking terms one
    after another nests them as soon as one term contains another - 「開戶」
    then 「開」 turns into `<mark><mark>開</mark>戶</mark>` - and the terms come
    from the user's own task, so that is an ordinary Chinese case, not a
    contrived one. Longest first, so the longer term wins at a shared position.
    """
    escaped = html.escape(text, quote=False)
    needles: list[str] = []
    seen: set[str] = set()
    for term in sorted(terms, key=len, reverse=True):
        needle = html.escape(term, quote=False)
        if len(needle) < 2 or needle.lower() in seen:
            continue
        seen.add(needle.lower())
        needles.append(re.escape(needle))
    if not needles:
        return escaped
    return re.sub(
        "|".join(needles),
        lambda match: f"<mark>{match.group(0)}</mark>",
        escaped,
        flags=re.IGNORECASE,
    )


def _relative(iso_or_epoch: str) -> tuple[str, str]:
    """(relative, absolute). Returns ('', '') when the timestamp is unusable."""
    if not iso_or_epoch:
        return "", ""
    try:
        moment = datetime.fromisoformat(iso_or_epoch)
    except ValueError:
        return "", str(iso_or_epoch)
    seconds = max(0.0, (datetime.now(moment.tzinfo) - moment).total_seconds())
    return relative_time.describe_age(seconds), moment.strftime("%Y-%m-%d %H:%M")


def _size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes / 1024 / 1024:.1f} MB"


class DocumentsWebPanel(WebViewPanel):
    PAGE = "documents_v1.html"

    file_opened = Signal(str)      # a generated file to reveal
    report_ready = Signal(str)     # text to show (preview / relevant excerpts)

    def __init__(self, project_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.store: ProjectKnowledgeStore | None = None
        self._project: Path | None = None
        self._selected: str | None = None
        # 「這一次讀索引失敗了，而且失敗在哪一步」。None＝上一次讀成功。
        # 存起來而不是就地丟掉，是因為**畫面與對話框需要的是同一件事實**，
        # 而 collect_state 只回得了畫面那一半。
        self._index_error: str | None = None
        # 正在講「索引讀不到」這件事嗎。`reject()` 自己會再推一次狀態，而這一支
        # 的 `push_state()` 就是發話的地方——沒有這個旗標的話，例外訊息只要在
        # 兩次讀之間變了一個字（例如訊息裡帶行號），兩支就會互相呼叫下去。
        self._telling_about_index: bool = False
        if project_path is not None:
            self.set_project(project_path)

    def set_project(self, project_path: Path) -> None:
        self._project = Path(project_path)
        if self.store is None:
            self.store = ProjectKnowledgeStore(self._project)
        else:
            self.store.set_project(self._project)
        self._selected = None
        # 換專案＝換一份索引，上一份的失敗不能跟過來（否則新專案一開就跳
        # 一個講舊專案的錯誤訊息）。
        self._index_error = None
        self.push_state()

    # ---- data -----------------------------------------------------------
    def collect_state(self) -> dict:
        if self.store is None:
            return {"mode": self._mode, "selected": None, "docs": []}

        try:
            documents = self.store.list_documents()
            statuses = self.store.document_update_statuses()
        except Exception as error:
            # **「索引讀不到」跟「這個專案還沒有文件」不是同一件事。**
            #
            # 這裡本來回 `{"mode":…, "selected": None, "docs": []}`——跟一個
            # 乾淨的空專案**逐欄位一模一樣**。畫面因此畫出「0 份」＋
            # 「沒有符合條件的文件。」，沒有錯誤訊息、沒有紅色徽章、沒有
            # toast、也沒有任何一行 log。使用者的合理結論是「我還沒放文件」，
            # 然後就不會去追為什麼每一輪 prompt 都沒帶到專案資料。
            #
            # 這條路真的走得到（實測）：面板開著的時候 `index.sqlite` 被刪掉、
            # 被還原、或被防毒隔離，下一次讀就是 `no such table: documents`
            # ——而且那一次讀還會順手生出一個 4 KB 的空 DB。
            #
            # 「不要讓面板掛掉」是對的，那是原本註解寫的理由；
            # **「回一個跟健康狀態無法區分的 payload」沒有人決定過。**
            # 同一支檔案往上三十行的 `_unknown_status()` 講的正是相反的原則：
            # 「有事，而且我不知道是什麼事」。
            # 這裡**沒有推 `index_error` 這個欄位**，是刻意的：紅色橫幅住在
            # `documents_v1.html`（設計端），而 `test_no_field_is_pushed_into
            # _the_void` 會擋下一個沒有人讀的欄位——它擋對了。畫面那一半接上
            # 之前，這件事實走 `push_state()` 的對話框，不走一個沒有人讀的欄位。
            self._index_error = _describe(error)
            return {
                "mode": self._mode,
                "selected": None,
                "docs": [],
                # None，不是 0／{}／""。讀不到就是讀不到，**不是「沒有東西
                # 要處理」**——那是同一個謊的縮小版。
                "batch": None,
                "note": None,
            }

        # 清單讀成功了。上一輪的失敗到此為止——不清掉的話錯誤訊息會黏在畫面上，
        # 而**一個修好之後還在報錯的畫面，跟一個沒修好的畫面一樣不能信**。
        self._index_error = None

        task = self._current_task()
        # 三個都可能各自失敗，而三個失敗都是「讀不到」，不是「沒有東西」。
        # 先算，再一起判——第一個講得出理由的那個就是要顯示的那個。
        hits_by_doc = self._hits_by_document(task)
        batch = self._batch_counts()
        note = self._selection_note(task)
        for candidate in (hits_by_doc, batch, note):
            if isinstance(candidate, _Unreadable):
                self._index_error = candidate.detail
                break

        docs = [
            self._document(doc, statuses.get(doc.document_id, "current"), hits_by_doc)
            for doc in documents
        ]

        selected = self._selected
        if selected not in {d["id"] for d in docs}:
            selected = docs[0]["id"] if docs else None
        return {
            "mode": self._mode,
            "selected": selected,
            "docs": docs,
            # None（而不是 `{"stale": 0, "dead": 0}`）：讀不到就是讀不到，
            # **不是「沒有東西要處理」**——那是同一個謊的縮小版，而它會讓
            # 那兩顆批次按鈕停用得跟「今天真的沒事要做」一模一樣。
            "batch": None if isinstance(batch, _Unreadable) else batch,
            # 「為什麼是這些」 when the honest answer is 「沒有為什麼」. Empty on
            # an ordinary run: a line that appears every time is a line people
            # stop reading, and then it is not there on the run that needed it.
            "note": None if isinstance(note, _Unreadable) else note,
        }

    def push_state(self) -> None:
        """推狀態，**而且在索引讀不到的時候真的說出來。**

        `collect_state()` 只回得了畫面那一半，而畫面那一半的紅色橫幅住在
        `documents_v1.html`（設計端的檔案）。在它接上之前，這裡不能讓
        「讀不到」只存在於 payload 裡——**一個沒有人接的訊號跟一個沒有發的
        訊號，在畫面上一模一樣**（`web_panel.action_failed` 的原話）。

        只在**由成功轉成失敗的那一次**發，不是每次推狀態都發：面板每按一個
        動作就推一次，每次都跳一個對話框的話，使用者會學會直接關掉它——
        而那正是需要它講話的那一次會被關掉的原因。

        講話走 `reject()` 而不是自己 `action_failed.emit()`。這裡本來直接發
        訊號，理由是「狀態已經推出去了，而 `reject()` 會再推一次」——聽起來
        合理，但它繞過的是 `test_a_rejected_action_is_visible` 在守的那條規矩：
        **說一句話跟推一次狀態要綁在一起**，因為第七個地方一定會忘記推。
        多推那一次不痛（`collect_state()` 冪等），繞過那條規矩才痛。

        遞迴由 `_telling_about_index` 擋住，不是靠「第二次比對會相等」那個
        巧合——例外訊息只要在兩次讀之間變了一個字（帶行號、帶時間），那個
        巧合就沒了，而它壞掉的樣子是整個 App 卡死。
        """
        before = self._index_error
        super().push_state()
        if self._telling_about_index:
            return
        if self._index_error and self._index_error != before:
            self._telling_about_index = True
            try:
                self.reject(
                    f"{t('documents.state.missing_index')}：{self._index_error}"
                )
            finally:
                self._telling_about_index = False

    def _current_task(self) -> str:
        """The task the excerpts below are evidence *for*.

        This used to be `""`. `search("")` matches nothing, so the store fell
        back to the most recently indexed chunks - and the panel presented them
        under 「AI 實際會讀到的片段」. On a project with a real task that is
        simply the wrong set, and it looked identical to a good one: same
        layout, same count, no marks, nothing saying it had matched on nothing.

        It is also what blocked the design's 證據清單 shape. The brief's own
        mock highlights the matched words, and the old code had a comment
        explaining that highlighting was impossible 「without a query」. The
        query existed the whole time - in `.aicc/task.md`, which is the file
        the run itself reads.
        """
        if self._project is None:
            return ""
        try:
            return (self._project / ".aicc" / "task.md").read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return ""

    def _selection_note(self, task: str) -> str | _Unreadable:
        try:
            return self.store.selection_note(task)
        except Exception as error:
            # `note` 是這一頁**唯一一個現成拿來講實話的欄位**，而它本來跟著
            # 一起被靜音成空字串。
            return _Unreadable(_describe(error))

    def _batch_counts(self) -> dict | _Unreadable:
        """How many documents each batch action would act on.

        Counts rather than ids: the page needs the number to label the button
        and to know whether to offer it at all, and shipping the ids would
        invite it to send back a list this side would then have to re-validate.
        The store recomputes the set when the action runs, so a document that
        changed state in between is handled by the store, not by a stale list.

        讀失敗回 `_Unreadable`，不是 `{"stale": 0, "dead": 0}`：那兩顆按鈕靠
        這個數字決定要不要停用，而「讀不到」被寫成 0 之後，畫面上跟「今天真的
        沒有東西要處理」**一模一樣**。
        """
        try:
            return {
                "stale": len(self.store.stale_document_ids()),
                "dead": len(self.store.dead_document_ids()),
            }
        except Exception as error:
            return _Unreadable(_describe(error))

    def _document(
        self,
        doc: KnowledgeDocument,
        status: str,
        hits_by_doc: dict[int, list[dict]] | _Unreadable,
    ) -> dict:
        # 那張表存的是 key（模組層存字會在 import 的當下凍住）。
        status_key, label_key = STATUS_LABEL.get(status, _unknown_status(status))
        status_label = t(label_key) if label_key in STRINGS else label_key
        # The store's healthy value is "indexed", not "ok". Comparing against
        # "ok" marked *every successfully indexed document* as failed - caught
        # by opening the panel and seeing three red 索引失敗 badges on files the
        # store reported as fine. Nothing in the schema was ever going to say
        # "ok": see project_knowledge.py's DEFAULT 'indexed'.
        if doc.status and doc.status != "indexed":
            status_key, status_label = "failed", doc.error or doc.status
        rel, absolute = _relative(doc.modified_at)
        return {
            "id": str(doc.document_id),
            "name": doc.display_name,
            "ext": (doc.extension or "").lstrip(".").upper(),
            "size": _size(doc.size_bytes),
            "path": doc.source_path,
            "status": status_key,
            "status_label": status_label,
            # chunk_count, not page count: the index stores chunks and the
            # number of pages is not tracked for every format.
            "pages": doc.chunk_count,
            "updated_rel": rel,
            "updated_abs": absolute,
            # None（不是 `[]`）when the excerpt search itself failed: 空陣列在
            # 畫面上是「這份文件沒有相關片段」，那是一句斷言。
            "hits": (
                None
                if isinstance(hits_by_doc, _Unreadable)
                else hits_by_doc.get(doc.document_id, [])
            ),
        }

    def _hits_by_document(self, task: str) -> dict[int, list[dict]] | _Unreadable:
        """The excerpts a prompt would actually carry, grouped per document.

        ROADMAP P1「更像證據清單，不要像資料庫表格」. A row saying 片段 3 and
        220 characters is a table; evidence answers **為什麼是這一段**. The
        store already worked that out - `reason` and `matched_terms` are set at
        the branch that decides them - and this method used to drop both on the
        floor and send a chunk index instead.
        """
        try:
            chunks = self.store.search(task, max_chunks=60)
        except Exception as error:
            return _Unreadable(_describe(error))
        grouped: dict[int, list[dict]] = {}
        for chunk in chunks:
            bucket = grouped.setdefault(chunk.document_id, [])
            if len(bucket) >= MAX_HITS_PER_DOC:
                continue
            text = " ".join(chunk.content.split())[:220]
            bucket.append(
                {
                    "loc": f"片段 {chunk.chunk_index + 1}",
                    "text": _highlight(text, chunk.matched_terms),
                    # Why this one is here. `recent` is the reason the field
                    # exists: it means nothing was matched against, and until
                    # it was said out loud it looked exactly like a good hit.
                    "why": _why(chunk),
                    "weak": chunk.reason == "recent",
                }
            )
        return grouped

    # ---- actions --------------------------------------------------------
    # Which actions open a file dialog, and what they ask for. Kept as data
    # rather than a branch each, because the only thing that differs between
    # them is the prompt and the filter - and three near-identical branches is
    # how the three of them drift apart.
    # **存 key 不存字**（這是類別層，取出來會在 import 的當下凍住）。
    FILE_DIALOGS = {
        "import_files": ("documents.import_files", "documents.filter.documents"),
        "import_transcript": (
            "documents.import_transcripts",
            "documents.filter.transcripts",
        ),
    }

    def handle_action(self, kind: str, arg: str) -> None:
        if kind == "select":
            self._selected = arg
            self.push_state()
            return
        if self.store is None:
            return
        try:
            self._run_action(kind, arg)
        except OSError as error:
            self.reject(f"{kind}：{error}")
        self.push_state()

    def _import(
        self, paths: list[Path] | None, folder: Path | None = None
    ) -> None:
        """Import, and stop for anything the scanner flagged.

        ROADMAP P1「匯入敏感文件前提示」. The question the dialog has to answer
        is not 「這個檔案裡有沒有東西」 but **「為什麼我該在意」** - importing
        puts the content into the search index, and from there it can be
        selected into the prompt sent to Claude or Codex. Nothing said that
        before, so a client file went from 「我硬碟上的一個檔案」 to 「送給廠商
        的一段文字」 with one click and no sentence in between.

        The clean files go in either way. Holding a whole folder hostage over
        one flagged file would teach people to answer 「全部匯入」 without
        reading, which is the outcome this exists to avoid.
        """
        store = self.store
        result = (
            store.import_folder(folder)
            if folder is not None
            else store.import_files(paths or [])
        )
        if not result.flagged:
            return

        if not self._confirm_flagged(result.flagged, len(result.imported)):
            return
        store.import_files(
            [Path(item.path) for item in result.flagged], allow_flagged=True
        )

    def _confirm_flagged(self, flagged, imported_count: int) -> bool:
        """The 「為什麼我該在意」 dialog, shared by both import buttons.

        Extracted when 匯入舊對話 was wired to the transcript importer. Two
        copies of this question would drift, and 「一個 app 裡有兩種『機密』的
        定義，就是其中一種會出錯的方式」 applies to the *sentence* as much as to
        the scanner: a conversation export is the single most likely file on
        the disk to contain a client's details, so it is the last place that
        should get a weaker warning than a stray PDF.
        """
        listed = "\n".join(
            f"　· {item.name}（{'、'.join(item.findings)}）" for item in flagged[:12]
        )
        extra = f"\n　⋯還有 {len(flagged) - 12} 個" if len(flagged) > 12 else ""
        answer = QMessageBox.question(
            self,
            t("documents.secrets_found"),
            f"已匯入 {imported_count} 個。下面這幾個先沒有匯入：\n\n"
            f"{listed}{extra}\n\n"
            "匯入不只是複製檔案——內容會進入搜尋索引，之後有機會被選進"
            "送給 Claude／Codex 的 context。\n\n"
            "還是要匯入嗎？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _import_transcripts(self, paths: list[Path]) -> None:
        """匯入舊對話 (ROADMAP P1「支援 Claude / ChatGPT 匯出對話」).

        This button existed and did the wrong thing. It shared `_import` with
        匯入檔案, so a ChatGPT export was indexed as **the raw JSON file** -
        `[{"title": ..., "mapping": {"1": {"message": {"author": ...` - and the
        conversation went into the index as scaffolding with the sentences
        scattered through it. `CloudTranscriptImporter`, which converts that to
        readable markdown, dedupes by content hash and refuses shapes it does
        not recognise, had **no live caller**: its only one was
        `KnowledgePanel`, the native panel that the web pages replaced, kept in
        `main_window` behind a `noqa` as a rollback path and never built.

        So every fix made to that importer was unreachable from the running
        app, and the button reported success either way. That is why the test
        for this drives the panel rather than the importer.
        """
        store = self.store
        flagged = store.inspect_import(paths)
        blocked = {item.path for item in flagged}
        clean = [p for p in paths if str(p.expanduser().resolve()) not in blocked]

        importer = CloudTranscriptImporter(self._project)
        results: list[TranscriptImportResult] = []
        if clean:
            results.append(importer.import_paths(clean))
        if flagged and self._confirm_flagged(
            flagged, sum(len(r.imported_files) for r in results)
        ):
            results.append(importer.import_paths([Path(i.path) for i in flagged]))
        if not results:
            return

        # Merged into one result rather than reporting each pass, so the user
        # gets the importer's own sentence once. It is the only thing that
        # knows how many were already here by content, and 「匯入了 3 個」 when
        # nothing changed is what trains people to stop reading confirmations.
        merged = TranscriptImportResult(
            success=all(r.success for r in results),
            imported_files=tuple(f for r in results for f in r.imported_files),
            errors=tuple(e for r in results for e in r.errors),
            already_here=tuple(a for r in results for a in r.already_here),
        )
        if merged.success:
            self.report_ready.emit(merged.summary)
        else:
            # action_failed opens a 「操作沒有完成」 warning, so the two channels
            # are not interchangeable: a success sent down this one would tell
            # the user their import failed.
            self.reject(merged.summary)

    def _remove_dead(self) -> None:
        """Sweep out index entries with no file behind them (ROADMAP P1).

        Names the files in the dialog rather than only counting them. 「要刪掉
        7 筆嗎」 is a question nobody can answer; 「要刪掉這 7 筆嗎」 with the
        list under it is. The count alone is also how a wrong set gets
        confirmed - if `dead_document_ids` ever widened to include
        `source_missing`, a number would hide that and a list would not.
        """
        store = self.store
        documents = {d.document_id: d.display_name for d in store.list_documents()}
        dead = store.dead_document_ids()
        if not dead:
            return

        listed = "\n".join(f"　· {documents.get(i, i)}" for i in dead[:12])
        extra = f"\n　⋯還有 {len(dead) - 12} 筆" if len(dead) > 12 else ""
        answer = QMessageBox.question(
            self,
            t("documents.purge_missing"),
            f"這 {len(dead)} 筆的原始檔和 AICC 的副本都已經不在了：\n\n"
            f"{listed}{extra}\n\n"
            "清掉的是索引紀錄，不會動到你磁碟上的任何檔案——"
            "本來也已經沒有檔案可以動了。\n\n"
            "要清掉嗎？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        store.remove_documents(dead)
        # Compared as strings: the page's selection is the id as text, and an
        # int() on it here would raise on any value that is not a number.
        if self._selected in {str(i) for i in dead}:
            self._selected = None

    def _run_action(self, kind: str, arg: str) -> None:
        store = self.store
        if kind in self.FILE_DIALOGS:
            title_key, filter_key = self.FILE_DIALOGS[kind]
            title, filters = t(title_key), t(filter_key)
            chosen, _ = QFileDialog.getOpenFileNames(self, title, "", filters)
            if not chosen:
                return
            # The two entries share a dialog, not an importer. Sharing both is
            # what made 匯入舊對話 index a ChatGPT export as raw JSON.
            if kind == "import_transcript":
                self._import_transcripts([Path(p) for p in chosen])
            else:
                self._import([Path(p) for p in chosen])
        elif kind == "import_folder":
            folder = QFileDialog.getExistingDirectory(self, t("documents.import_folder"))
            if folder:
                self._import(None, folder=Path(folder))
        elif kind in ("scan_project", "rescan", "reindex"):
            # rescan is "re-read the list", scan_project is "index the project".
            # Both end in a fresh push; only the latter walks the disk.
            if kind != "rescan":
                store.rebuild_project_index()
        elif kind == "reindex_all":
            store.refresh_all_documents()
        elif kind == "reparse_stale":
            store.refresh_documents(store.stale_document_ids())
        elif kind == "remove_dead":
            self._remove_dead()
        elif kind == "detect_changes":
            # Nothing to do but re-read: collect_state() already reports each
            # document's update status, so pushing is the whole action.
            pass
        elif kind == "import_wizard":
            self.file_opened.emit(str(store.run_import_wizard(arg)))
        elif kind == "preview_relevant":
            self.report_ready.emit(store.build_context(arg))
        elif kind in ("reparse", "remove", "open", "show_artifacts"):
            self._run_document_action(kind, arg)

    def _run_document_action(self, kind: str, arg: str) -> None:
        """The four that act on the selected document rather than the project.

        The page sends the selection as the argument, but a selection made
        before a rescan can name a document that no longer exists - so this
        resolves the id and does nothing if it has gone, rather than raising
        from inside a store call.
        """
        try:
            document_id = int(arg)
        except (TypeError, ValueError):
            return
        known = {doc.document_id for doc in self.store.list_documents()}
        if document_id not in known:
            return

        if kind == "reparse":
            self.store.refresh_document(document_id)
        elif kind == "remove":
            # delete_copy stays False: this removes the document from the index,
            # it does not delete the user's original file. The page's two-step
            # confirmation is about the index entry.
            self.store.remove_document(document_id, delete_copy=False)
            if self._selected == arg:
                self._selected = None
        elif kind in ("open", "show_artifacts"):
            self.report_ready.emit(self.store.document_preview(document_id))
