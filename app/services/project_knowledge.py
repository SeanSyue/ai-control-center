from __future__ import annotations

from app.i18n import t

import csv
import hashlib
import html
import json
import re
import shutil
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import unified_diff
from pathlib import Path
from typing import Iterable

from app.services.security_scan import SecurityScanner


SUPPORTED_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".xml",
    ".sql",
    ".ps1",
    ".bat",
    ".cmd",
    ".sh",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
}

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 2_000_000
MAX_SCAN_FILES = 3000
CHUNK_SIZE = 2400
CHUNK_OVERLAP = 240
# 使用者自己訂的「這個專案的規矩」。**刻意不在 `MEMORY_FILES` 裡面。**
#
# 那八份是 AICC 維護的（跑完之後 Codex 起草、Claude 複審、才寫回去）。
# 這一份是**他寫的**，AICC 只讀不寫——放進那張表的話，記憶維護那一步會把
# 它當成自己的東西改掉，而他不會收到任何一個字。
#
# （2026-08-23 真的試過放進去：`test_the_memory_prompt_asks_for_exactly_the_
# files_the_writer_accepts` 立刻變紅。那條紅燈是對的。）
from app.core import rule_layers  # noqa: E402

PROJECT_RULES_FILE = "PROJECT_RULES.md"

MEMORY_FILES = {
    "PROJECT_BRIEF.md": "Project Brief",
    "ARCHITECTURE.md": "Architecture",
    "DECISIONS.md": "Decisions",
    "SECURITY_MODEL.md": "Security Model",
    "PRODUCT_REVIEW.md": "Product Review",
    "BUSINESS_MODEL.md": "Business Model",
    "NEXT_STEPS.md": "Next Steps",
    "OPEN_QUESTIONS.md": "Open Questions",
}
# 這兩份不是「比較重要」，是**少一句就等於失效**：安全界線，以及明確不准 AI
# 自己決定的清單。清單型的硬界線少一項就等於沒有那一項，而少掉的那一項在
# 畫面上看不出來——被截斷的安全規則讀起來跟完整的一模一樣。
#
# 這個 tuple 存在的理由是它有牙齒：`_assert_safety_memory_intact` 會在組裝
# 完成之後回頭比對原文，對不上就丟 `MemoryTruncatedError`。少了這一步，
# 「不准截斷」只是一句叮嚀。
MEMORY_MUST_ARRIVE_WHOLE = ("SECURITY_MODEL.md", "OPEN_QUESTIONS.md")
HANDOFF_PACKAGE_FILE = "HANDOFF_PACKAGE.md"
TASK_CONTEXT_FILE = "TASK_CONTEXT.md"
SESSION_BOOTSTRAP_FILE = "SESSION_BOOTSTRAP.md"
IMPORT_WIZARD_REPORT_FILE = "IMPORT_WIZARD_REPORT.md"
GENERATED_KNOWLEDGE_FILES = (
    TASK_CONTEXT_FILE,
    HANDOFF_PACKAGE_FILE,
    SESSION_BOOTSTRAP_FILE,
    IMPORT_WIZARD_REPORT_FILE,
)
MEMORY_TEMPLATE = """# {title}

Update this shared project memory from AI Control Center.
"""


class MemoryTruncatedError(RuntimeError):
    """共同記憶裡「不准被截斷」的那幾份沒有完整抵達。

    刻意是例外而不是回傳值：一個被截斷的安全規則，跟完整的安全規則，在
    prompt 裡讀起來一模一樣。所以這件事必須大聲失敗，而不是回一個沒有人
    檢查的旗標。

    上游怎麼接（2026-08-21 訂正）：**三條路都是「停下這一輪」**——
    `Orchestrator._knowledge_context`、`run_codex_decision_gate`、
    `run_codex_memory_update` 全部經過 `Orchestrator._stop_on_cut_memory`，
    在時間軸記一筆 `memory_truncated` 之後把例外往上丟，由 `WorkflowWorker`
    收成 `MEMORY_TRUNCATED` 這個 `partial` 結局。

    前一版寫的是「接住之後這一輪不帶任何專案脈絡照跑」，那是錯的，而且錯得
    不明顯：空脈絡到了 `prompt_builder` 會變成「（尚未建立專案資料索引，請依
    目前專案檔案判斷。）」——索引明明建好了，被切的是安全規則，模型卻收到
    一句與事實不符的話，還被鼓勵繼續做下去。「AI 知道自己沒有資料」在那條路
    上從來沒有發生過。
    """


@dataclass(frozen=True)
class KnowledgeDocument:
    document_id: int
    source_type: str
    display_name: str
    source_path: str
    stored_path: str
    extension: str
    size_bytes: int
    modified_at: str
    indexed_at: str
    chunk_count: int
    status: str
    error: str = ""


@dataclass(frozen=True)
class KnowledgeChunk:
    document_id: int
    display_name: str
    source_path: str
    chunk_index: int
    content: str
    score: float = 0.0
    # Why this chunk is in the context (ROADMAP P0.5「在 UI 裡顯示為什麼這些
    # 資料會被帶入」). One of:
    #
    #   keyword   - the full-text index matched terms from the task
    #   substring - the index matched nothing, so a plain LIKE was used
    #   recent    - **nothing was matched against**. The task yielded no usable
    #               search terms, so the most recently indexed chunks were sent
    #               instead. They may have nothing to do with the task.
    #
    # `recent` is the reason this field exists. It is the one case where the
    # honest answer to 「為什麼帶這些資料」 is 「沒有為什麼」, and until now it
    # looked identical on screen to a good match.
    reason: str = "keyword"
    matched_terms: tuple[str, ...] = ()
    # "project" or "imported". Needed in the context itself, not just in the
    # document list: an imported transcript is somebody's conversation from
    # months ago, and the model has no way to tell that from a current source
    # file unless the context says so.
    source_type: str = "project"


@dataclass(frozen=True)
class ContextProfile:
    """一次執行要撈多少**專案文件**當佐證。

    2026-08-21 這裡少了一個欄位：`memory_chars`。它管的是共同記憶那八份可以
    送出多少字，超過就切——而八份共同記憶正是「每次都必須知道、又查不到」的
    那一份東西，切它等於把硬規則切掉。實測（出廠預設 minimal／6,000）：
    八份**全部**被切成 750 字，而且三份的切點都在句子中間：
    `SECURITY_MODEL.md` 斷在「執行期狀態（DB、鎖、快取、暫」——「暫存」兩個
    字被切成兩半；`PROJECT_BRIEF.md` 斷在「不進 Git、不進匯出；不准靠」；
    `OPEN_QUESTIONS.md` 斷在「要不要先有花費上」。

    欄位是整個拿掉的，不是設成一個很大的數字。留著一個「上限」欄位就等於留著
    一條隨時會被重新接起來的線；而且 `build_context` 當年就是在配額之外**又**
    切了一刀（`memory_context[: profile.memory_chars]`，連「節錄」標籤都沒有）。
    沒有那個數字，就沒有東西可以拿來切。

    profile 仍然有意義，只是它管的是**佐證的廣度**（撈幾段、多少字），那是
    真正會隨任務膨脹、也真正該由使用者控制的那一半。共同記憶不受它管。
    """

    name: str
    label: str
    max_chunks: int
    document_chars: int


@dataclass(frozen=True)
class ContextBudget:
    profile: str
    profile_label: str
    contract_characters: int
    memory_characters: int
    document_characters: int
    raw_evidence_characters: int
    total_characters: int
    estimated_tokens: int
    documents: int
    chunks: int
    omitted_memory_characters: int
    omitted_document_characters: int
    repeated_history_avoided_characters: int = 0

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


CONTEXT_PROFILES = {
    "minimal": ContextProfile(
        name="minimal",
        label="Minimal",
        max_chunks=4,
        document_chars=7000,
    ),
    "balanced": ContextProfile(
        name="balanced",
        label="Balanced",
        max_chunks=10,
        document_chars=18000,
    ),
    "strict": ContextProfile(
        name="strict",
        label="Strict",
        max_chunks=16,
        document_chars=32000,
    ),
}

