from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.project_knowledge import ProjectKnowledgeStore
from app.core import prompt_markers
from app.i18n import t


SENSITIVITY_LEVELS = ("personal", "client", "sensitive_kyc")
DEFAULT_SENSITIVITY = "personal"
FAIL_CLOSED_SENSITIVITY = "sensitive_kyc"
# **存 key 不存字**（模組層取出來會在 import 的當下凍住）。
# 給人看的那一句用 `sensitivity_label()`。
SENSITIVITY_LABEL_KEYS = {
    "personal": "sensitivity.personal",
    "client": "sensitivity.client",
    "sensitive_kyc": "sensitivity.kyc",
}

# .aicc/project.json is written from more than one thread: the UI thread
# (set_sensitivity, called synchronously from a combo box handler) and a
# background QThread during Google Drive sync (ensure_project_metadata).
# Both are unlocked read-modify-write cycles without this lock, which can
# lose an update (e.g. a just-set sensitivity flag silently reverted by a
# sync run that read the file a moment earlier). Reentrant so a caller that
# already holds it (link_cloud_project wrapping a call into
# ensure_project_metadata) does not deadlock itself.
_PROJECT_METADATA_LOCK = threading.RLock()


@dataclass(frozen=True)
class ProjectEntry:
    name: str
    path: str

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).expanduser().resolve()


