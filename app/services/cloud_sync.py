from __future__ import annotations

from app.i18n import t

import hashlib
import json
import os
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.project_registry import ProjectRegistry, sensitivity_label
from app.services.security_scan import SecurityScanner


@dataclass
class CloudSyncSettings:
    enabled: bool = False
    root_path: str = ""
    auto_sync_on_start: bool = True
    auto_sync_on_switch: bool = True
    auto_sync_after_run: bool = True
    device_id: str = ""


@dataclass
class CloudProjectInfo:
    project_id: str
    name: str
    updated_at: str
    device_id: str = ""


@dataclass
class SyncResult:
    success: bool
    project_id: str = ""
    project_name: str = ""
    cloud_path: str = ""
    uploaded: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    # 因為敏感度而整批留在本機的東西。跟 `blocked` 分開，因為兩者的意思不同：
    # `blocked` 是「這個檔案掃到疑似機密所以擋下」（逐檔、意外），
    # `withheld` 是「這個分類的專案本來就不送這種東西」（整批、政策）。
    # 混在一起的話，一句「已擋下 12」會讓人以為掃描器抓到了十二個秘密。
    withheld: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    finished_at: str = ""

    @property
    def summary(self) -> str:
        if not self.success:
            detail = self.errors[0] if self.errors else t("error.unknown")
            return f"Google Drive 同步失敗：{detail}"

        parts = [
            f"上傳 {len(self.uploaded)}",
            f"下載 {len(self.downloaded)}",
            f"衝突 {len(self.conflicts)}",
            f"未變更 {len(self.unchanged)}",
        ]
        line = t("cloud.sync_done") + "、".join(parts)
        if self.blocked:
            # Named, not counted. A bare "已擋下 1" gave no way to tell whether
            # the withheld file was a stray .env or the project's own decision
            # log - and it was the decision log, kept back for three matches on
            # ordinary prose that nobody could see.
            line += t("cloud.blocked_uploads") + "；".join(self.blocked)
        if self.withheld:
            # 同樣具名不計數，理由同上：安靜地少傳跟沒有少傳，在畫面上長得一樣。
            line += t("cloud.withheld_by_sensitivity") + "；".join(self.withheld)
        return line


