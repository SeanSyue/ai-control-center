import json
from pathlib import Path

from app.services.cloud_sync import (
    CloudSyncSettings,
    GoogleDriveFolderSync,
)
from app.services.project_registry import ProjectRegistry


def configured_sync(tmp_path: Path, monkeypatch):
    appdata = tmp_path / "appdata"
    drive = tmp_path / "drive"
    drive.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))

    service = GoogleDriveFolderSync()
    service.save_settings(
        CloudSyncSettings(
            enabled=True,
            root_path=str(drive),
            device_id="test-device",
        )
    )
    return service, drive


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    aicc = project / ".aicc"
    aicc.mkdir(parents=True)
    (aicc / "task.md").write_text("# 任務\n\nA\n", encoding="utf-8")
    return project


def test_initial_sync_uploads_task(tmp_path: Path, monkeypatch) -> None:
    service, drive = configured_sync(tmp_path, monkeypatch)
    project = make_project(tmp_path)

    result = service.sync_project(project)

    assert result.success
    assert ".aicc/task.md" in result.uploaded
    cloud_task = (
        drive
        / "AIControlCenter"
        / "projects"
        / result.project_id
        / ".aicc"
        / "task.md"
    )
    assert cloud_task.read_text(encoding="utf-8") == "# 任務\n\nA\n"


def test_cloud_change_downloads_to_local(tmp_path: Path, monkeypatch) -> None:
    service, drive = configured_sync(tmp_path, monkeypatch)
    project = make_project(tmp_path)
    first = service.sync_project(project)
    cloud_task = (
        drive
        / "AIControlCenter"
        / "projects"
        / first.project_id
        / ".aicc"
        / "task.md"
    )
    cloud_task.write_text("# 任務\n\nCloud\n", encoding="utf-8")

    result = service.sync_project(project)

    assert result.success
    assert ".aicc/task.md" in result.downloaded
    assert (project / ".aicc" / "task.md").read_text(
        encoding="utf-8"
    ) == "# 任務\n\nCloud\n"


def test_conflict_preserves_both_versions(tmp_path: Path, monkeypatch) -> None:
    service, drive = configured_sync(tmp_path, monkeypatch)
    project = make_project(tmp_path)
    first = service.sync_project(project)
    local_task = project / ".aicc" / "task.md"
    cloud_task = (
        drive
        / "AIControlCenter"
        / "projects"
        / first.project_id
        / ".aicc"
        / "task.md"
    )
    local_task.write_text("LOCAL", encoding="utf-8")
    cloud_task.write_text("CLOUD", encoding="utf-8")

    result = service.sync_project(project)

    assert result.success
    assert ".aicc/task.md" in result.conflicts
    assert local_task.read_text(encoding="utf-8") == "LOCAL"
    assert cloud_task.read_text(encoding="utf-8") == "LOCAL"
    conflict_files = list((project / ".aicc" / "conflicts").rglob("*.cloud"))
    assert conflict_files
    assert conflict_files[0].read_text(encoding="utf-8") == "CLOUD"


def test_secret_like_file_is_blocked_not_uploaded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, drive = configured_sync(tmp_path, monkeypatch)
    project = make_project(tmp_path)
    (project / ".aicc" / "planning-request.md").write_text(
        "api_key=sk-thisshouldnotleak1234567890\n",
        encoding="utf-8",
    )

    result = service.sync_project(project)

    assert result.success
    # The entry carries the reason alongside the path now - a bare count, or
    # even a bare filename, left the user no way to tell a stray .env from
    # their own decision log, and it was silently the decision log.
    blocked = [entry for entry in result.blocked
               if entry.startswith(".aicc/planning-request.md")]
    assert blocked, result.blocked
    assert "api_key" in blocked[0] and "第 1 行" in blocked[0]
    assert ".aicc/planning-request.md" not in result.uploaded
    assert ".aicc/planning-request.md" in result.summary
    cloud_file = (
        drive
        / "AIControlCenter"
        / "projects"
        / result.project_id
        / ".aicc"
        / "planning-request.md"
    )
    assert not cloud_file.exists()