DEFAULT_CONTRACT_CHARACTERS = 2400
_MEMORY_SECURITY_SCANNER = SecurityScanner()
MEMORY_HIGH_RISK_TERMS = (
    "delete production",
    "drop table",
    "reset --hard",
    "oauth token",
    "channel access token",
)


@dataclass(frozen=True)
class ImportInspection:
    """What a file would bring in, checked before it is indexed.

    ROADMAP P1「匯入敏感文件前提示」. Importing is not「複製一個檔案」: the
    content goes into the search index, and from there it can be selected into
    the context sent to Claude or Codex. That is a path from 「我硬碟上的一個
    檔案」 to 「送給廠商的一段文字」, and nothing on screen said so.
    """

    path: str
    name: str
    findings: tuple[str, ...] = ()

    @property
    def flagged(self) -> bool:
        return bool(self.findings)


@dataclass(frozen=True)
class ImportResult:
    imported: tuple[str, ...]
    skipped: tuple[str, ...]
    errors: tuple[str, ...]
    # Files held back because the scanner found something, listed separately
    # from `skipped`. They are different events: skipped means 「這不是一個可以
    # 匯入的檔案」, flagged means 「可以，但你要先看一眼」 - and merging them
    # would let a withheld secret read as a broken path.
    flagged: tuple[ImportInspection, ...] = ()

    @property
    def summary(self) -> str:
        base = (
            f"匯入 {len(self.imported)}、"
            f"略過 {len(self.skipped)}、"
            f"錯誤 {len(self.errors)}"
        )
        if self.flagged:
            base += f"、{len(self.flagged)} 個需要你確認"
        return base


@dataclass(frozen=True)
class KnowledgeReadinessReport:
    document_count: int
    project_document_count: int
    imported_document_count: int
    chunk_count: int
    memory_file_count: int
    missing_memory_files: tuple[str, ...]
    updated_document_count: int
    missing_source_count: int
    parse_error_count: int
    ready: bool
    next_steps: tuple[str, ...]


