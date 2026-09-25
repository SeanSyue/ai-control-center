"""同步只准寫在同步資料夾的 `AIControlCenter/projects/<project_id>/` 裡面。

## 改壞的樣子

2026-09-25 外部審查（A11）量到的：`project_id` 讀自 `.aicc/project.json`，
一個沒驗證過的字串，直接接在路徑後面。把它改成另一個絕對路徑，Windows 的
`Path("root") / "C:/elsewhere"` 會把 root 整個丟掉——`sync_project()` 回報
成功，而 `task.md` 被寫到同步資料夾外面。

那份 metadata 可能被改過，也可能是從別台電腦同步過來的；兩種都不該決定
這台電腦要寫到哪裡。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.cloud_sync import CloudSyncSettings, GoogleDriveFolderSync
from app.services.project_registry import ProjectRegistry


def _configured(tmp_path: Path, monkeypatch) -> tuple[GoogleDriveFolderSync, Path]:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    drive = tmp_path / "drive"
    drive.mkdir()
    service = GoogleDriveFolderSync()
    service.save_settings(
        CloudSyncSettings(enabled=True, root_path=str(drive), device_id="test-device")
    )
    return service, drive


def _project_with_id(tmp_path: Path, project_id: str) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)
    metadata_path = project / ".aicc" / "project.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    data["project_id"] = project_id
    metadata_path.write_text(json.dumps(data), encoding="utf-8")
    return project


def test_an_absolute_project_id_does_not_send_the_sync_elsewhere(
    tmp_path: Path, monkeypatch
) -> None:
    service, drive = _configured(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    project = _project_with_id(tmp_path, str(elsewhere))

    result = service.sync_project(project)

    assert not (elsewhere / ".aicc" / "task.md").exists(), (
        "同步跟著 project_id 寫到同步資料夾外面去了"
    )
    assert result.success is False


@pytest.mark.parametrize(
    "project_id",
    ["..", "../outside", "a/b", "C:/elsewhere", "", "not-a-uuid"],
)
def test_linking_refuses_an_id_that_is_not_a_plain_uuid(
    tmp_path: Path, monkeypatch, project_id: str
) -> None:
    service, drive = _configured(tmp_path, monkeypatch)
    local = tmp_path / "local"
    local.mkdir()

    result = service.link_cloud_project(project_id, local)

    assert result.success is False
    assert not (local / ".aicc" / "project.json").exists()


def test_a_same_named_junction_cannot_lead_the_sync_elsewhere(
    tmp_path: Path, monkeypatch
) -> None:
    """格式完全正確的 UUID，但雲端那一格已經是一個指到別處的連結資料夾。

    這一條給「解析之後必須剛好在 projects 下一層」那道檢查長牙齒——
    光看格式擋不到它。
    """
    _winapi = pytest.importorskip("_winapi")
    service, drive = _configured(tmp_path, monkeypatch)
    project_id = "6f1c2a10-0000-4000-8000-00000000b001"
    project = _project_with_id(tmp_path, project_id)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    projects = drive / "AIControlCenter" / "projects"
    projects.mkdir(parents=True)
    _winapi.CreateJunction(str(elsewhere), str(projects / project_id))

    result = service.sync_project(project)

    assert not (elsewhere / ".aicc" / "task.md").exists(), (
        "同步跟著連結資料夾寫到同步資料夾外面去了"
    )
    assert result.success is False


def test_a_tidy_looking_id_that_is_not_a_uuid_is_still_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """格式那一道自己的牙齒。

    `legacy-project` 不會跑出同步資料夾——「剛好在下一層」那道放得過它。
    但它不是 AICC 產生得出來的值，所以它只可能是被改過的 metadata。
    **只收自己產生得出來的東西**，才不用去猜每一種 Windows 路徑的怪招
    （結尾的點、`CON`、`名稱:串流`⋯⋯）。
    """
    service, drive = _configured(tmp_path, monkeypatch)
    project = _project_with_id(tmp_path, "legacy-project")

    result = service.sync_project(project)

    assert result.success is False
    assert not (drive / "AIControlCenter" / "projects" / "legacy-project").exists()


def test_an_ordinary_project_still_syncs(tmp_path: Path, monkeypatch) -> None:
    service, drive = _configured(tmp_path, monkeypatch)
    project = _project_with_id(tmp_path, "6f1c2a10-0000-4000-8000-00000000b002")

    result = service.sync_project(project)

    assert result.success is True
    assert (
        drive / "AIControlCenter" / "projects"
        / "6f1c2a10-0000-4000-8000-00000000b002" / ".aicc" / "task.md"
    ).is_file()
