import json
import threading

import pytest

from pathlib import Path

from app.services.project_registry import ProjectRegistry


def test_initialize_project_creates_aicc_files(tmp_path: Path) -> None:
    project = tmp_path / "sample"
    project.mkdir()

    result = ProjectRegistry.initialize_project(project)

    assert result == project.resolve()
    assert (project / ".aicc" / "task.md").is_file()
    assert (project / ".aicc" / "project.json").is_file()
    assert (project / ".aicc" / ".gitignore").is_file()
    assert (project / ".aicc" / "knowledge" / "PROJECT_BRIEF.md").is_file()
    assert (project / ".aicc" / "knowledge" / "ARCHITECTURE.md").is_file()
    assert (project / ".aicc" / "knowledge" / "manifest.json").is_file()


def test_initialize_project_ignores_generated_session_history(
    tmp_path: Path,
) -> None:
    project = tmp_path / "sample-history"
    project.mkdir()

    ProjectRegistry.initialize_project(project)

    ignore = (project / ".aicc" / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert "sessions.json" in ignore
    assert "runs/" in ignore
    assert "timeline/" in ignore


def test_initialize_project_adds_aicc_to_local_git_exclude(
    tmp_path: Path,
) -> None:
    project = tmp_path / "sample-git"
    (project / ".git" / "info").mkdir(parents=True)
    exclude = project / ".git" / "info" / "exclude"
    exclude.write_text("# local excludes\n", encoding="utf-8")

    ProjectRegistry.initialize_project(project)

    assert ".aicc/" in exclude.read_text(encoding="utf-8")


def test_project_sensitivity_defaults_to_personal(tmp_path: Path) -> None:
    project = tmp_path / "sample-sensitivity"
    project.mkdir()

    assert ProjectRegistry.get_sensitivity(project) == "personal"


def test_project_sensitivity_roundtrip(tmp_path: Path) -> None:
    project = tmp_path / "sample-sensitivity-2"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    ProjectRegistry.set_sensitivity(project, "sensitive_kyc")

    assert ProjectRegistry.get_sensitivity(project) == "sensitive_kyc"

    data = json.loads(
        (project / ".aicc" / "project.json").read_text(encoding="utf-8")
    )
    assert data["sensitivity"] == "sensitive_kyc"
    # Existing project.json fields (project_id etc.) must survive the update.
    assert "project_id" in data


def test_project_sensitivity_rejects_unknown_level(tmp_path: Path) -> None:
    project = tmp_path / "sample-sensitivity-3"
    project.mkdir()

    with pytest.raises(ValueError):
        ProjectRegistry.set_sensitivity(project, "top_secret")


def test_ensure_project_metadata_preserves_extra_fields_and_ids(
    tmp_path: Path,
) -> None:
    project = tmp_path / "sample-metadata"
    project.mkdir()
    ProjectRegistry.set_sensitivity(project, "sensitive_kyc")

    first = ProjectRegistry.ensure_project_metadata(project)
    project_id = first["project_id"]
    created_at = first["created_at"]
    assert first["sensitivity"] == "sensitive_kyc"

    second = ProjectRegistry.ensure_project_metadata(project)

    # Refreshing metadata (as every cloud sync run does) must keep the
    # identity fields stable and must not drop unrelated keys such as
    # sensitivity.
    assert second["project_id"] == project_id
    assert second["created_at"] == created_at
    assert second["sensitivity"] == "sensitive_kyc"
    assert ProjectRegistry.get_sensitivity(project) == "sensitive_kyc"


def test_get_sensitivity_fails_closed_on_corrupted_file(tmp_path: Path) -> None:
    project = tmp_path / "sample-corrupt"
    metadata_path = project / ".aicc" / "project.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text("{not valid json", encoding="utf-8")

    assert ProjectRegistry.get_sensitivity(project) == "sensitive_kyc"


def test_get_sensitivity_fails_closed_on_non_dict_json(tmp_path: Path) -> None:
    project = tmp_path / "sample-array"
    metadata_path = project / ".aicc" / "project.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text("[1, 2, 3]", encoding="utf-8")

    assert ProjectRegistry.get_sensitivity(project) == "sensitive_kyc"


def test_get_sensitivity_fails_closed_on_unrecognized_value(
    tmp_path: Path,
) -> None:
    project = tmp_path / "sample-unknown-level"
    metadata_path = project / ".aicc" / "project.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        json.dumps({"sensitivity": "made_up_future_level"}),
        encoding="utf-8",
    )

    assert ProjectRegistry.get_sensitivity(project) == "sensitive_kyc"


def test_get_sensitivity_defaults_when_field_missing_but_file_valid(
    tmp_path: Path,
) -> None:
    project = tmp_path / "sample-no-field"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    data = json.loads(
        (project / ".aicc" / "project.json").read_text(encoding="utf-8")
    )
    assert "sensitivity" not in data
    assert ProjectRegistry.get_sensitivity(project) == "personal"


def test_metadata_write_lock_serializes_concurrent_callers(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def holder() -> None:
        with ProjectRegistry.metadata_write_lock():
            order.append("A-enter")
            entered.set()
            release.wait(timeout=5)
            order.append("A-exit")

    thread_a = threading.Thread(target=holder)
    thread_a.start()
    assert entered.wait(timeout=5)

    def waiter() -> None:
        with ProjectRegistry.metadata_write_lock():
            order.append("B-enter")

    thread_b = threading.Thread(target=waiter)
    thread_b.start()
    thread_b.join(timeout=0.3)
    # If the lock did not actually serialize these two callers, B would
    # already be done by now.
    assert thread_b.is_alive()

    release.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert order == ["A-enter", "A-exit", "B-enter"]


def test_metadata_write_lock_is_reentrant(tmp_path: Path) -> None:
    project = tmp_path / "sample-reentrant"
    project.mkdir()

    # Must not deadlock: ensure_project_metadata acquires the same lock
    # internally, so a caller that already holds it (like
    # GoogleDriveFolderSync.link_cloud_project) must still be able to call
    # it without hanging.
    with ProjectRegistry.metadata_write_lock():
        result = ProjectRegistry.ensure_project_metadata(project)

    assert "project_id" in result


def test_initialize_project_and_ensure_metadata_share_one_writer(
    tmp_path: Path,
) -> None:
    project = tmp_path / "sample-metadata-2"
    project.mkdir()

    ProjectRegistry.initialize_project(project)
    data = json.loads(
        (project / ".aicc" / "project.json").read_text(encoding="utf-8")
    )

    assert data["version"] == 1
    assert data["name"] == project.name
    assert "project_id" in data
    assert "created_at" in data
    assert "updated_at" in data

    # initialize_project must remain idempotent: calling it again on an
    # already-initialized project must not change the stored project_id.
    project_id = data["project_id"]
    ProjectRegistry.initialize_project(project)
    data_again = json.loads(
        (project / ".aicc" / "project.json").read_text(encoding="utf-8")
    )
    assert data_again["project_id"] == project_id