class ProjectKnowledgeStore:
    """Local project document index with conservative context retrieval.

    Imported external files are copied into .aicc/imported-context so they can
    travel with the project's AICC metadata. Project source files are indexed
    in place and are never copied.
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path.expanduser().resolve()
        self.aicc_path = self.project_path / ".aicc"
        self.knowledge_path = self.aicc_path / "knowledge"
        self.import_path = self.aicc_path / "imported-context"
        self.database_path = self.knowledge_path / "index.sqlite"
        self.summary_path = self.knowledge_path / "PROJECT_BRIEF.md"
        self.knowledge_path.mkdir(parents=True, exist_ok=True)
        self.import_path.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def set_project(self, project_path: Path) -> None:
        self.__init__(project_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def checkpoint(self) -> bool:
        """Fold the write-ahead log back into `index.sqlite`.

        WAL mode means recent writes live in `index.sqlite-wal` until SQLite
        merges them. That is invisible while the three files stay together -
        and **the Workspace export deliberately copies only the first one**
        (`-wal` and `-shm` are transient, so `EXCLUDED_NAMES` drops them).

        The result was an export that carried a structurally valid, *empty*
        index: the file landed on the new machine, the project opened, and
        every document was gone. Nothing failed. 「這個專案沒有文件」 and
        「你的文件全部沒有跟過來」 looked identical, and every prompt on the
        new machine went out with no project context.

        Returns False rather than raising if the checkpoint cannot run - the
        caller decides whether that is fatal, and an export that fails here
        must not look like an export that succeeded.
        """
        if not self.database_path.is_file():
            return False
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return True
        except sqlite3.Error:
            return False

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    source_path TEXT NOT NULL UNIQUE,
                    stored_path TEXT NOT NULL DEFAULT '',
                    extension TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    modified_at TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'indexed',
                    error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id)
                        ON DELETE CASCADE,
                    UNIQUE(document_id, chunk_index)
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_document
                    ON chunks(document_id);
                """
            )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                        content,
                        content='chunks',
                        content_rowid='id',
                        tokenize='unicode61'
                    )
                    """
                )
                connection.executescript(
                    """
                    CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                      INSERT INTO chunks_fts(rowid, content)
                      VALUES (new.id, new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                      INSERT INTO chunks_fts(chunks_fts, rowid, content)
                      VALUES ('delete', old.id, old.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                      INSERT INTO chunks_fts(chunks_fts, rowid, content)
                      VALUES ('delete', old.id, old.content);
                      INSERT INTO chunks_fts(rowid, content)
                      VALUES (new.id, new.content);
                    END;
                    """
                )
            except sqlite3.OperationalError:
                # SQLite builds without FTS5 still work through LIKE fallback.
                pass

    def inspect_import(
        self, paths: Iterable[Path]
    ) -> tuple[ImportInspection, ...]:
        """What the scanner finds in each file, before anything is indexed.

        ROADMAP P1「匯入敏感文件前提示」. Only the files it has something to say
        about are returned - a list that includes every clean file is a list
        nobody reads, and the point is to make the flagged ones visible.

        Uses the same `SecurityScanner` the workspace export uses, so 「匯出會
        被擋的東西」 and 「匯入會被提醒的東西」 are the same set. Two different
        definitions of 「機密」 in one app is how one of them ends up wrong.
        """
        found: list[ImportInspection] = []
        for raw_path in paths:
            path = Path(raw_path).expanduser().resolve()
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            report = _MEMORY_SECURITY_SCANNER.scan_text(text, path=str(path))
            if not report.has_blocking_findings:
                continue
            found.append(
                ImportInspection(
                    path=str(path),
                    name=path.name,
                    findings=tuple(
                        sorted({finding.kind for finding in report.findings})
                    ),
                )
            )
        return tuple(found)

    def import_files(
        self,
        paths: Iterable[Path],
        *,
        allow_flagged: bool = False,
    ) -> ImportResult:
        """Index these files. Anything the scanner flags is held back.

        `allow_flagged` is the caller saying 「我看過了，還是要匯入」. Default
        False so the safe path is the one you get by not thinking about it -
        and held back rather than blocked outright, because a project whose
        whole purpose is client documents will legitimately contain things
        that look like secrets. The decision is the user's; making it silently
        is what this stops.
        """
        imported: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []
        flagged = () if allow_flagged else self.inspect_import(paths)
        withheld = {item.path for item in flagged}

        for raw_path in paths:
            path = Path(raw_path).expanduser().resolve()
            if not path.is_file():
                skipped.append(str(path))
                continue
            if str(path) in withheld:
                continue
            try:
                try:
                    path.relative_to(self.project_path)
                    inside_project = ".aicc" not in path.parts
                except ValueError:
                    inside_project = False

                if inside_project:
                    self.index_file(path, source_type="project")
                else:
                    destination = self._copy_import_file(path)
                    self.index_file(
                        destination,
                        source_type="imported",
                        original_source_path=path,
                    )
                imported.append(path.name)
            except (OSError, ValueError, sqlite3.Error) as exc:
                errors.append(f"{path.name}：{exc}")

        return ImportResult(
            imported=tuple(imported),
            skipped=tuple(skipped),
            errors=tuple(errors),
            flagged=flagged,
        )

    def import_folder(self, folder: Path) -> ImportResult:
        root = folder.expanduser().resolve()
        if not root.is_dir():
            return ImportResult((), (), (f"資料夾不存在：{root}",))

        candidates: list[Path] = []
        for path in root.rglob("*"):
            if len(candidates) >= MAX_SCAN_FILES:
                break
            if self._should_include(path):
                candidates.append(path)
        return self.import_files(candidates)

    def rebuild_project_index(self) -> ImportResult:
        indexed: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []
        count = 0

        with self._connect() as connection:
            project_rows = connection.execute(
                "SELECT id FROM documents WHERE source_type = 'project'"
            ).fetchall()
            for row in project_rows:
                connection.execute(
                    "DELETE FROM documents WHERE id = ?",
                    (row["id"],),
                )

        for path in self.project_path.rglob("*"):
            if count >= MAX_SCAN_FILES:
                skipped.append(
                    f"已達掃描上限 {MAX_SCAN_FILES} 個檔案"
                )
                break
            if not self._should_include(path, include_aicc=False):
                continue
            count += 1
            try:
                self.index_file(path, source_type="project")
                indexed.append(
                    path.relative_to(self.project_path).as_posix()
                )
            except (OSError, ValueError, sqlite3.Error) as exc:
                errors.append(
                    f"{path.relative_to(self.project_path)}：{exc}"
                )

        return ImportResult(
            imported=tuple(indexed),
            skipped=tuple(skipped),
            errors=tuple(errors),
        )

    def index_file(
        self,
        path: Path,
        source_type: str,
        original_source_path: Path | None = None,
    ) -> KnowledgeDocument:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"檔案不存在：{resolved}")
        if resolved.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"尚未支援格式：{resolved.suffix or '無副檔名'}")
        size = resolved.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(
                f"檔案超過 {MAX_FILE_BYTES // (1024 * 1024)} MB 上限"
            )

        text = self._extract_text(resolved)
        chunks = self._split_chunks(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        source_path = str((original_source_path or resolved).resolve())
        stored_path = ""
        try:
            stored_path = resolved.relative_to(self.project_path).as_posix()
        except ValueError:
            stored_path = str(resolved)
        modified_at = datetime.fromtimestamp(
            resolved.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
        indexed_at = datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id, content_hash FROM documents WHERE source_path = ?",
                (source_path,),
            ).fetchone()
            if existing and existing["content_hash"] == digest:
                row = connection.execute(
                    "SELECT * FROM documents WHERE id = ?",
                    (existing["id"],),
                ).fetchone()
                return self._row_to_document(row)

            if existing:
                document_id = int(existing["id"])
                connection.execute(
                    "DELETE FROM chunks WHERE document_id = ?",
                    (document_id,),
                )
                connection.execute(
                    """
                    UPDATE documents SET
                        source_type = ?, display_name = ?, stored_path = ?,
                        extension = ?, size_bytes = ?, modified_at = ?,
                        indexed_at = ?, content_hash = ?, chunk_count = ?,
                        status = 'indexed', error = ''
                    WHERE id = ?
                    """,
                    (
                        source_type,
                        resolved.name,
                        stored_path,
                        resolved.suffix.lower(),
                        size,
                        modified_at,
                        indexed_at,
                        digest,
                        len(chunks),
                        document_id,
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO documents(
                        source_type, display_name, source_path, stored_path,
                        extension, size_bytes, modified_at, indexed_at,
                        content_hash, chunk_count, status, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexed', '')
                    """,
                    (
                        source_type,
                        resolved.name,
                        source_path,
                        stored_path,
                        resolved.suffix.lower(),
                        size,
                        modified_at,
                        indexed_at,
                        digest,
                        len(chunks),
                    ),
                )
                document_id = int(cursor.lastrowid)

            connection.executemany(
                """
                INSERT INTO chunks(document_id, chunk_index, content)
                VALUES (?, ?, ?)
                """,
                [
                    (document_id, index, chunk)
                    for index, chunk in enumerate(chunks)
                ],
            )
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
            return self._row_to_document(row)

    def list_documents(self) -> list[KnowledgeDocument]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM documents
                ORDER BY source_type, display_name COLLATE NOCASE
                """
            ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def document_update_statuses(self) -> dict[int, str]:
        return {
            document.document_id: self.document_update_status(document)
            for document in self.list_documents()
        }

    def import_readiness_report(self) -> KnowledgeReadinessReport:
        documents = self.list_documents()
        update_statuses = {
            document.document_id: self.document_update_status(document)
            for document in documents
        }
        existing_memory = self.list_memory_files()
        missing_memory = tuple(
            file_name
            for file_name in MEMORY_FILES
            if file_name not in existing_memory
        )
        updated_count = sum(
            status == "updated" for status in update_statuses.values()
        )
        missing_source_count = sum(
            status in {"missing", "source_missing"}
            for status in update_statuses.values()
        )
        parse_error_count = sum(
            document.status != "indexed" for document in documents
        )
        next_steps: list[str] = []
        if missing_memory:
            next_steps.append("Create shared memory files.")
        if not documents:
            next_steps.append("Scan the project or import reference documents.")
        if updated_count:
            next_steps.append("Re-parse updated source documents.")
        if missing_source_count:
            next_steps.append("Review documents with missing original files.")
        if parse_error_count:
            next_steps.append("Open failed documents and fix parse errors.")
        if not next_steps:
            next_steps.append("Ready for a Codex -> Claude -> Codex handoff task.")

        return KnowledgeReadinessReport(
            document_count=len(documents),
            project_document_count=sum(
                document.source_type == "project" for document in documents
            ),
            imported_document_count=sum(
                document.source_type == "imported" for document in documents
            ),
            chunk_count=sum(document.chunk_count for document in documents),
            memory_file_count=len(existing_memory),
            missing_memory_files=missing_memory,
            updated_document_count=updated_count,
            missing_source_count=missing_source_count,
            parse_error_count=parse_error_count,
            ready=not missing_memory
            and bool(documents)
            and updated_count == 0
            and missing_source_count == 0
            and parse_error_count == 0,
            next_steps=tuple(next_steps),
        )

    def format_readiness_report(self) -> str:
        report = self.import_readiness_report()
        missing_memory = (
            "\n".join(f"- {item}" for item in report.missing_memory_files)
            if report.missing_memory_files
            else "- None"
        )
        next_steps = "\n".join(f"- {item}" for item in report.next_steps)
        status = "Ready" if report.ready else "Needs attention"
        return (
            "## Import Readiness\n\n"
            f"Status: {status}\n"
            f"Documents: {report.document_count}\n"
            f"Project documents: {report.project_document_count}\n"
            f"Imported documents: {report.imported_document_count}\n"
            f"Chunks: {report.chunk_count}\n"
            f"Shared memory files: {report.memory_file_count}/{len(MEMORY_FILES)}\n"
            f"Updated source documents: {report.updated_document_count}\n"
            f"Missing source documents: {report.missing_source_count}\n"
            f"Parse errors: {report.parse_error_count}\n\n"
            "Missing memory files:\n"
            f"{missing_memory}\n\n"
            "Suggested next steps:\n"
            f"{next_steps}\n"
        )

    def document_update_status(self, document: KnowledgeDocument) -> str:
        source = Path(document.source_path)
        stored = self._stored_document_path(document)
        candidate = source if source.is_file() else stored
        if candidate is None or not candidate.is_file():
            return "missing"

        if not source.is_file() and stored is not None and stored.is_file():
            return "source_missing"

        try:
            modified_at = datetime.fromtimestamp(
                source.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat()
        except OSError:
            return "missing"
        if modified_at != document.modified_at:
            return "updated"
        return "current"

    def refresh_document(self, document_id: int) -> KnowledgeDocument | None:
        document = self._get_document(document_id)
        if document is None:
            return None

        source = Path(document.source_path)
        stored = self._stored_document_path(document)
        source_exists = source.is_file()
        stored_exists = stored is not None and stored.is_file()
        if not source_exists and not stored_exists:
            return self._mark_document_error(
                document_id,
                "missing",
                "Original file and stored copy are both missing.",
            )

        try:
            if document.source_type == "imported":
                original = source if source_exists else Path(document.source_path)
                target = self._copy_import_file(source) if source_exists else stored
                if target is None:
                    raise ValueError("Stored imported copy is missing.")
                return self.index_file(
                    target,
                    source_type="imported",
                    original_source_path=original,
                )
            target = source if source_exists else stored
            if target is None:
                raise ValueError("Project file is missing.")
            return self.index_file(target, source_type=document.source_type)
        except (OSError, ValueError, sqlite3.Error) as exc:
            return self._mark_document_error(document_id, "error", str(exc))

    def refresh_all_documents(self) -> ImportResult:
        return self.refresh_documents(
            document.document_id for document in self.list_documents()
        )

    def refresh_documents(self, document_ids: Iterable[int]) -> ImportResult:
        """Re-parse a chosen subset (ROADMAP P1「批次重新解析」).

        `refresh_all_documents` is this with every id, which is the whole
        reason it is written this way round: two loops doing the same thing is
        how the subset one ends up not reporting errors the same way.
        """
        names = {
            document.document_id: document.display_name
            for document in self.list_documents()
        }
        refreshed: list[str] = []
        errors: list[str] = []
        for document_id in document_ids:
            updated = self.refresh_document(document_id)
            if updated is None:
                label = names.get(document_id, str(document_id))
                errors.append(f"{label}: document not found")
                continue
            if updated.status == "indexed":
                refreshed.append(updated.display_name)
            else:
                errors.append(f"{updated.display_name}: {updated.error}")
        return ImportResult(tuple(refreshed), (), tuple(errors))

    def stale_document_ids(self) -> tuple[int, ...]:
        """Documents whose original has been edited since it was indexed.

        The actionable set behind the 原檔已變更 badge: what a prompt carries
        for these is the old text.
        """
        return tuple(
            document_id
            for document_id, status in self.document_update_statuses().items()
            if status == "updated"
        )

    def dead_document_ids(self) -> tuple[int, ...]:
        """Index entries with no file behind them any more.

        **`source_missing` is deliberately not in here**, and that is the
        entire point of this method existing rather than a `status in
        {...}` at the call site.

        | status | 原檔 | AICC 的副本 | 刪掉會怎樣 |
        |---|---|---|---|
        | `missing` | 沒了 | 沒有／也沒了 | 只是清掉一個指向空氣的紀錄 |
        | `source_missing` | 沒了 | **還在** | 把僅存的一份藏起來 |

        `source_missing` looks like the strongest candidate for a cleanup
        sweep - it is the one that reads 「原檔不存在」 - and it is the one
        entry where the copy AICC holds is the last copy in existence. A batch
        button that swept it up would quietly de-index exactly the documents
        the user can no longer recover from anywhere else. Removing those stays
        one at a time, where the user is looking at the file they are removing.
        """
        return tuple(
            document_id
            for document_id, status in self.document_update_statuses().items()
            if status == "missing"
        )

    def remove_documents(self, document_ids: Iterable[int]) -> int:
        """Drop several index entries. Never touches originals - see
        `remove_document`, which this calls with its `delete_copy` default.
        """
        removed = 0
        for document_id in document_ids:
            self.remove_document(document_id)
            removed += 1
        return removed

    def remove_document(self, document_id: int, delete_copy: bool = False) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
            if row is None:
                return
            stored_path = str(row["stored_path"])
            source_type = str(row["source_type"])
            connection.execute(
                "DELETE FROM documents WHERE id = ?",
                (document_id,),
            )

        if delete_copy and source_type == "imported" and stored_path:
            target = (self.project_path / stored_path).resolve()
            try:
                target.relative_to(self.import_path.resolve())
            except ValueError:
                return
            try:
                target.unlink(missing_ok=True)
            except OSError:
                return

    def document_preview(self, document_id: int, max_chars: int = 12000) -> str:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT content FROM chunks
                WHERE document_id = ?
                ORDER BY chunk_index
                """,
                (document_id,),
            ).fetchall()
        text = "\n\n".join(str(row["content"]) for row in rows)
        if len(text) > max_chars:
            return text[:max_chars].rstrip() + t("knowledge.preview_truncated")
        return text

    def search(
        self,
        query: str,
        max_chunks: int = 10,
        max_chars: int = 18000,
    ) -> list[KnowledgeChunk]:
        terms = self._query_terms(query)
        if not terms:
            return self._fallback_recent_chunks(max_chunks, max_chars)

        rows: list[sqlite3.Row] = []
        with self._connect() as connection:
            fts_query = " OR ".join(f'"{term}"' for term in terms[:12])
            try:
                rows = connection.execute(
                    """
                    SELECT
                        c.document_id,
                        d.display_name,
                        d.source_path,
                        d.source_type,
                        c.chunk_index,
                        c.content,
                        bm25(chunks_fts) AS rank
                    FROM chunks_fts
                    JOIN chunks c ON c.id = chunks_fts.rowid
                    JOIN documents d ON d.id = c.document_id
                    WHERE chunks_fts MATCH ?
                    ORDER BY rank ASC
                    LIMIT ?
                    """,
                    (fts_query, max_chunks * 3),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []

            # LIKE runs **as well as** FTS, not only when FTS found nothing.
            #
            # FTS5's default tokenizer treats a whitespace-delimited run of
            # CJK as one token, so 「開戶討論」 matches a chunk containing
            # 「⋯ 開戶討論」 and does **not** match one containing
            # 「開戶討論紀錄」. Both are obvious hits to a person.
            #
            # The old code ran LIKE only when FTS returned nothing, so a query
            # that matched one document by token silently lost every document
            # LIKE would have found. Measured: three chunks contain 「開戶討論」,
            # LIKE finds all three, `search` returned two. **Partial success
            # from FTS was worse than total failure** - total failure at least
            # fell through to the path that works.
            #
            # FTS rows stay first so bm25 ordering still leads where it
            # applies; the dedupe below drops the overlap.
            fts_keys = {
                (int(row["document_id"]), int(row["chunk_index"]))
                for row in rows
            }
            conditions = " OR ".join("c.content LIKE ?" for _ in terms[:8])
            parameters = [f"%{term}%" for term in terms[:8]]
            parameters.append(max_chunks * 3)
            like_rows = connection.execute(
                f"""
                SELECT
                    c.document_id,
                    d.display_name,
                    d.source_path,
                    d.source_type,
                    c.chunk_index,
                    c.content,
                    0.0 AS rank
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE {conditions}
                ORDER BY d.indexed_at DESC, c.chunk_index ASC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            rows = list(rows) + list(like_rows)

        results: list[KnowledgeChunk] = []
        used_chars = 0
        seen: set[tuple[int, int]] = set()
        for row in rows:
            key = (int(row["document_id"]), int(row["chunk_index"]))
            if key in seen:
                continue
            # Per row now, not per query: the same search can produce both
            # kinds, and 「這一段是索引比到的還是字串比到的」 is only useful
            # if it is true of that段 rather than of the batch.
            reason = "keyword" if key in fts_keys else "substring"
            content = str(row["content"]).strip()
            if not content:
                continue
            if used_chars + len(content) > max_chars and results:
                break
            seen.add(key)
            results.append(
                KnowledgeChunk(
                    document_id=key[0],
                    display_name=str(row["display_name"]),
                    source_path=str(row["source_path"]),
                    chunk_index=key[1],
                    content=content,
                    score=float(row["rank"] or 0.0),
                    reason=reason,
                    matched_terms=tuple(terms[:12]),
                    source_type=str(row["source_type"] or "project"),
                )
            )
            used_chars += len(content)
            if len(results) >= max_chunks:
                break
        return results

    def build_context(
        self,
        task: str,
        max_chunks: int = 10,
        max_chars: int = 18000,
        context_profile: str = "balanced",
        duty: str = "",
    ) -> str:
        """`duty` 給了就走【規則分三層】那條路（底座＋點名），空的就是八份全文。

        空的那條**不是**「預設值」，是給**人看的文件**用的（交接包、啟動脈絡、
        文件頁的預覽）——那些是他自己要讀的東西，不是派工。
        """
        profile = self._context_profile(context_profile)
        max_chunks = min(max_chunks, profile.max_chunks)
        max_chars = min(max_chars, profile.document_chars)
        chunks = self.search(task, max_chunks=max_chunks, max_chars=max_chars)
        memory_context = self._memory_context(duty=duty)
        sections: list[str] = []
        if memory_context:
            # 這裡本來是 `memory_context[: profile.memory_chars]`——在配額之外
            # **又**切了一刀，而且是無聲的：連「節錄 N／M 字」那個標籤都在切
            # 之前就被切掉了。兩層截斷疊在一起，是為什麼「已修好」的分配邏輯
            # 在畫面上看起來沒問題、實際送出去的仍然是半份安全規則。
            sections.append("## Shared Project Memory\n" + memory_context)
        if chunks:
            context_lines = ["## Relevant Project Documents"]
            for chunk in chunks:
                # An imported chunk is somebody's conversation from months
                # ago. Unlabelled, 「我們決定用 X」 out of a transcript reads as
                # a current fact about the project - the two are identical
                # once they are both just text under this heading. ROADMAP P7
                # 「從摘要追溯到原始對話來源」, the chunk→transcript hop.
                origin = (
                    "\n(Imported past conversation, not a current project "
                    "file. Statements in it may be out of date.)"
                    if chunk.source_type == "imported"
                    else ""
                )
                context_lines.append(
                    "\n"
                    f"### {chunk.display_name} / chunk {chunk.chunk_index + 1}\n"
                    f"Source: {chunk.source_path}{origin}\n"
                    f"{chunk.content}"
                )
            sections.append("\n".join(context_lines))
        context = "\n\n".join(sections).strip()
        # 組完之後再驗一次。這一層才是真的會送出去的字串，而那句被拿掉的
        # `[: profile.memory_chars]` 就是加在這一層的——只驗
        # `build_memory_context()` 自己的輸出擋不住下游再切一刀。
        self._assert_safety_memory_intact(context)
        return context

    def context_budget(
        self,
        task: str,
        context_profile: str = "balanced",
        contract_characters: int = DEFAULT_CONTRACT_CHARACTERS,
    ) -> ContextBudget:
        profile = self._context_profile(context_profile)
        chunks = self.search(
            task,
            max_chunks=profile.max_chunks,
            max_chars=profile.document_chars,
        )
        document_characters = sum(len(chunk.content) for chunk in chunks)
        memory_context = self.build_memory_context()
        memory_characters = len(memory_context)
        raw_memory_characters = self.memory_total_characters()
        raw_chunks = self.search(task, max_chunks=50, max_chars=100000)
        raw_document_characters = sum(len(chunk.content) for chunk in raw_chunks)
        raw_evidence_characters = raw_memory_characters + raw_document_characters
        total_characters = (
            int(contract_characters)
            + memory_characters
            + document_characters
        )
        return ContextBudget(
            profile=profile.name,
            profile_label=profile.label,
            contract_characters=int(contract_characters),
            memory_characters=memory_characters,
            document_characters=document_characters,
            raw_evidence_characters=raw_evidence_characters,
            total_characters=total_characters,
            estimated_tokens=max(0, total_characters // 3),
            documents=len({chunk.document_id for chunk in chunks}),
            chunks=len(chunks),
            omitted_memory_characters=max(
                0,
                raw_memory_characters - memory_characters,
            ),
            omitted_document_characters=max(
                0,
                raw_document_characters - document_characters,
            ),
        )

    def selection_note(
        self,
        task: str,
        context_profile: str = "balanced",
    ) -> str:
        """One sentence saying **why** these documents were chosen, or "".

        ROADMAP P0.5「在 UI 裡顯示為什麼這些資料會被帶入」. The counts already
        said how much went in; nothing said on what grounds.

        Speaks up for exactly one case, and stays silent otherwise. A line on
        every run is a line people stop reading, and then it is not there when
        it matters.

        The case it speaks for is **`recent`**: the task produced no usable
        search terms, so the most recently indexed chunks were sent. They may
        be about something else entirely. That run looks exactly like a
        well-matched one on screen, and the model is being handed unrelated
        material as if it were context.

        **`substring` deliberately says nothing**, and the reason is worth
        knowing: SQLite FTS5's default tokenizer does not segment CJK, so a
        Chinese task never matches the full-text index and *always* falls
        through to LIKE. Measured, not assumed - `installer pytest` takes the
        keyword path and 「設定頁的儲存列」 does not. A note there would fire on
        every Chinese task, and it would give wrong advice: re-indexing does
        not help, because nothing is stale. See ROADMAP P0.5.
        """
        chunks = self.search(
            task,
            max_chunks=self._context_profile(context_profile).max_chunks,
            max_chars=self._context_profile(context_profile).document_chars,
        )
        if not chunks:
            return ""
        reasons = {chunk.reason for chunk in chunks}
        if reasons == {"recent"}:
            return (
                t("knowledge.no_keywords_note")
            )
        return ""

    def context_stats(
        self,
        task: str,
        context_profile: str = "balanced",
    ) -> dict[str, int]:
        budget = self.context_budget(task, context_profile=context_profile)
        return {
            "documents": budget.documents,
            "chunks": budget.chunks,
            "document_characters": budget.document_characters,
            "memory_characters": budget.memory_characters,
            "characters": (
                budget.memory_characters + budget.document_characters
            ),
            "estimated_tokens": budget.estimated_tokens,
            "contract_characters": budget.contract_characters,
            "total_characters": budget.total_characters,
            "omitted_memory_characters": budget.omitted_memory_characters,
            "omitted_document_characters": budget.omitted_document_characters,
            "raw_evidence_characters": budget.raw_evidence_characters,
        }

    def write_task_context(self, task: str) -> Path:
        context = self.build_context(task)
        stats = self.context_stats(task)
        generated_at = datetime.now(timezone.utc).isoformat()
        content = (
            "# AI Control Center Task Context\n\n"
            f"Generated at: {generated_at}\n"
            f"Project: {self.project_path}\n\n"
            "## Task\n\n"
            f"{task.strip() or '_No task provided._'}\n\n"
            "## Context Estimate\n\n"
            f"- Documents: {stats['documents']}\n"
            f"- Chunks: {stats['chunks']}\n"
            f"- Shared memory characters: {stats['memory_characters']:,}\n"
            f"- Retrieved document characters: {stats['document_characters']:,}\n"
            f"- Total characters: {stats['characters']:,}\n"
            f"- Contract characters: {stats['contract_characters']:,}\n"
            f"- Total selected characters: {stats['total_characters']:,}\n"
            f"- Estimated tokens: {stats['estimated_tokens']:,}\n\n"
            "## Omitted To Save Tokens\n\n"
            f"- Omitted memory characters: {stats['omitted_memory_characters']:,}\n"
            f"- Omitted document characters: {stats['omitted_document_characters']:,}\n\n"
            "## Retrieved Context\n\n"
            f"{context or '_No relevant context found._'}\n"
        )
        path = self.knowledge_path / TASK_CONTEXT_FILE
        path.write_text(content, encoding="utf-8")
        return path

    def write_project_brief(self, content: str) -> Path:
        return self.write_memory_file("PROJECT_BRIEF.md", content)

    def ensure_memory_files(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for file_name, title in MEMORY_FILES.items():
            path = self.knowledge_path / file_name
            if not path.exists():
                path.write_text(
                    MEMORY_TEMPLATE.format(title=title),
                    encoding="utf-8",
                )
            paths.append(path)
        return tuple(paths)

    def list_memory_files(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for file_name in MEMORY_FILES:
            path = self.knowledge_path / file_name
            if not path.is_file():
                continue
            try:
                result[file_name] = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
        return result

    def write_memory_file(
        self, file_name: str, content: str, author: str = "unknown"
    ) -> Path:
        if file_name not in MEMORY_FILES:
            raise ValueError(f"Unsupported memory file: {file_name}")
        path = self.knowledge_path / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.strip() + "\n", encoding="utf-8")
        self._record_provenance(file_name, author)
        return path

    def write_memory_from_text(
        self, content: str, author: str = "unknown"
    ) -> tuple[str, ...]:
        sections = self._split_memory_sections(content)
        if not sections:
            self.write_project_brief(content)
            self._record_provenance("PROJECT_BRIEF.md", author)
            return ("PROJECT_BRIEF.md",)

        written: list[str] = []
        for file_name, section_content in sections.items():
            if not section_content.strip():
                continue
            self.write_memory_file(file_name, section_content, author=author)
            written.append(file_name)
        return tuple(written)

    # ---- provenance -----------------------------------------------------
    # Who last wrote each memory file. Kept in one sidecar rather than nine, and
    # deliberately not in the files' own frontmatter: these files are fed into
    # the model's context, so extra fields there would cost tokens on every run.
    # `author` defaults to "unknown" so a caller that does not know cannot
    # accidentally claim someone wrote it.
    PROVENANCE_FILE = ".provenance.json"
    VALID_AUTHORS = ("claude", "codex", "you")

    def _provenance_path(self) -> Path:
        return self.knowledge_path / self.PROVENANCE_FILE

    def read_provenance(self) -> dict[str, dict[str, str]]:
        path = self._provenance_path()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _record_provenance(self, file_name: str, author: str) -> None:
        if author not in self.VALID_AUTHORS:
            # An unrecognised author records nothing at all; a stale entry would
            # be worse than none, because the panel would show it as fact.
            return
        record = self.read_provenance()
        record[file_name] = {
            "by": author,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        path = self._provenance_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            # Provenance is a nicety; failing to record it must never break the
            # write that just succeeded.
            pass

    def preview_memory_update_diff(self, content: str) -> str:
        sections = self._split_memory_sections(content)
        if not sections:
            sections = {"PROJECT_BRIEF.md": content}

        parts: list[str] = []
        for file_name, new_content in sections.items():
            if file_name not in MEMORY_FILES:
                continue
            path = self.knowledge_path / file_name
            old_content = ""
            if path.is_file():
                try:
                    old_content = path.read_text(encoding="utf-8")
                except OSError:
                    old_content = ""
            diff = unified_diff(
                old_content.splitlines(),
                (new_content.strip() + "\n").splitlines(),
                fromfile=f"{file_name} (before)",
                tofile=f"{file_name} (after)",
                lineterm="",
            )
            parts.extend(diff)
            parts.append("")
        return "\n".join(parts).strip()

    def scan_memory_update_safety(self, content: str) -> dict[str, object]:
        findings: list[str] = []
        if _MEMORY_SECURITY_SCANNER.scan_text(content).has_blocking_findings:
            findings.append("possible-secret")

        lowered = content.casefold()
        for term in MEMORY_HIGH_RISK_TERMS:
            if term in lowered:
                findings.append(f"high-risk-term:{term}")

        return {
            "safe": not findings,
            "findings": tuple(findings),
            "summary": (
                "No blocking memory safety findings."
                if not findings
                else "; ".join(findings)
            ),
        }

    def build_memory_context(self) -> str:
        """八份共同記憶，**全文**，一個字都不切。

        沿革，因為這件事修過兩次而前一次修錯了對象：

        - 2026-08-11：原本照 `MEMORY_FILES` 順序填、填滿就 `break`，八份裡
          只有兩份進得去 prompt。修法是「公平配額 + 被剪掉的自己講」。
        - 2026-08-21（這次）：**配額本身就是問題。** 公平配額把「六份完全
          不見」換成了「八份全部被切一半」——出廠預設（minimal／6,000）下
          每份只剩 750 字，而 `SECURITY_MODEL.md` 斷在「執行期狀態
          （DB、鎖、快取、暫」——「暫存」兩個字被切成兩半。不是因為那半句
          不重要，只是因為它排在第 750 個字。

        為什麼可以整個拿掉：省下來的量本來就很小。八份合計約 19,000 字（約
        6,000 tokens），而且共同記憶是**一個對話只送一次**，不是每一輪重送
        （`_knowledge_context_for_round`）。要瘦的是內容本身，不是為了湊一個
        數字去切它——切的那一刀永遠落在檔案後半段，而檔案後半段沒有比較不
        重要。

        沒有 `max_chars` 參數，這是刻意的：留一個參數就是留一條「呼叫端自己
        決定要不要切」的路，而這種決定沒有人有足夠的資訊做得對。四個舊呼叫端
        本來各自寫死 12,000／20,000／40,000／50,000 四個不同的數字，沒有一個
        說得出為什麼是那個數字。

        最後那一行 `_assert_safety_memory_intact` 不是防衛性程式碼，是這件事
        唯一會變紅的地方：如果哪天有人在這裡（或下游）又加了一刀，它會炸，
        而不是安靜地送出一份看起來完整的安全規則。
        """
        return self._memory_context(duty="")

    def build_dispatch_context(self, duty: str) -> str:
        """派工的時候送的那一份：**底座＋專案規則全文，其餘只給索引。**

        【規則分三層】：每個 AI 只收到「底座＋自己的角色積木」。老闆
        2026-08-23 講了兩次：「不就是寫成檔案讓 node 去看嗎???」
        「門要多大的短指標是對的」。

        判準**不是**「重要／不重要」，是**這一份 AI 會不會自己去讀**：
        限制它的那幾份逐字送（它不會主動去讀一份限制自己的檔案）；背景與
        已定案的給索引加點名（它需要的時候會去讀，而且讀到的是當下最新的
        那一份，不是派工那一刻的快照）。

        跟【共同記憶不准截斷】不牴觸：那條管「送出去的那一份不准被剪掉半條
        安全規則」，這裡管「哪幾份該送」。被選中的照樣一個字都不切——
        `_assert_safety_memory_intact` 仍然會回頭逐字比對。
        """
        return self._memory_context(duty=duty)

    def _memory_context(self, duty: str) -> str:
        available = tuple(
            name
            for name in (*MEMORY_FILES, PROJECT_RULES_FILE)
            if (self.knowledge_path / name).is_file()
        )
        if duty:
            wanted = tuple(
                name
                for name in rule_layers.pushed_files(
                    PROJECT_RULES_FILE in available
                )
                if name in available
            )
        else:
            wanted = tuple(name for name in MEMORY_FILES if name in available)

        titles = dict(MEMORY_FILES)
        titles[PROJECT_RULES_FILE] = t("knowledge.project_rules_title")

        sections: list[str] = []
        for file_name in wanted:
            try:
                content = (
                    (self.knowledge_path / file_name)
                    .read_text(encoding="utf-8")
                    .strip()
                )
            except OSError:
                continue
            if not content:
                continue
            sections.append(f"### {titles[file_name]}\n{content}")

        if duty:
            pointer = rule_layers.pointer_section(
                duty, str(self.knowledge_path), available
            )
            if pointer:
                sections.append(t("knowledge.where_the_rest_is") + pointer)

        context = "\n\n".join(sections).strip()
        self._assert_safety_memory_intact(context)
        return context

    def _assert_safety_memory_intact(self, context: str) -> None:
        """`MEMORY_MUST_ARRIVE_WHOLE` 的每一份都要逐字出現在 `context` 裡。

        比對的是**硬碟上的原文**，不是這支程式剛才組出來的變數——所以任何
        在中途發生的截斷（配額、切片、清洗、字數上限）都會被抓到，不管它加
        在哪一層。組裝端與檢查端讀的是同一顆硬碟，但走的是兩條路。

        `contract` 那半邊（角色、輸出格式、停止條件）由 prompt_builder 的
        golden snapshot 守；這裡守的是證據那半邊。
        """
        for file_name in MEMORY_MUST_ARRIVE_WHOLE:
            path = self.knowledge_path / file_name
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not content:
                continue
            if content not in context:
                raise MemoryTruncatedError(
                    f"{file_name} 沒有完整進入 prompt："
                    f"硬碟上 {len(content):,} 字，組出來的脈絡裡找不到全文。"
                    "安全規則與「不准自己決定的清單」不接受任何形式的截斷"
                    "——被截斷的版本讀起來跟完整的一模一樣。"
                )

    def memory_total_characters(self) -> int:
        """硬碟上所有共同記憶的字數，沒有上限。

        `omitted_memory_characters` 原本是拿 `build_memory_context(50000)`
        去減——**那個「原始量」自己也被截斷了**，所以回報的省略量在 AICC 自己
        的專案上少報 46,742 字。一個本身也受制於缺口的缺口數字，比沒有數字糟：
        它看起來已經把缺口算進去了。

        2026-08-21 之後共同記憶不再被截斷，所以那個省略量現在恆為 0。這支
        函式留著，因為「0」要是算出來的才有意義——寫死一個 0 的話，哪天有人
        又加了一刀，畫面上仍然會是 0。
        """
        total = 0
        for file_name, title in MEMORY_FILES.items():
            path = self.knowledge_path / file_name
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not content:
                continue
            total += len(f"### {title}\n{content}")
        return total

    @staticmethod
    def _context_profile(name: str) -> ContextProfile:
        return CONTEXT_PROFILES.get(
            str(name or "").strip().lower(),
            CONTEXT_PROFILES["balanced"],
        )

    def write_handoff_package(self) -> Path:
        self.ensure_memory_files()
        manifest_path = self.export_manifest()
        documents = self.list_documents()
        memory_context = self.build_memory_context()
        if not memory_context:
            memory_context = "_No shared memory content yet._"
        document_lines = [
            (
                f"- {document.display_name} "
                f"({document.source_type}, {document.status}, "
                f"{document.chunk_count} chunks)"
            )
            for document in documents
        ]
        if not document_lines:
            document_lines = ["- None"]

        generated_at = datetime.now(timezone.utc).isoformat()
        content = (
            "# AI Control Center Handoff Package\n\n"
            f"Generated at: {generated_at}\n"
            f"Project: {self.project_path}\n"
            f"Manifest: {manifest_path.relative_to(self.project_path)}\n\n"
            f"{self.format_readiness_report()}\n\n"
            "## Shared Project Memory\n\n"
            f"{memory_context}\n\n"
            "## Indexed Documents\n\n"
            + "\n".join(document_lines)
            + "\n\n"
            "## How To Use\n\n"
            "- Give this file to a new Claude/Codex session before continuing work.\n"
            "- Treat shared memory as the project baseline.\n"
            "- Re-scan or refresh documents when indexed sources change.\n"
        )
        path = self.knowledge_path / HANDOFF_PACKAGE_FILE
        path.write_text(content, encoding="utf-8")
        return path

    def write_session_bootstrap(self, task: str = "") -> Path:
        handoff_path = self.write_handoff_package()
        task_context_path: Path | None = None
        if task.strip():
            task_context_path = self.write_task_context(task)

        handoff_text = handoff_path.read_text(encoding="utf-8").strip()
        task_context_text = (
            task_context_path.read_text(encoding="utf-8").strip()
            if task_context_path is not None
            else "_No task-specific context was generated._"
        )
        generated_at = datetime.now(timezone.utc).isoformat()
        content = (
            "# AI Control Center Session Bootstrap\n\n"
            f"Generated at: {generated_at}\n"
            f"Project: {self.project_path}\n\n"
            "## How To Start\n\n"
            "Use this file when opening a new Claude Code or Codex session.\n"
            "Codex should read the project state first, Claude should verify "
            "implementation details, and both should preserve the decisions below.\n\n"
            "## Required Operating Rules\n\n"
            "- Do not modify files during planning mode.\n"
            "- Ask the user before destructive changes, dependency installs, "
            "database migrations, credential access, or external service changes.\n"
            "- Keep customer or sensitive files local unless explicitly approved.\n\n"
            "## Task-Specific Context\n\n"
            f"{task_context_text}\n\n"
            "## Project Handoff Package\n\n"
            f"{handoff_text}\n"
        )
        path = self.knowledge_path / SESSION_BOOTSTRAP_FILE
        path.write_text(content, encoding="utf-8")
        return path

    def generated_file_statuses(self) -> dict[str, bool]:
        return {
            file_name: (self.knowledge_path / file_name).is_file()
            for file_name in GENERATED_KNOWLEDGE_FILES
        }

    def run_import_wizard(self, task: str = "") -> Path:
        self.ensure_memory_files()
        scan_result = self.rebuild_project_index()
        bootstrap_path = self.write_session_bootstrap(task)
        manifest_path = self.export_manifest()
        readiness = self.import_readiness_report()
        generated = self.generated_file_statuses()
        generated_for_report = dict(generated)
        generated_for_report[IMPORT_WIZARD_REPORT_FILE] = True
        generated_count = sum(generated_for_report.values())
        generated_lines = [
            f"- {file_name}: {'已建立' if exists else '尚未建立'}"
            for file_name, exists in generated_for_report.items()
        ]
        next_steps = self._localized_import_next_steps(readiness.next_steps)
        attention_items: list[str] = []
        if readiness.document_count == 0:
            attention_items.append(
                t("knowledge.block.no_documents")
            )
        if scan_result.errors:
            attention_items.append(t("knowledge.block.parse_errors"))
        if readiness.missing_memory_files:
            attention_items.append(t("knowledge.block.memory_incomplete"))
        if readiness.updated_document_count:
            attention_items.append(t("knowledge.block.stale_sources"))
        if readiness.missing_source_count:
            attention_items.append(t("knowledge.block.missing_sources"))
        if readiness.parse_error_count:
            attention_items.append(t("knowledge.block.failed_documents"))
        if not attention_items:
            attention_items.append(t("knowledge.block.none"))
        attention = "\n".join(f"- {item}" for item in attention_items)
        status = t("knowledge.ready_for_handoff") if readiness.ready else t("knowledge.needs_attention")
        scan_errors = (
            "\n".join(f"- {item}" for item in scan_result.errors)
            if scan_result.errors
            else t("common.none_bullet")
        )
        content = (
            "# AI Control Center 導入精靈報告\n\n"
            f"產生時間：{datetime.now(timezone.utc).isoformat()}\n"
            f"專案路徑：{self.project_path}\n\n"
            "## 結果摘要\n\n"
            f"狀態：{status}\n"
            f"已索引文件：{readiness.document_count}\n"
            f"已索引段落：{readiness.chunk_count}\n"
            f"共同記憶：{readiness.memory_file_count}/{len(MEMORY_FILES)}\n"
            f"產生檔案：{generated_count}/{len(GENERATED_KNOWLEDGE_FILES)}\n\n"
            "已完成：\n"
            "- 已建立共同記憶檔。\n"
            "- 已掃描專案並重建本機索引。\n"
            "- 已產生交接包。\n"
            "- 已產生新 session 啟動提示。\n"
            "- 已匯出知識庫 manifest。\n\n"
            "需要處理：\n"
            f"{attention}\n\n"
            "下一步：\n"
            f"{next_steps}\n\n"
            "## 掃描結果\n\n"
            f"- 已索引：{len(scan_result.imported)}\n"
            f"- 已略過：{len(scan_result.skipped)}\n"
            f"- 錯誤：{len(scan_result.errors)}\n\n"
            "掃描錯誤：\n"
            f"{scan_errors}\n\n"
            f"{self.format_readiness_report()}\n\n"
            "## 產生檔案\n\n"
            + "\n".join(generated_lines)
            + "\n\n"
            "## Bootstrap\n\n"
            f"- {bootstrap_path.relative_to(self.project_path)}\n"
            f"- {manifest_path.relative_to(self.project_path)}\n\n"
            "## Recommended Next Steps\n\n"
            f"{next_steps}\n"
        )
        path = self.knowledge_path / IMPORT_WIZARD_REPORT_FILE
        path.write_text(content, encoding="utf-8")
        self.export_manifest()
        return path

    @staticmethod
    def _localized_import_next_steps(next_steps: tuple[str, ...]) -> str:
        mapping = {
            "Create shared memory files.": t("knowledge.next.create_memory"),
            "Scan the project or import reference documents.": (
                t("knowledge.next.scan_or_import")
            ),
            "Re-parse updated source documents.": t("knowledge.next.reparse_stale"),
            "Review documents with missing original files.": (
                t("knowledge.next.check_missing")
            ),
            "Open failed documents and fix parse errors.": (
                t("knowledge.next.fix_failed")
            ),
            "Ready for a Codex -> Claude -> Codex handoff task.": (
                t("knowledge.next.ready")
            ),
        }
        return "\n".join(
            f"- {mapping.get(item, item)}" for item in next_steps
        )

    @staticmethod
    def _split_memory_sections(content: str) -> dict[str, str]:
        aliases = {
            file_name.casefold(): file_name
            for file_name in MEMORY_FILES
        }
        aliases.update(
            {
                title.casefold(): file_name
                for file_name, title in MEMORY_FILES.items()
            }
        )

        sections: dict[str, list[str]] = {}
        current: str | None = None
        heading_pattern = re.compile(r"^#{1,4}\s+(.+?)\s*$")
        for raw_line in content.splitlines():
            line = raw_line.strip()
            match = heading_pattern.match(line)
            if match:
                heading = match.group(1).strip()
                heading = re.sub(r"^[0-9]+[.)]\s*", "", heading)
                heading = heading.strip("`：: ")
                normalized = aliases.get(heading.casefold())
                if normalized:
                    current = normalized
                    sections.setdefault(current, [])
                    continue
            if current is not None:
                sections[current].append(raw_line)

        return {
            file_name: "\n".join(lines).strip()
            for file_name, lines in sections.items()
        }

    def export_manifest(self) -> Path:
        manifest_path = self.knowledge_path / "manifest.json"
        manifest = {
            "version": 1,
            "project": str(self.project_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "documents": [asdict(item) for item in self.list_documents()],
            "memory_files": [
                {
                    "file_name": file_name,
                    "title": title,
                    "exists": (self.knowledge_path / file_name).is_file(),
                }
                for file_name, title in MEMORY_FILES.items()
            ],
            "generated_files": [
                {
                    "file_name": file_name,
                    "exists": (self.knowledge_path / file_name).is_file(),
                }
                for file_name in GENERATED_KNOWLEDGE_FILES
            ],
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest_path

    def _copy_import_file(self, source: Path) -> Path:
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"尚未支援格式：{source.suffix or '無副檔名'}")
        if source.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(
                f"檔案超過 {MAX_FILE_BYTES // (1024 * 1024)} MB 上限"
            )
        digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:10]
        safe_name = re.sub(r"[^\w.()\- ]+", "_", source.name, flags=re.UNICODE)
        destination = self.import_path / f"{digest}_{safe_name}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def _should_include(self, path: Path, include_aicc: bool = True) -> bool:
        if not path.is_file():
            return False
        try:
            relative_parts = path.resolve().relative_to(self.project_path).parts
        except ValueError:
            relative_parts = path.parts
        if any(part in IGNORED_DIRS for part in relative_parts):
            return False
        if not include_aicc and ".aicc" in relative_parts:
            return False
        if include_aicc and ".aicc" in relative_parts:
            try:
                path.resolve().relative_to(self.import_path.resolve())
            except ValueError:
                return False
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return False
        try:
            return path.stat().st_size <= MAX_FILE_BYTES
        except OSError:
            return False

    @staticmethod
    def _extract_text(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise ValueError(
                    t("knowledge.pypdf_missing")
                ) from exc
            try:
                reader = PdfReader(str(path))
                pages: list[str] = []
                for index, page in enumerate(reader.pages[:500]):
                    extracted = (page.extract_text() or "").strip()
                    if extracted:
                        pages.append(f"[第 {index + 1} 頁]\n{extracted}")
                    if sum(len(item) for item in pages) >= MAX_EXTRACTED_CHARACTERS:
                        break
                return "\n\n".join(pages)[:MAX_EXTRACTED_CHARACTERS]
            except Exception as exc:
                raise ValueError(f"無法解析 PDF：{exc}") from exc

        if suffix in {".docx", ".xlsx", ".pptx"}:
            return ProjectKnowledgeStore._extract_office_archive(path)

        raw = path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".json":
            try:
                return json.dumps(
                    json.loads(raw),
                    ensure_ascii=False,
                    indent=2,
                )[:MAX_EXTRACTED_CHARACTERS]
            except json.JSONDecodeError:
                return raw[:MAX_EXTRACTED_CHARACTERS]
        if suffix == ".csv":
            try:
                rows = list(csv.reader(raw.splitlines()))
                return "\n".join(
                    " | ".join(row) for row in rows
                )[:MAX_EXTRACTED_CHARACTERS]
            except csv.Error:
                return raw[:MAX_EXTRACTED_CHARACTERS]
        if suffix in {".html", ".htm", ".xml"}:
            without_script = re.sub(
                r"<(script|style)[^>]*>.*?</\1>",
                " ",
                raw,
                flags=re.IGNORECASE | re.DOTALL,
            )
            extracted = re.sub(r"<[^>]+>", " ", without_script)
            return html.unescape(
                re.sub(r"[ \t]+", " ", extracted)
            )[:MAX_EXTRACTED_CHARACTERS]
        return raw[:MAX_EXTRACTED_CHARACTERS]

    @staticmethod
    def _extract_office_archive(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".docx":
            prefixes = (
                "word/document.xml",
                "word/header",
                "word/footer",
                "word/footnotes.xml",
                "word/endnotes.xml",
            )
        elif suffix == ".pptx":
            prefixes = (
                "ppt/slides/slide",
                "ppt/notesSlides/notesSlide",
            )
        else:
            prefixes = (
                "xl/sharedStrings.xml",
                "xl/worksheets/sheet",
            )

        parts: list[str] = []
        try:
            with zipfile.ZipFile(path) as archive:
                names = sorted(
                    name
                    for name in archive.namelist()
                    if name.endswith(".xml")
                    and any(name.startswith(prefix) for prefix in prefixes)
                )
                for name in names:
                    try:
                        root = ET.fromstring(archive.read(name))
                    except (ET.ParseError, KeyError):
                        continue
                    values: list[str] = []
                    for element in root.iter():
                        local_name = element.tag.rsplit("}", 1)[-1]
                        if local_name not in {"t", "v"}:
                            continue
                        value = (element.text or "").strip()
                        if value:
                            values.append(value)
                    if values:
                        parts.append(f"[{name}]\n" + " ".join(values))
                    if sum(len(item) for item in parts) >= MAX_EXTRACTED_CHARACTERS:
                        break
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError(f"無法解析 Office 文件：{exc}") from exc
        return "\n\n".join(parts)[:MAX_EXTRACTED_CHARACTERS]

    @staticmethod
    def _split_chunks(text: str) -> list[str]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []
        chunks: list[str] = []
        start = 0
        length = len(normalized)
        while start < length:
            end = min(length, start + CHUNK_SIZE)
            if end < length:
                boundary = max(
                    normalized.rfind("\n\n", start, end),
                    normalized.rfind("\n", start, end),
                    normalized.rfind("。", start, end),
                )
                if boundary > start + CHUNK_SIZE // 2:
                    end = boundary + 1
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= length:
                break
            start = max(start + 1, end - CHUNK_OVERLAP)
        return chunks

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        """挑關鍵字的機制搬去 `app/services/chinese_text.py` 了（2026-08-24）。

        那份停用詞**不是介面文字**：介面翻成英文的那天，中文的停用詞還是要
        留著（使用者的舊任務描述是中文的，索引裡的文件也是）。真的要支援
        英文查詢，做的是**再加一份**，不是把這一份換掉。
        """
        from app.services.chinese_text import terms

        return terms(query)

    def _fallback_recent_chunks(
        self,
        max_chunks: int,
        max_chars: int,
    ) -> list[KnowledgeChunk]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.document_id, d.display_name, d.source_path,
                       d.source_type, c.chunk_index, c.content
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                ORDER BY d.indexed_at DESC, c.chunk_index ASC
                LIMIT ?
                """,
                (max_chunks,),
            ).fetchall()
        result: list[KnowledgeChunk] = []
        used = 0
        for row in rows:
            content = str(row["content"])
            if used + len(content) > max_chars and result:
                break
            result.append(
                KnowledgeChunk(
                    document_id=int(row["document_id"]),
                    display_name=str(row["display_name"]),
                    source_path=str(row["source_path"]),
                    chunk_index=int(row["chunk_index"]),
                    content=content,
                    reason="recent",
                    source_type=str(row["source_type"] or "project"),
                )
            )
            used += len(content)
        return result

    def _get_document(self, document_id: int) -> KnowledgeDocument | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        return self._row_to_document(row) if row is not None else None

    def _stored_document_path(self, document: KnowledgeDocument) -> Path | None:
        if not document.stored_path:
            return None
        stored = Path(document.stored_path)
        if stored.is_absolute():
            return stored
        return (self.project_path / stored).resolve()

    def _mark_document_error(
        self,
        document_id: int,
        status: str,
        error: str,
    ) -> KnowledgeDocument:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM chunks WHERE document_id = ?",
                (document_id,),
            )
            connection.execute(
                """
                UPDATE documents SET
                    indexed_at = ?, chunk_count = 0, status = ?, error = ?
                WHERE id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    status,
                    error,
                    document_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        return self._row_to_document(row)

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> KnowledgeDocument:
        return KnowledgeDocument(
            document_id=int(row["id"]),
            source_type=str(row["source_type"]),
            display_name=str(row["display_name"]),
            source_path=str(row["source_path"]),
            stored_path=str(row["stored_path"]),
            extension=str(row["extension"]),
            size_bytes=int(row["size_bytes"]),
            modified_at=str(row["modified_at"]),
            indexed_at=str(row["indexed_at"]),
            chunk_count=int(row["chunk_count"]),
            status=str(row["status"]),
            error=str(row["error"]),
        )
