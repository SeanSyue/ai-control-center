from __future__ import annotations

from app.i18n import t

import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.services.line_notifier import LineNotifier
from app.services.project_knowledge import ProjectKnowledgeStore
from app.services.project_registry import ProjectRegistry, sensitivity_label
from app.services.security_scan import SecurityScanReport, SecurityScanner
from app.services.session_registry import SessionRegistry

# How many excluded files to name before falling back to a count. The preview
# goes into a message box, and a list long enough to scroll is a list nobody
# reads - which turns 「我看過了」 into something that feels like diligence and
# is not. Twelve fits on screen next to everything else the box has to say.
MAX_NAMED_SKIPS = 12


@dataclass(frozen=True)
class WorkspaceExportPreview:
    success: bool
    included_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    # Excluded files grouped by why (ROADMAP P5). Keys are
    # `WorkspacePortabilityService.skip_reason_text()`.
    skipped_by_reason: dict[str, list[str]] = field(default_factory=dict)
    security_report: SecurityScanReport | None = None
    sensitivity: str = "personal"
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """What a person needs at the moment they decide to hand this over.

        ROADMAP P5「明確顯示哪些會被匯出、哪些不會」. This used to be two
        counts - 「預計匯出 312 個檔案，排除 42 個檔案。」 - which cannot answer
        the question actually being asked, which is 「我的 .env 有沒有在裡面」.

        Not a file list either. 312 paths in a message box is the same
        non-answer wearing more ink: nobody reads it, and 「我看過了」 becomes
        untrue in a way that feels like diligence. What goes in instead:

        * **the `.aicc` entries by name**, because those are the AICC-specific
          ones a person would not think to expect in a zip they are sending;
        * **exclusions grouped by reason with counts**, so 「.env 有被排除嗎」
          is answerable by reading one line rather than scanning a list;
        * everything else as a count per top-level folder.
        """
        if not self.success:
            detail = self.errors[0] if self.errors else t("error.unknown")
            return f"匯出預覽失敗：{detail}"

        from app.services.workspace_portability import (  # noqa: PLC0415
            WorkspacePortabilityService as _Service,
        )

        lines = [
            f"預計匯出 {len(self.included_files)} 個檔案，"
            f"排除 {len(self.skipped_files)} 個。"
        ]
        if self.sensitivity != "personal":
            label = sensitivity_label(self.sensitivity)
            lines.append(f"專案敏感度：{label}")

        aicc = sorted(
            name for name in self.included_files if name.startswith(".aicc/")
        )
        others: dict[str, int] = {}
        for name in self.included_files:
            if name.startswith(".aicc/"):
                continue
            top = name.split("/", 1)[0] if "/" in name else t("export.project_root")
            others[top] = others.get(top, 0) + 1

        lines.append("")
        lines.append(t("export.will_take"))
        for top, count in sorted(others.items(), key=lambda kv: -kv[1]):
            lines.append(f"  · {top}：{count} 個")
        if aicc:
            lines.append(f"  · .aicc（AICC 自己的資料，共 {len(aicc)} 個）：")
            # Folders, not files: `.aicc/runs` can hold hundreds, and the
            # thing worth telling is that the run history travels at all.
            groups: dict[str, int] = {}
            for name in aicc:
                parts = name.split("/")
                key = parts[1] if len(parts) > 2 else name
                groups[key] = groups.get(key, 0) + 1
            for key, count in sorted(groups.items()):
                lines.append(
                    f"      {key}" + (f"（{count} 個）" if count > 1 else "")
                )

        if self.skipped_by_reason:
            lines.append("")
            lines.append(t("export.will_not_take"))
            for reason, names in sorted(
                self.skipped_by_reason.items(), key=lambda kv: -len(kv[1])
            ):
                label = _Service.skip_reason_text(reason)
                lines.append(f"  · {label}：{len(names)} 個")
                if reason not in _Service.NAMED_SKIP_REASONS:
                    continue
                shown = sorted(names)[:MAX_NAMED_SKIPS]
                for name in shown:
                    lines.append(f"      {name}")
                if len(names) > len(shown):
                    lines.append(f"      （還有 {len(names) - len(shown)} 個）")

        security = (
            self.security_report.summary()
            if self.security_report is not None
            else t("export.scan_not_run")
        )
        lines.append("")
        lines.append(security)
        return "\n".join(lines)