class ProjectRegistry:
    def __init__(self) -> None:
        appdata = os.environ.get("APPDATA")

        if appdata:
            self.config_dir = Path(appdata) / "AIControlCenter"
        else:
            self.config_dir = Path.home() / ".aicontrolcenter"

        self.config_path = self.config_dir / "projects.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> tuple[list[ProjectEntry], str]:
        if not self.config_path.exists():
            return [], ""

        try:
            data = json.loads(
                self.config_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return [], ""

        raw_projects = data.get("projects", [])
        projects: list[ProjectEntry] = []
        seen_paths: set[str] = set()

        if isinstance(raw_projects, list):
            for item in raw_projects:
                if not isinstance(item, dict):
                    continue

                raw_path = str(item.get("path", "")).strip()
                raw_name = str(item.get("name", "")).strip()

                if not raw_path:
                    continue

                try:
                    resolved = str(
                        Path(raw_path).expanduser().resolve()
                    )
                except OSError:
                    continue

                key = resolved.casefold()
                if key in seen_paths:
                    continue

                seen_paths.add(key)
                projects.append(
                    ProjectEntry(
                        name=raw_name or Path(resolved).name,
                        path=resolved,
                    )
                )

        last_project = str(data.get("last_project", "")).strip()
        return projects, last_project

    def save(
        self,
        projects: list[ProjectEntry],
        last_project: str,
    ) -> None:
        payload = {
            "version": 1,
            "last_project": last_project,
            "projects": [asdict(project) for project in projects],
        }

        temp_path = self.config_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.config_path)

    @staticmethod
    def validate_project(path: Path) -> tuple[bool, str]:
        resolved = path.expanduser().resolve()

        if not resolved.is_dir():
            return False, t("project.not_a_folder")

        task_path = resolved / ".aicc" / "task.md"
        if not task_path.is_file():
            return (
                False,
                t("project.not_initialised"),
            )

        return True, ""

    @staticmethod
    def initialize_project(path: Path) -> Path:
        resolved = path.expanduser().resolve()

        if not resolved.is_dir():
            raise ValueError(t("project.not_a_folder"))

        aicc_path = resolved / ".aicc"
        aicc_path.mkdir(parents=True, exist_ok=True)

        task_path = aicc_path / "task.md"
        if not task_path.exists():
            # 標題來自 `prompt_markers`——**它會被讀回來比對**，
            # 所以寫出去的那一行跟認得回來的那一行必須是同一個值。
            task_path.write_text(
                f"{prompt_markers.TASK_HEADING}\n\n{t('project.task_placeholder')}\n",
                encoding="utf-8",
            )

        project_metadata_path = aicc_path / "project.json"
        if not project_metadata_path.exists():
            ProjectRegistry.ensure_project_metadata(resolved)

        ignore_path = aicc_path / ".gitignore"
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

        ProjectRegistry._ensure_local_git_exclude(resolved)
        knowledge = ProjectKnowledgeStore(resolved)
        knowledge.ensure_memory_files()
        knowledge.export_manifest()
        return resolved

    @staticmethod
    def _project_metadata_path(path: Path) -> Path:
        return path.expanduser().resolve() / ".aicc" / "project.json"

    @staticmethod
    def _read_project_metadata(project_file: Path) -> dict:
        if not project_file.is_file():
            return {}
        try:
            data = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_project_metadata(project_file: Path, data: dict) -> None:
        project_file.parent.mkdir(parents=True, exist_ok=True)
        temp_path = project_file.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(project_file)

    @staticmethod
    @contextmanager
    def metadata_write_lock():
        """Serialize a read-modify-write against `.aicc/project.json`.

        Use this to wrap any multi-step read/merge/write against
        project.json that is not already covered by a single call to
        `ensure_project_metadata` / `set_sensitivity` (for example
        `GoogleDriveFolderSync.link_cloud_project`, which reads both the
        cloud and local files before writing). The lock is reentrant, so
        it is safe to call `ensure_project_metadata` while already holding
        it.
        """
        with _PROJECT_METADATA_LOCK:
            yield

    @staticmethod
    def ensure_project_metadata(path: Path) -> dict:
        """Create or refresh `.aicc/project.json` for a project.

        This is the single place that reads/writes project.json. It keeps
        `project_id` and `created_at` stable across repeated calls and
        preserves any other keys already stored there (for example
        `sensitivity`) instead of rebuilding the file from a fixed field
        list - callers such as cloud sync call this on every run and must
        not silently drop fields another part of the app already wrote.
        """
        with ProjectRegistry.metadata_write_lock():
            resolved = path.expanduser().resolve()
            project_file = ProjectRegistry._project_metadata_path(resolved)
            data = ProjectRegistry._read_project_metadata(project_file)
            now = datetime.now(timezone.utc).isoformat()

            project_id = (
                str(data.get("project_id", "")).strip() or str(uuid.uuid4())
            )
            created_at = str(data.get("created_at", "")).strip() or now
            name = str(data.get("name", "")).strip() or resolved.name

            data.update(
                {
                    "version": 1,
                    "project_id": project_id,
                    "name": name,
                    "created_at": created_at,
                    "updated_at": now,
                }
            )
            ProjectRegistry._write_project_metadata(project_file, data)
            return data

    @staticmethod
    def get_sensitivity(path: Path) -> str:
        """Return this project's sensitivity tier.

        Fails closed, not open: a project.json that cannot be read or
        parsed, or that holds a `sensitivity` value this build does not
        recognize, is treated as the *most* restrictive tier
        (`sensitive_kyc`) rather than the least restrictive default -
        this value gates whether a project is eligible for cloud sync and
        workspace export, so an unreadable file must never silently
        downgrade a project's protection. A project.json that is readable
        but simply has no `sensitivity` key at all (never classified, not
        corrupted) still returns the normal default.
        """
        project_file = ProjectRegistry._project_metadata_path(path)
        if not project_file.is_file():
            return DEFAULT_SENSITIVITY

        try:
            raw = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return FAIL_CLOSED_SENSITIVITY

        if not isinstance(raw, dict):
            return FAIL_CLOSED_SENSITIVITY

        value = str(raw.get("sensitivity", "")).strip()
        if not value:
            return DEFAULT_SENSITIVITY
        return value if value in SENSITIVITY_LEVELS else FAIL_CLOSED_SENSITIVITY

    # 這個值**為什麼**是這個值。畫面需要它，因為三種來源的意思完全不同：
    #
    #   chosen      他自己選的
    #   default     他還沒選過（新專案）
    #   unreadable  **設定檔讀不到，所以退到最嚴的那一級**
    #
    # 第三種是安全關鍵。頁面設計 2026-08-24 查到的：
    #
    # > 規格原本要把它標成「出廠預設，你還沒選過」，而使用者最合理的反應
    # > 是把它改低。壞掉的專案會被手動降級，執行紀錄就外流了，
    # > 而畫面上完全正常。
    #
    # 他要知道的不是「這是預設值」（暗示可以放心改），
    # 是「**這個值不是因為你選了它**」。
    SOURCE_CHOSEN = "chosen"
    SOURCE_DEFAULT = "default"
    SOURCE_UNREADABLE = "unreadable"

    @staticmethod
    def sensitivity_source(path: Path) -> str:
        """這個專案的敏感度是**怎麼來的**。

        跟 `get_sensitivity()` 走同一條路，所以兩者不會給出互相矛盾的答案
        ——那是這個專案已經得過的病（同一個事實兩份實作）。
        """
        project_file = ProjectRegistry._project_metadata_path(path)
        if not project_file.is_file():
            return ProjectRegistry.SOURCE_DEFAULT

        try:
            raw = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ProjectRegistry.SOURCE_UNREADABLE

        if not isinstance(raw, dict):
            return ProjectRegistry.SOURCE_UNREADABLE

        value = str(raw.get("sensitivity", "")).strip()
        if not value:
            return ProjectRegistry.SOURCE_DEFAULT
        if value not in SENSITIVITY_LEVELS:
            return ProjectRegistry.SOURCE_UNREADABLE
        return ProjectRegistry.SOURCE_CHOSEN

    @staticmethod
    def set_sensitivity(path: Path, level: str) -> None:
        if level not in SENSITIVITY_LEVELS:
            raise ValueError(f"未知的敏感度分類：{level}")

        with ProjectRegistry.metadata_write_lock():
            project_file = ProjectRegistry._project_metadata_path(path)
            data = ProjectRegistry._read_project_metadata(project_file)
            data["sensitivity"] = level
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            ProjectRegistry._write_project_metadata(project_file, data)

    @staticmethod
    def _ensure_local_git_exclude(project_path: Path) -> None:
        git_dir = project_path / ".git"
        if not git_dir.is_dir():
            return
        exclude_path = git_dir / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = exclude_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
        normalized = {
            line.strip().replace("\\", "/")
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        if ".aicc/" in normalized or ".aicc" in normalized:
            return
        prefix = "" if not content or content.endswith("\n") else "\n"
        with exclude_path.open("a", encoding="utf-8") as stream:
            stream.write(prefix + ".aicc/\n")


def sensitivity_label(level: str) -> str:
    """那一級在畫面上叫什麼。查不到就用最嚴的那一級的說法——**不回空白**。"""
    return t(SENSITIVITY_LABEL_KEYS.get(level, SENSITIVITY_LABEL_KEYS[FAIL_CLOSED_SENSITIVITY]))
