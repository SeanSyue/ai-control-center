from pathlib import Path

from app.services.cloud_sync import GoogleDriveFolderSync


def test_ai_settings_is_in_cloud_allowlist() -> None:
    assert Path(".aicc/ai-settings.json") in GoogleDriveFolderSync.EXACT_FILES


def test_planning_request_is_in_cloud_allowlist() -> None:
    assert (
        Path(".aicc/planning-request.md")
        in GoogleDriveFolderSync.EXACT_FILES
    )


def test_shared_memory_files_are_in_cloud_allowlist() -> None:
    expected = {
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
    }

    assert expected <= GoogleDriveFolderSync.EXACT_FILES