@dataclass(frozen=True)
class WorkspaceExportResult:
    success: bool
    archive_path: str = ""
    included_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.success:
            detail = self.errors[0] if self.errors else t("error.unknown")
            return f"匯出失敗：{detail}"
        return (
            f"匯出完成：{len(self.included_files)} 個檔案，"
            f"略過 {len(self.skipped_files)} 個。"
        )


@dataclass(frozen=True)
class WorkspaceImportResult:
    success: bool
    project_path: str = ""
    imported_files: list[str] = field(default_factory=list)
    # Entries refused because their name pointed outside the destination.
    # Recorded rather than dropped in silence: an archive containing one is
    # either corrupt or hostile, and both are worth telling the user about.
    rejected_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.success:
            detail = self.errors[0] if self.errors else t("error.unknown")
            return f"匯入失敗：{detail}"
        done = f"匯入完成：{len(self.imported_files)} 個檔案。"
        if self.rejected_files:
            # Said out loud, not buried: an entry pointing outside the folder
            # means the archive tried to write somewhere it was not given.
            done += (
                f"　已拒絕 {len(self.rejected_files)} 個指向資料夾外的項目"
                f"（{self.rejected_files[0]}）。"
            )
        return done


class WorkspacePortabilityService:
    ARCHIVE_VERSION = 1
    EXCLUDED_NAMES = {
        ".env",
        "sync-state.json",
        "workflow.lock",
        "index.sqlite-wal",
        "index.sqlite-shm",
    }
    EXCLUDED_PARTS = {
        "runtime",
        "conflicts",
        "__pycache__",
        ".pytest_cache",
    }
    ALLOWED_AICC_PREFIXES = {
        "knowledge",
        "handoff",
        "notes",
        "prompts",
        "runs",
        "timeline",
    }
    ALLOWED_AICC_FILES = {
        "task.md",
        "planning-request.md",
        "project.json",
        "policies.json",
        "sessions.json",
        "ai-settings.json",
    }

    def export_project(
        self,
        project_path: Path,
        destination: Path,
    ) -> WorkspaceExportResult:
        project = project_path.expanduser().resolve()
        if not project.is_dir():
            return WorkspaceExportResult(
                success=False,
                errors=[f"專案不存在：{project}"],
            )

        destination = destination.expanduser().resolve()
        if destination.suffix.lower() != ".zip":
            destination = destination / f"{project.name}.aicc-workspace.zip"
        destination.parent.mkdir(parents=True, exist_ok=True)

        ProjectRegistry.initialize_project(project)
        knowledge = ProjectKnowledgeStore(project)
        knowledge.ensure_memory_files()
        knowledge.write_handoff_package()
        knowledge.export_manifest()

        preview = self.preview_export(project)
        if not preview.success:
            return WorkspaceExportResult(
                success=False,
                errors=preview.errors,
            )
        if preview.security_report and preview.security_report.has_blocking_findings:
            return WorkspaceExportResult(
                success=False,
                included_files=preview.included_files,
                skipped_files=preview.skipped_files,
                errors=[
                    t("export.stopped.secrets")
                ],
            )

        # Last thing before the files are read, so nothing written above (the
        # handoff package, the manifest) is still sitting in the write-ahead
        # log. `-wal` is excluded from the archive on purpose, so whatever has
        # not been folded back into `index.sqlite` by now does not travel -
        # see `ProjectKnowledgeStore.checkpoint`.
        if not knowledge.checkpoint():
            return WorkspaceExportResult(
                success=False,
                errors=[
                    t("export.stopped.index")
                ],
            )

        included: list[str] = []
        skipped: list[str] = []
        manifest = {
            "version": self.ARCHIVE_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "project_name": project.name,
            "project_path_hint": str(project),
            "note": (
                "This archive excludes credentials, CLI logins, runtime locks, "
                "sync state, .env files, and local secret material."
            ),
            # 兩件**只有在舊機上才知道**的事，記下來給接手清單用。
            # 沒有它們，新機分不出「這個專案本來就沒有 git」和「歷史沒跟過來」，
            # 而一條分不出這兩者的提醒，只會被學會忽略。
            # 都是布林值，不含任何憑證內容。
            "had_git": (project / ".git").is_dir(),
            "line_notify_configured": self._line_notify_configured(),
            # 決策紀錄**刻意不放進這個包**（`decisions` 不在
            # `ALLOWED_AICC_PREFIXES`，雲端白名單也一樣）：稽核紀錄只從會驗證
            # 它、也會講清楚界線的那道門出去，也就是決策紀錄頁自己的匯出。
            #
            # 但那個決定必須讓新機講得出來。少了這個數字，搬家之後那條鏈會從
            # GENESIS 重新開始，而之後匯出給稽核的檔案會顯示「N 筆通過驗證」
            # ——完整、可信，而且少了搬家前的全部。**只有舊機知道那個「全部」
            # 是多少**，所以在這裡記下來。
            "decision_entries": self._decision_entry_count(project),
        }

        try:
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "AICC_EXPORT_MANIFEST.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                included.append("AICC_EXPORT_MANIFEST.json")

                for relative in preview.included_files:
                    if relative == "AICC_EXPORT_MANIFEST.json":
                        continue
                    file_path = project / relative
                    archive.write(file_path, relative)
                    included.append(relative)
                skipped.extend(preview.skipped_files)
        except OSError as exc:
            return WorkspaceExportResult(
                success=False,
                errors=[str(exc)],
            )

        return WorkspaceExportResult(
            success=True,
            archive_path=str(destination),
            included_files=included,
            skipped_files=skipped,
        )

    def preview_export(self, project_path: Path) -> WorkspaceExportPreview:
        project = project_path.expanduser().resolve()
        if not project.is_dir():
            return WorkspaceExportPreview(
                success=False,
                errors=[f"專案不存在：{project}"],
            )

        sensitivity = ProjectRegistry.get_sensitivity(project)
        if sensitivity == "sensitive_kyc":
            return WorkspaceExportPreview(
                success=False,
                sensitivity=sensitivity,
                errors=[
                    t("export.stopped.kyc")
                ],
            )

        included = ["AICC_EXPORT_MANIFEST.json"]
        skipped: list[str] = []
        skipped_by_reason: dict[str, list[str]] = {}
        scan_paths: list[Path] = []
        for file_path in self._iter_export_files(project):
            relative = file_path.relative_to(project).as_posix()
            reason = self.skip_reason(relative, sensitivity)
            if reason:
                skipped.append(relative)
                skipped_by_reason.setdefault(reason, []).append(relative)
                continue
            included.append(relative)
            scan_paths.append(file_path)

        report = SecurityScanner().scan_paths(scan_paths, root=project)
        return WorkspaceExportPreview(
            success=True,
            included_files=included,
            skipped_files=skipped,
            skipped_by_reason=skipped_by_reason,
            security_report=report,
            sensitivity=sensitivity,
        )

    def import_project(
        self,
        archive_path: Path,
        destination_dir: Path,
    ) -> WorkspaceImportResult:
        archive_path = archive_path.expanduser().resolve()
        destination_dir = destination_dir.expanduser().resolve()
        if not archive_path.is_file():
            return WorkspaceImportResult(
                success=False,
                errors=[f"匯入檔不存在：{archive_path}"],
            )

        destination_dir.mkdir(parents=True, exist_ok=True)
        imported: list[str] = []
        rejected: list[str] = []
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                manifest = json.loads(
                    archive.read("AICC_EXPORT_MANIFEST.json").decode("utf-8")
                )
                project_name = str(
                    manifest.get("project_name") or archive_path.stem
                )
                target = destination_dir / self._safe_name(project_name)
                target.mkdir(parents=True, exist_ok=True)

                root = target.resolve()
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename.replace("\\", "/")
                    if name == "AICC_EXPORT_MANIFEST.json":
                        continue
                    if self._should_skip(name):
                        continue
                    destination = self._safe_destination(root, name)
                    if destination is None:
                        rejected.append(name)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(info.filename))
                    imported.append(name)
        except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
            return WorkspaceImportResult(
                success=False,
                errors=[str(exc)],
            )

        ProjectRegistry.initialize_project(target)
        self._drop_machine_bound_sessions(target)
        self._write_handover_marker(target, manifest)
        return WorkspaceImportResult(
            success=True,
            project_path=str(target),
            imported_files=imported,
            rejected_files=rejected,
        )

    @staticmethod
    def _decision_entry_count(project: Path) -> int:
        """舊機上有幾筆決策紀錄。問不到就當作 0。

        跟 `_line_notify_configured` 同樣的取捨：匯出不該為了記一個數字而失敗。
        少一條提醒，好過多一次匯出失敗——而那條提醒的存在本來就是額外的。
        """
        try:
            from app.services.long_task import DecisionLogStore

            return len(DecisionLogStore(project).list_entries())
        except Exception:
            return 0

    @staticmethod
    def _line_notify_configured() -> bool:
        # LineNotifier 需要 APPDATA，沒有就直接拋。匯出不該為了記一個布林值
        # 而失敗，所以問不到就當作沒設定——寧可少一條提醒，不要多一次匯出失敗。
        try:
            return LineNotifier().is_configured()
        except Exception:
            return False

    @staticmethod
    def _write_handover_marker(target: Path, manifest: dict) -> None:
        """Leave a note saying this project arrived from somewhere else.

        接手清單只對「搬過來的專案」有意義。沒有這個檔案的話，清單要嘛對每個
        專案都出現（於是變成沒有人看的裝飾），要嘛得用猜的去判斷專案從哪來。

        它自己不可攜——`.aicc/handover.json` 不在 `ALLOWED_AICC_FILES` 裡，
        所以再匯出一次不會把「上一次搬家」的紀錄也傳下去。
        """
        payload = {
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "from": str(manifest.get("project_path_hint", "")),
            "had_git": bool(manifest.get("had_git", False)),
            "line_notify_configured": bool(
                manifest.get("line_notify_configured", False)
            ),
            # 舊機有幾筆決策紀錄。它們刻意不在包裡，而這個數字是新機唯一
            # 能講出「有東西留在那邊」的依據。
            "decision_entries": int(manifest.get("decision_entries", 0) or 0),
        }
        path = target / ".aicc" / "handover.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _drop_machine_bound_sessions(target: Path) -> None:
        """Keep the line-up, drop the session ids.

        `sessions.json` holds two kinds of thing and only one of them travels:

        | 欄位 | 可攜嗎 |
        |---|---|
        | `lineup` 的 role／provider／identity | 可以，是使用者花時間設定的 |
        | `lineup` 的 `session_id` | **不行**——舊機 CLI 的本機紀錄 |
        | `selected` / `sessions` | 不行，同上 |
        | `project_path` | 不行——舊機的絕對路徑 |

        **session id 是存在 `AgentSlot` 上的**，`selected` 那一塊是從它們算出來
        的。所以只清 `selected` 等於什麼都沒做——下次 `load()` 會再從陣容算
        一次，原封不動長回來。負向驗證就是這樣抓到第一版的：把「只清 sessions、
        selected 留著」這個破壞放進去，測試全綠。

        Carrying the ids over means the first run on the new machine resumes a
        conversation that does not exist here. That is the worst shape of
        wrong: not a crash, just a model that starts with no memory of what
        it is supposedly continuing.

        Excluding the whole file from the export was the other option and it
        is worse - it throws away the line-up, which is portable and is the
        part the user actually configured.

        ## 為什麼是改原始 JSON，不是 `load_lineup()` → `save()`

        匯入是**唯一一條它自己就講明「資料來自別台機器」的路**，也就是最可能
        遇到「這一版讀不出來的一格」的地方：對面多裝了一個廠牌、或者對面那台
        跑的是比較新的 build。而經過 `Lineup` 來回一趟，等於用這一版的形狀
        把整份陣容重寫一次——那一格會在**匯入的當下從硬碟上被刪掉**，而使用者
        收到的是「匯入成功」。（`Lineup.to_list()` 現在會把讀不出來的那幾格
        原樣接回去，但那是第二道；這裡不繞那一圈，連新版多出來的**欄位**也
        一併留著。）

        只動四個地方，其餘一個位元都不碰：每一格的 `session_id`、`sessions`、
        `selected`，以及 `project_path`（那是舊機的絕對路徑，留著會讓之後每
        一個讀它的人拿到一條不存在的路）。**版本號刻意不動**——這是一次針對性
        的清除，不是照這一版的形狀重寫，所以「比這一版新的檔案不准蓋」那條
        規矩不適用，而把一份新版檔案降版寫回去才是真的會弄丟東西。
        """
        registry = SessionRegistry(target)
        path = registry.path
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 讀不出來就不要動它。這裡改不了的東西，寫回去只會更少。
            return
        if not isinstance(payload, dict):
            return
        entries = payload.get("lineup")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    # **session id 存在每一格上**，`selected` 是從它們算出來的。
                    # 只清 `selected` 等於什麼都沒做——負向驗證就是這樣抓到
                    # 第一版的：那個破壞放進去，測試全綠。
                    entry["session_id"] = ""
        payload["selected"] = {"claude": "", "codex": ""}
        payload["sessions"] = {"claude": [], "codex": []}
        payload["project_path"] = str(registry.project_path)
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(path)

    @staticmethod
    def _safe_destination(root: Path, name: str) -> Path | None:
        """Where `name` may be written, or None if it escapes `root`.

        An archive is untrusted input - the whole point of import is that it
        came from another machine - so an entry name cannot be joined onto the
        destination and used. Two ways it escapes, and only the first was
        previously handled:

        * `../../evil.txt` walks up out of the folder.
        * `C:/Users/x/evil.txt` is drive-absolute, and on Windows
          `Path("dest") / "C:/Users/x/evil.txt"` discards `dest` entirely -
          measured: an entry named after a path in %TEMP% was written there,
          outside the destination, and still reported as imported.

        Rather than enumerate the tricks, this resolves the joined path and
        requires the result to sit under `root`. Symlinked parents and
        Windows 8.3 short names collapse during resolve(), so they cannot be
        used to sneak past the comparison either.
        """
        candidate = Path(name)
        if candidate.is_absolute() or candidate.drive or name.startswith(("/", "\\")):
            return None
        try:
            resolved = (root / candidate).resolve()
        except (OSError, ValueError):
            return None
        return resolved if resolved.is_relative_to(root) else None

    def _iter_export_files(self, project: Path):
        for file_path in project.rglob("*"):
            if file_path.is_file():
                yield file_path

    # Why a file is left out, in the words the person deciding whether to hand
    # this zip to somebody actually needs. Keyed by the same strings
    # `skip_reason` returns.
    _SKIP_REASON_KEYS = {
        "secret_or_local": "skip.secret_or_local",
        "runtime": "skip.runtime",
        "aicc_not_portable": "skip.aicc_not_portable",
        "vcs_or_venv": "skip.vcs_or_venv",
        "client_local_only": "skip.client_local_only",
    }

    @classmethod
    def skip_reason_text(cls, reason: str) -> str:
        """那個理由寫給人看是什麼。**認不得的回代號本身**——不回空白。

        空白會讓那一列看起來像「沒有理由」，而它其實是「有一種理由我沒
        列到」。代號很醜，但醜的東西會被回報，空白不會。
        """
        key = cls._SKIP_REASON_KEYS.get(reason)
        return t(key) if key else reason

    # Reasons where the *specific file* is what the reader needs, not the
    # count. Both of these hold things somebody might expect to travel and be
    # wrong about: a secret they assumed was excluded, or the decision log,
    # which deliberately does not ride along in a workspace zip (it has its
    # own authorised export - see P8). A number cannot answer either.
    #
    # `runtime` and `vcs_or_venv` are counted only: nobody needs the list of
    # three thousand `.git` objects, and printing it would bury the two lines
    # above it.
    NAMED_SKIP_REASONS = frozenset(
        {"secret_or_local", "aicc_not_portable", "client_local_only"}
    )

    # 跟 `GoogleDriveFolderSync.LOCAL_ONLY_PREFIXES` 同一條政策，兩條出去的路
    # 各有一份（使用者 2026-08-16 定案：`.aicc/runs` 跟敏感度走）。
    #
    # **刻意各寫一份而不是共用一個常數。** 兩邊的比對單位不同：同步比的是
    # `Path(".aicc/runs")` 這種完整相對路徑，這裡比的是 `.aicc` 底下的第一層
    # 名稱字串。硬要共用就得多一層轉換，而那層轉換錯了會安靜地讓政策失效。
    # 代價是改的時候兩邊都要改——所以
    # `tests/test_what_actually_leaves_the_machine.py` 有一條在釘兩邊一致。
    LOCAL_ONLY_AICC_PREFIXES = {
        "client": frozenset({"runs"}),
    }

    def skip_reason(self, relative: str, sensitivity: str = "") -> str:
        """Why this file is excluded, or "" if it travels.

        ROADMAP P5「明確顯示哪些會被匯出、哪些不會」. This used to be
        `_should_skip` returning a bool, and the preview could only say
        「排除 42 個檔案」 - a count, at the exact moment somebody is deciding
        whether to hand the zip to another person. 「我的 .env 有沒有在裡面」
        is the question being asked, and a number cannot answer it.

        The reason is produced by the decision itself rather than by a second
        pass that re-derives it, for the same reason the scheduler's blockers
        are (see `ProjectDashboardService.start_readiness`): a second copy
        explains confidently and wrongly the first time either changes.
        """
        path = Path(relative)
        parts = set(path.parts)
        if path.name in self.EXCLUDED_NAMES:
            return "secret_or_local"
        if parts & self.EXCLUDED_PARTS:
            return "runtime"
        if path.parts and path.parts[0] == ".aicc":
            if len(path.parts) < 2:
                return "aicc_not_portable"
            second = path.parts[1]
            if (
                second not in self.ALLOWED_AICC_PREFIXES
                and second not in self.ALLOWED_AICC_FILES
            ):
                return "aicc_not_portable"
            if second in self.LOCAL_ONLY_AICC_PREFIXES.get(sensitivity, frozenset()):
                # 這兩問的先後不重要，因為 `LOCAL_ONLY_AICC_PREFIXES` 一定是
                # `ALLOWED_AICC_PREFIXES` 的子集（由
                # `test_the_policy_only_names_folders_that_would_otherwise_travel`
                # 釘住）。第一版這裡寫了一段解釋順序的註解，負向驗證把順序
                # 對調之後沒有任何測試變紅——那個差別在這個不變式底下不存在。
                return "client_local_only"
            return ""
        if ".git" in parts or ".venv" in parts:
            return "vcs_or_venv"
        return ""

    def _should_skip(self, relative: str) -> bool:
        return bool(self.skip_reason(relative))

    @staticmethod
    def _safe_name(raw: str) -> str:
        cleaned = "".join(
            character
            for character in raw.strip()
            if character not in '<>:"/\\|?*'
        )
        return cleaned or "imported-project"