def test_sync_preserves_sensitivity_field_across_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, drive = configured_sync(tmp_path, monkeypatch)
    project = make_project(tmp_path)
    ProjectRegistry.set_sensitivity(project, "client")

    first = service.sync_project(project)
    assert first.success is True
    assert ProjectRegistry.get_sensitivity(project) == "client"

    # A second sync run must not silently reset the field back to default.
    second = service.sync_project(project)
    assert second.success is True
    assert ProjectRegistry.get_sensitivity(project) == "client"


def _write_cloud_project_metadata(
    drive: Path,
    project_id: str,
) -> Path:
    cloud_metadata = (
        drive
        / "AIControlCenter"
        / "projects"
        / project_id
        / ".aicc"
        / "project.json"
    )
    cloud_metadata.parent.mkdir(parents=True)
    cloud_metadata.write_text(
        json.dumps(
            {
                "version": 1,
                "project_id": project_id,
                "name": "cloud-name",
                "created_at": "2020-01-01T00:00:00+00:00",
                "updated_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return cloud_metadata


def test_link_cloud_project_does_not_leak_or_bypass_local_sensitivity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, drive = configured_sync(tmp_path, monkeypatch)
    cloud_project_id = "cloud-proj-kyc"
    _write_cloud_project_metadata(drive, cloud_project_id)

    local_project = tmp_path / "local-project"
    local_project.mkdir()
    ProjectRegistry.set_sensitivity(local_project, "sensitive_kyc")

    result = service.link_cloud_project(cloud_project_id, local_project)

    # Linking must not silently wipe the local sensitivity flag, and must
    # not sync a KYC-marked project the instant it is linked.
    assert ProjectRegistry.get_sensitivity(local_project) == "sensitive_kyc"
    assert result.success is False
    assert "sensitive-KYC" in result.summary
    assert not (
        drive
        / "AIControlCenter"
        / "projects"
        / cloud_project_id
        / ".aicc"
        / "planning-request.md"
    ).exists()


def test_link_cloud_project_adopts_cloud_project_id_for_non_sensitive_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, drive = configured_sync(tmp_path, monkeypatch)
    cloud_project_id = "cloud-proj-normal"
    _write_cloud_project_metadata(drive, cloud_project_id)

    local_project = tmp_path / "local-project-2"
    local_project.mkdir()

    result = service.link_cloud_project(cloud_project_id, local_project)

    assert result.success is True
    assert result.project_id == cloud_project_id
    local_metadata = json.loads(
        (local_project / ".aicc" / "project.json").read_text(
            encoding="utf-8"
        )
    )
    assert local_metadata["project_id"] == cloud_project_id


def test_sensitive_kyc_project_is_not_synced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, drive = configured_sync(tmp_path, monkeypatch)
    project = make_project(tmp_path)
    ProjectRegistry.set_sensitivity(project, "sensitive_kyc")

    result = service.sync_project(project)

    assert result.success is False
    assert "sensitive-KYC" in result.errors[0]
    assert not (drive / "AIControlCenter" / "projects").exists()


def test_session_and_timeline_files_are_synced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, drive = configured_sync(tmp_path, monkeypatch)
    project = make_project(tmp_path)
    aicc = project / ".aicc"
    (aicc / "sessions.json").write_text("{}", encoding="utf-8")
    (aicc / "timeline").mkdir()
    (aicc / "timeline" / "timeline.jsonl").write_text(
        '{"source":"claude"}\n',
        encoding="utf-8",
    )
    (aicc / "runs" / "run-1").mkdir(parents=True)
    (aicc / "runs" / "run-1" / "result.json").write_text(
        '{"review_status":"PASS"}',
        encoding="utf-8",
    )

    result = service.sync_project(project)

    assert result.success
    assert ".aicc/sessions.json" in result.uploaded
    assert ".aicc/timeline/timeline.jsonl" in result.uploaded
    assert ".aicc/runs/run-1/result.json" in result.uploaded