class CloudSyncConfigStore:
    FILE_NAME = "cloud_sync.json"

    def __init__(self) -> None:
        appdata = os.environ.get("APPDATA")
        if appdata:
            self.config_dir = Path(appdata) / "AIControlCenter"
        else:
            self.config_dir = Path.home() / ".aicontrolcenter"

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / self.FILE_NAME

    def load(self) -> CloudSyncSettings:
        if not self.config_path.exists():
            return CloudSyncSettings(device_id=str(uuid.uuid4()))

        try:
            raw = json.loads(
                self.config_path.read_text(encoding="utf-8")
            )
            settings = CloudSyncSettings(
                enabled=bool(raw.get("enabled", False)),
                root_path=str(raw.get("root_path", "")),
                auto_sync_on_start=bool(
                    raw.get("auto_sync_on_start", True)
                ),
                auto_sync_on_switch=bool(
                    raw.get("auto_sync_on_switch", True)
                ),
                auto_sync_after_run=bool(
                    raw.get("auto_sync_after_run", True)
                ),
                device_id=str(raw.get("device_id", "")),
            )
        except (OSError, json.JSONDecodeError, TypeError):
            settings = CloudSyncSettings()

        if not settings.device_id:
            settings.device_id = str(uuid.uuid4())
            self.save(settings)

        return settings

    def save(self, settings: CloudSyncSettings) -> None:
        if not settings.device_id:
            settings.device_id = str(uuid.uuid4())

        temp_path = self.config_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(
                asdict(settings),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp_path.replace(self.config_path)


class GoogleDriveFolderSync:
    """Synchronize AICC metadata through a local Google Drive folder.

    This deliberately syncs only allow-listed .aicc metadata. Project source
    code, .env files, LINE credentials, databases, and customer files are not
    copied. Source code should continue to use Git/private repositories.

    Allow-listed files can still contain pasted secrets (for example a task
    note that includes an API key), so every local file is scanned before it
    is copied into the shared Drive folder. Files with a blocking finding are
    skipped and reported instead of uploaded.
    """

    APP_FOLDER_NAME = "AIControlCenter"
    MAX_FILE_SIZE = 5 * 1024 * 1024
    EXACT_FILES = {
        Path(".aicc/task.md"),
        Path(".aicc/planning-request.md"),
        Path(".aicc/project.json"),
        Path(".aicc/policies.json"),
        Path(".aicc/workflow-result.json"),
        Path(".aicc/sessions.json"),
        Path(".aicc/ai-settings.json"),
        Path(".aicc/knowledge/PROJECT_BRIEF.md"),
        Path(".aicc/knowledge/ARCHITECTURE.md"),
        Path(".aicc/knowledge/DECISIONS.md"),
        Path(".aicc/knowledge/SECURITY_MODEL.md"),
        Path(".aicc/knowledge/PRODUCT_REVIEW.md"),
        Path(".aicc/knowledge/BUSINESS_MODEL.md"),
        Path(".aicc/knowledge/NEXT_STEPS.md"),
        Path(".aicc/knowledge/OPEN_QUESTIONS.md"),
        Path(".aicc/knowledge/TASK_CONTEXT.md"),
        Path(".aicc/knowledge/HANDOFF_PACKAGE.md"),
        Path(".aicc/knowledge/SESSION_BOOTSTRAP.md"),
        Path(".aicc/knowledge/IMPORT_WIZARD_REPORT.md"),
        Path(".aicc/knowledge/manifest.json"),
    }
    PREFIX_DIRS = {
        Path(".aicc/handoff"),
        Path(".aicc/notes"),
        Path(".aicc/prompts"),
        Path(".aicc/runs"),
        Path(".aicc/timeline"),
    }
    # 敏感度決定 `.aicc/runs` 走不走（使用者 2026-08-16 定案）。
    #
    # `sensitive_kyc` 整個專案不同步，所以那一層不在這裡。缺的是 `client`：
    # Stage C 從 2026-08-10 起把逐字 prompt 與逐字輸出寫進 runs，而准它出去的
    # 是**前綴**，所以那個資料夾底下的一切都會走。守門的憑證掃描器實測抓
    # API key，**不抓護照號碼、帳戶號碼、客戶姓名、案號**。
    #
    # 為什麼是整個資料夾而不是「只留摘要」——2026-08-16 實際打開量過，
    # run 資料夾裡**沒有一個檔案是摘要**：
    #
    # | 檔案 | 夾帶什麼 |
    # |---|---|
    # | `run.json` | `task_preview`：任務原文前 160 字 |
    # | `result.json` | `claude_output` / `codex_output` / `review_question` |
    # | `timeline.jsonl` | 每一段 prompt 逐字（含 4,845 字的啟動提示）|
    # | `task.md` / `*-output.txt` / `*-session-bootstrap.md` / `knowledge-context.md` | 整份都是內容 |
    #
    # 而同步是雙向比對雜湊的，所以也不能在上傳時改寫內容——改過的檔案會
    # 永遠被讀成「雲端有變更」。要給客戶專案一份真的摘要，得在執行時另外寫
    # 一個只含非內容欄位的檔案，那是另一件事。
    LOCAL_ONLY_PREFIXES = {
        "client": (Path(".aicc/runs"),),
    }

    def __init__(self) -> None:
        self.config_store = CloudSyncConfigStore()
        self._lock = threading.Lock()
        self._scanner = SecurityScanner()

    def load_settings(self) -> CloudSyncSettings:
        return self.config_store.load()

    def save_settings(self, settings: CloudSyncSettings) -> None:
        self.config_store.save(settings)

    def is_configured(self) -> bool:
        settings = self.load_settings()
        return bool(
            settings.enabled
            and settings.root_path
            and Path(settings.root_path).expanduser().is_dir()
        )

    def sync_project(self, project_path: Path) -> SyncResult:
        with self._lock:
            return self._sync_project_locked(project_path)

    def _sync_project_locked(self, project_path: Path) -> SyncResult:
        finished_at = datetime.now(timezone.utc).isoformat()
        settings = self.load_settings()

        if not settings.enabled:
            return SyncResult(
                success=False,
                errors=[t("cloud.not_enabled")],
                finished_at=finished_at,
            )

        root = Path(settings.root_path).expanduser().resolve()
        if not root.is_dir():
            return SyncResult(
                success=False,
                errors=[f"Google Drive 資料夾不存在：{root}"],
                finished_at=finished_at,
            )

        local_project = project_path.expanduser().resolve()
        if not local_project.is_dir():
            return SyncResult(
                success=False,
                errors=[f"本機專案不存在：{local_project}"],
                finished_at=finished_at,
            )

        sensitivity = ProjectRegistry.get_sensitivity(local_project)
        if sensitivity == "sensitive_kyc":
            return SyncResult(
                success=False,
                errors=[
                    t("cloud.kyc_disabled")
                ],
                finished_at=finished_at,
            )

        try:
            metadata = self.ensure_project_metadata(local_project)
            project_id = metadata["project_id"]
            project_name = metadata["name"]
            cloud_project = (
                root
                / self.APP_FOLDER_NAME
                / "projects"
                / project_id
            )
            cloud_project.mkdir(parents=True, exist_ok=True)

            state_path = local_project / ".aicc" / "sync-state.json"
            old_state = self._load_json(state_path, default={})
            old_files = old_state.get("files", {})
            if not isinstance(old_files, dict):
                old_files = {}

            local_only = self.LOCAL_ONLY_PREFIXES.get(sensitivity, ())
            local_files = self._collect_allowed_files(local_project, local_only)
            cloud_files = self._collect_allowed_files(cloud_project, local_only)
            all_relative = sorted(
                set(local_files) | set(cloud_files),
                key=lambda value: value.as_posix().casefold(),
            )

            result = SyncResult(
                success=True,
                project_id=project_id,
                project_name=project_name,
                cloud_path=str(cloud_project),
                finished_at=finished_at,
                withheld=self._withheld_notes(
                    local_project, cloud_project, sensitivity, local_only
                ),
            )
            new_state: dict[str, Any] = {
                "version": 1,
                "project_id": project_id,
                "last_sync_at": finished_at,
                "device_id": settings.device_id,
                "files": {},
            }

            for relative in all_relative:
                local_file = local_project / relative
                cloud_file = cloud_project / relative
                previous = old_files.get(relative.as_posix(), {})
                previous_hash = ""
                if isinstance(previous, dict):
                    previous_hash = str(previous.get("hash", ""))

                try:
                    outcome, current_hash = self._sync_one_file(
                        local_file=local_file,
                        cloud_file=cloud_file,
                        relative=relative,
                        previous_hash=previous_hash,
                        local_project=local_project,
                        cloud_project=cloud_project,
                    )
                except (OSError, ValueError) as exc:
                    result.success = False
                    result.errors.append(
                        f"{relative.as_posix()}：{exc}"
                    )
                    continue

                entry = relative.as_posix()
                if outcome == "blocked":
                    entry = f"{entry}（{self._block_reason(local_file)}）"
                getattr(result, outcome).append(entry)
                if current_hash:
                    new_state["files"][relative.as_posix()] = {
                        "hash": current_hash,
                    }

            self._atomic_write_json(state_path, new_state)
            self._update_catalog(
                root=root,
                metadata=metadata,
                settings=settings,
                updated_at=finished_at,
            )
            return result
        except (OSError, ValueError, KeyError) as exc:
            return SyncResult(
                success=False,
                errors=[str(exc)],
                finished_at=finished_at,
            )

    def _sync_one_file(
        self,
        local_file: Path,
        cloud_file: Path,
        relative: Path,
        previous_hash: str,
        local_project: Path,
        cloud_project: Path,
    ) -> tuple[str, str]:
        local_exists = local_file.is_file()
        cloud_exists = cloud_file.is_file()
        local_hash = self._hash_file(local_file) if local_exists else ""
        cloud_hash = self._hash_file(cloud_file) if cloud_exists else ""

        if local_exists and cloud_exists and local_hash == cloud_hash:
            return "unchanged", local_hash

        if local_exists and not cloud_exists:
            if self._is_upload_blocked(local_file):
                return "blocked", ""
            self._copy_atomic(local_file, cloud_file)
            return "uploaded", local_hash

        if cloud_exists and not local_exists:
            self._copy_atomic(cloud_file, local_file)
            return "downloaded", cloud_hash

        if not local_exists and not cloud_exists:
            return "unchanged", ""

        local_changed = not previous_hash or local_hash != previous_hash
        cloud_changed = not previous_hash or cloud_hash != previous_hash

        if local_changed and not cloud_changed:
            if self._is_upload_blocked(local_file):
                return "blocked", ""
            self._copy_atomic(local_file, cloud_file)
            return "uploaded", local_hash

        if cloud_changed and not local_changed:
            self._copy_atomic(cloud_file, local_file)
            return "downloaded", cloud_hash

        # Both sides changed, or this is a first sync with different content.
        if self._is_upload_blocked(local_file):
            # The conflict cannot be resolved without pushing local content
            # into the shared Drive folder, so leave both sides untouched.
            return "blocked", ""

        # Preserve both versions, then keep the local version as the active file.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        local_conflict = (
            local_project
            / ".aicc"
            / "conflicts"
            / stamp
            / Path(relative.as_posix() + ".cloud")
        )
        cloud_conflict = (
            cloud_project
            / "conflicts"
            / stamp
            / Path(relative.as_posix() + ".local")
        )
        self._copy_atomic(cloud_file, local_conflict)
        self._copy_atomic(local_file, cloud_conflict)
        self._copy_atomic(local_file, cloud_file)
        return "conflicts", local_hash

    def _is_upload_blocked(self, local_file: Path) -> bool:
        report = self._scanner.scan_paths([local_file])
        return report.has_blocking_findings

    def _block_reason(self, local_file: Path) -> str:
        """Which rule held this file back, and where.

        Re-scans the one file rather than threading the report up through
        _sync_one_file: this only runs for a file that was already refused, so
        the second pass costs nothing anyone will notice, and it keeps the
        normal path unchanged.
        """
        report = self._scanner.scan_paths([local_file])
        blocking = [f for f in report.findings if f.severity == "high"]
        if not blocking:
            return t("cloud.security_scan")
        first = blocking[0]
        where = t("cloud.at_line", n=first.line) if first.line else t("cloud.in_filename")
        more = f"，另有 {len(blocking) - 1} 處" if len(blocking) > 1 else ""
        return f"{where}疑似 {first.kind}{more}"

    def ensure_project_metadata(self, project_path: Path) -> dict:
        """Create or refresh `.aicc/project.json` for the given project.

        Delegates to `ProjectRegistry.ensure_project_metadata`, the single
        place that owns project.json, so a sync run does not silently
        overwrite fields (such as project sensitivity) that another part
        of the app already wrote to that file.
        """
        local_project = project_path.expanduser().resolve()
        metadata = ProjectRegistry.ensure_project_metadata(local_project)

        ignore_path = local_project / ".aicc" / ".gitignore"
        if not ignore_path.exists():
            ignore_path.write_text(
                "workflow-result.json\n"
                "sync-state.json\n"
                "conflicts/\n"
                "sessions.json\n"
                "runs/\n"
                "timeline/\n",
                encoding="utf-8",
            )

        return metadata

    def list_cloud_projects(self) -> list[CloudProjectInfo]:
        settings = self.load_settings()
        if not settings.enabled or not settings.root_path:
            return []

        root = Path(settings.root_path).expanduser().resolve()
        catalog_path = root / self.APP_FOLDER_NAME / "catalog.json"
        catalog = self._load_json(catalog_path, default={})
        raw_projects = catalog.get("projects", []) if isinstance(catalog, dict) else []
        result: list[CloudProjectInfo] = []

        if isinstance(raw_projects, list):
            for item in raw_projects:
                if not isinstance(item, dict):
                    continue
                project_id = str(item.get("project_id", "")).strip()
                if not project_id:
                    continue
                result.append(
                    CloudProjectInfo(
                        project_id=project_id,
                        name=str(item.get("name", "")).strip()
                        or project_id,
                        updated_at=str(item.get("updated_at", "")),
                        device_id=str(item.get("device_id", "")),
                    )
                )

        return sorted(
            result,
            key=lambda item: item.updated_at,
            reverse=True,
        )

    def link_cloud_project(
        self,
        project_id: str,
        local_project: Path,
    ) -> SyncResult:
        settings = self.load_settings()
        if not settings.enabled or not settings.root_path:
            return SyncResult(
                success=False,
                errors=[t("cloud.settings_first")],
            )

        root = Path(settings.root_path).expanduser().resolve()
        cloud_project = (
            root / self.APP_FOLDER_NAME / "projects" / project_id
        )
        cloud_metadata = cloud_project / ".aicc" / "project.json"
        local_project = local_project.expanduser().resolve()

        if not cloud_metadata.is_file():
            return SyncResult(
                success=False,
                errors=[t("cloud.link.no_project_json")],
            )

        if not local_project.is_dir():
            return SyncResult(
                success=False,
                errors=[t("cloud.link.pick_existing_git")],
            )

        local_aicc = local_project / ".aicc"
        local_aicc.mkdir(parents=True, exist_ok=True)
        local_metadata_path = local_aicc / "project.json"

        with ProjectRegistry.metadata_write_lock():
            cloud_data = self._load_json(cloud_metadata, default={})
            if not isinstance(cloud_data, dict):
                cloud_data = {}
            # A cloud-side file must never dictate local-only safety
            # policy such as project sensitivity - only this machine sets
            # that. Drop it before merging so local always wins, and so a
            # project with no local sensitivity yet falls back to the
            # normal default instead of silently adopting the cloud value.
            cloud_data.pop("sensitivity", None)

            local_data = self._load_json(local_metadata_path, default={})
            if not isinstance(local_data, dict):
                local_data = {}

            merged = {**cloud_data, **local_data, "project_id": project_id}
            self._atomic_write_json(local_metadata_path, merged)
            ProjectRegistry.ensure_project_metadata(local_project)

        return self.sync_project(local_project)

    @staticmethod
    def _count_files(base: Path, prefix: Path) -> int:
        directory = base / prefix
        if not directory.is_dir():
            return 0
        return sum(1 for path in directory.rglob("*") if path.is_file())

    def _withheld_notes(
        self,
        local_project: Path,
        cloud_project: Path,
        sensitivity: str,
        local_only: tuple[Path, ...],
    ) -> list[str]:
        """講出來留了什麼。安靜地少傳，跟沒有少傳，在畫面上長得一樣。

        雲端那個數字**不是多餘的**：這條政策是 2026-08-16 才加的，在那之前
        同步過的 run 還躺在雲端。同步不會刪遠端檔案（刪掉別人機器上的東西不是
        同步該做的事），所以那些要人自己去清——而不講的話沒有人會知道要清。
        """
        notes: list[str] = []
        label = sensitivity_label(sensitivity)

        for prefix in local_only:
            local_count = self._count_files(local_project, prefix)
            cloud_count = self._count_files(cloud_project, prefix)
            if not local_count and not cloud_count:
                continue
            note = (
                f"{label}：{prefix.as_posix()} 不離開這台機器"
                f"（本機 {local_count} 個檔案未上傳）"
            )
            if cloud_count:
                note += (
                    f"。**雲端還有 {cloud_count} 個是這條規則生效前傳上去的，"
                    "同步不會自動刪除，要自己去清。**"
                )
            notes.append(note)

        return notes

    def _collect_allowed_files(
        self,
        base: Path,
        local_only: tuple[Path, ...] = (),
    ) -> set[Path]:
        """白名單准出去的檔案。`local_only` 的前綴整批不算在內。

        `local_only` 兩邊都要傳（本機與雲端）。只擋本機那一側的話，之前已經
        同步上去的檔案會在下一次同步被**下載回來**，而畫面會說「下載 12」
        ——政策看起來生效了，實際上東西還在雲端而且又回到本機。
        """
        files: set[Path] = set()

        for relative in self.EXACT_FILES:
            if any(relative.is_relative_to(prefix) for prefix in local_only):
                continue
            candidate = base / relative
            if candidate.is_file() and candidate.stat().st_size <= self.MAX_FILE_SIZE:
                files.add(relative)

        for prefix in self.PREFIX_DIRS:
            if any(prefix.is_relative_to(skip) for skip in local_only):
                continue
            directory = base / prefix
            if not directory.is_dir():
                continue
            for candidate in directory.rglob("*"):
                if not candidate.is_file():
                    continue
                if candidate.stat().st_size > self.MAX_FILE_SIZE:
                    continue
                files.add(candidate.relative_to(base))

        return files

    def _update_catalog(
        self,
        root: Path,
        metadata: dict[str, str],
        settings: CloudSyncSettings,
        updated_at: str,
    ) -> None:
        app_root = root / self.APP_FOLDER_NAME
        app_root.mkdir(parents=True, exist_ok=True)
        catalog_path = app_root / "catalog.json"
        catalog = self._load_json(catalog_path, default={})
        raw_projects = catalog.get("projects", []) if isinstance(catalog, dict) else []
        projects: dict[str, dict[str, str]] = {}

        if isinstance(raw_projects, list):
            for item in raw_projects:
                if isinstance(item, dict):
                    project_id = str(item.get("project_id", "")).strip()
                    if project_id:
                        projects[project_id] = {
                            "project_id": project_id,
                            "name": str(item.get("name", "")),
                            "updated_at": str(item.get("updated_at", "")),
                            "device_id": str(item.get("device_id", "")),
                        }

        projects[metadata["project_id"]] = {
            "project_id": metadata["project_id"],
            "name": metadata["name"],
            "updated_at": updated_at,
            "device_id": settings.device_id,
        }
        self._atomic_write_json(
            catalog_path,
            {
                "version": 1,
                "updated_at": updated_at,
                "projects": sorted(
                    projects.values(),
                    key=lambda item: item["name"].casefold(),
                ),
            },
        )

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _copy_atomic(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_name(destination.name + ".aicc-tmp")
        shutil.copy2(source, temp_path)
        temp_path.replace(destination)

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _atomic_write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(path.name + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
