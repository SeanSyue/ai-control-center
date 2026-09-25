"""匯入的專案只能落在使用者選的資料夾**裡面一層**。

## 改壞的樣子

2026-09-25 外部審查（A10）量到的：壓縮檔的 manifest 寫 `project_name=".."`，
`_safe_name()` 只拿掉 `<>:"/\\|?*`，`..` 原封不動留下來。專案根目錄於是變成
**選定資料夾的上一層**，而後面那道 `_safe_destination()` 是對著那個已經跑出去
的根目錄在比，所以一個叫 `victim.txt` 的 entry 把選定資料夾外面的同名檔案蓋掉，
匯入還回報成功。

壓縮檔是不可信的輸入——匯入的整個意思就是它從別台電腦來。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.services.workspace_portability import WorkspacePortabilityService


def _archive_named(tmp_path: Path, project_name: str) -> Path:
    archive = tmp_path / "crafted.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "AICC_EXPORT_MANIFEST.json",
            json.dumps({"project_name": project_name}),
        )
        zf.writestr("victim.txt", "written by the archive")
    return archive


def test_a_project_named_dot_dot_does_not_overwrite_the_folder_above(
    tmp_path: Path,
) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("original", encoding="utf-8")
    archive = _archive_named(tmp_path, "..")
    destination = tmp_path / "dest"

    result = WorkspacePortabilityService().import_project(archive, destination)

    assert victim.read_text(encoding="utf-8") == "original", (
        "選定資料夾外面的檔案被匯入蓋掉了"
    )
    if result.success:
        assert Path(result.project_path).parent == destination.resolve()


@pytest.mark.parametrize(
    "name",
    ["..", ".", "...", " .. ", "a/../..", "..\\..", "CON", "nul", "com1", "LPT9.txt", "   "],
)
def test_the_project_folder_is_always_a_direct_child_of_the_chosen_folder(
    tmp_path: Path, name: str
) -> None:
    archive = _archive_named(tmp_path, name)
    destination = tmp_path / "dest"

    result = WorkspacePortabilityService().import_project(archive, destination)

    assert result.success is True
    project = Path(result.project_path)
    assert project.parent == destination.resolve(), (
        f"專案名稱 {name!r} 讓專案落在 {project}，不是選定資料夾的下一層"
    )
    assert project.name.split(".")[0].upper() not in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }, f"專案名稱 {name!r} 變成 Windows 的保留裝置名稱 {project.name!r}"


def test_a_same_named_junction_cannot_lead_the_import_elsewhere(
    tmp_path: Path,
) -> None:
    """名字完全正常，但選定資料夾裡已經有一個同名的連結資料夾指到別處。

    `_safe_name()` 擋不到這個——名字沒有問題，問題在它**解析之後**在哪。
    這一條給 `import_project` 裡那道「必須剛好是下一層」的檢查長牙齒。
    """
    _winapi = pytest.importorskip("_winapi")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    destination = tmp_path / "dest"
    destination.mkdir()
    _winapi.CreateJunction(str(elsewhere), str(destination / "csv-summary"))
    archive = _archive_named(tmp_path, "csv-summary")

    result = WorkspacePortabilityService().import_project(archive, destination)

    assert not (elsewhere / "victim.txt").exists(), (
        "匯入跟著連結資料夾寫到選定資料夾外面去了"
    )
    assert result.success is False


def test_an_ordinary_name_is_kept(tmp_path: Path) -> None:
    archive = _archive_named(tmp_path, "csv-summary")
    destination = tmp_path / "dest"

    result = WorkspacePortabilityService().import_project(archive, destination)

    assert result.success is True
    assert Path(result.project_path) == destination.resolve() / "csv-summary"
