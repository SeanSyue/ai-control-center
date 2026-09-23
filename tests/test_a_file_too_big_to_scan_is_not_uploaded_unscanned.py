"""大到掃不動的檔案**不准就這樣傳到共用的 Drive 資料夾去**。

## 使用者會看到什麼（沒有這幾條的話）

同步結果那一列寫著「已上傳」，`success=True`，沒有 blocked、沒有 errors——
而那個檔案裡的 API key 原封不動躺在 Drive 副本的第一行。畫面上跟一次真的
通過密鑰掃描的上傳**一個字都不差**。

2026-08-24 實測出來的：同一把 `sk-` 金鑰放在同一個資料夾的兩個檔案，
一次同步，42 bytes 那個被擋下並附上「疑似 api_key」，840 KB 那個上傳成功。
兩條界線各自演進——同步准 5 MB，掃描只讀 512 KB——中間那 4.5 MB 就是
「會傳、但永遠不會被讀」的窗口。

## 這幾條擋不住什麼

擋不住「掃描器認不出來的機密」。`SecurityScanner` 是**憑證**掃描器：
它抓 API key、token、密碼，**不抓**客戶姓名、護照號碼、案號。這幾條守的是
「該掃的有沒有真的被掃到」，不是「掃到的夠不夠多」。
"""
from __future__ import annotations

from pathlib import Path

from app.services.cloud_sync import CloudSyncSettings, GoogleDriveFolderSync
from app.services.security_scan import MAX_SCAN_BYTES, SecurityScanner

# 一把真的會被 `openai_key` 那條規則認出來的金鑰形狀。
A_KEY = "sk-live1234567890abcdefghij"

# 撐大檔案用的填充。刻意用有空格的英文句子：長度到了但沒有任何一段連續
# 48 字，所以它自己不會撞上 `long_secret`——不然這幾條測到的會是填充物，
# 不是那把金鑰。
FILLER_LINE = "the quick brown fox jumps over the lazy dog again and again\n"


def _fill_to(path: Path, size: int, first_line: str) -> None:
    body = first_line.rstrip("\n") + "\n"
    body += FILLER_LINE * ((size - len(body)) // len(FILLER_LINE) + 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _configured_sync(tmp_path: Path, monkeypatch) -> tuple[GoogleDriveFolderSync, Path]:
    appdata = tmp_path / "appdata"
    drive = tmp_path / "drive"
    drive.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    service = GoogleDriveFolderSync()
    service.save_settings(
        CloudSyncSettings(enabled=True, root_path=str(drive), device_id="test-device")
    )
    return service, drive


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n\nA\n", encoding="utf-8")
    return project


def test_the_scanner_reads_every_file_the_sync_is_willing_to_upload() -> None:
    """**掃描範圍必須涵蓋同步範圍。**兩個常數，一條規則。

    這一條是靜態的那一半：它在任何一邊先動的那一刻就變紅，不必等到真的有人
    的金鑰被傳上去。改壞的樣子——把 `MAX_SCAN_BYTES` 調回 512 KB，或者有人
    覺得「Drive 可以放大一點」把 `MAX_FILE_SIZE` 調到 20 MB——兩種都會讓
    中間長出一段沒有人讀過就出得去的區間。

    擋不住什麼：它只比大小。掃描因為別的理由沒讀到那個檔案（權限、編碼、
    略過清單），這一條看不到——那幾條在下面。
    """
    assert MAX_SCAN_BYTES >= GoogleDriveFolderSync.MAX_FILE_SIZE, (
        f"掃描只讀 {MAX_SCAN_BYTES} bytes，而同步准 "
        f"{GoogleDriveFolderSync.MAX_FILE_SIZE} bytes 的檔案上傳。"
        "中間那一段是「會傳、但永遠不會被讀」的窗口，"
        "而它剛好是逐字 prompt 與逐字輸出躺著的大小。"
    )


def test_a_key_inside_a_840kb_file_does_not_reach_the_shared_drive(
    tmp_path: Path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    840 KB——超過舊的 512 KB 上限、遠低於同步的 5 MB 上限，也就是那個窗口
    正中間。使用者會看到的：這個檔案出現在「已擋下」而不是「已上傳」，
    而 Drive 那一側根本沒有這個檔案。

    改壞了的樣子：同步結果說「已上傳」，`success=True`，而 Drive 副本第一行
    就是那把金鑰。
    """
    service, drive = _configured_sync(tmp_path, monkeypatch)
    project = _project(tmp_path)
    big = project / ".aicc" / "notes" / "session-notes.md"
    _fill_to(big, 840 * 1024, f"OPENAI_API_KEY={A_KEY}")
    assert big.stat().st_size > 512 * 1024, "測試資料沒有落在那個窗口裡"

    result = service.sync_project(project)

    # `blocked` 那一列後面會接上「為什麼」（「第 1 行疑似 api_key」），
    # 所以比對開頭而不是整串——那句理由是給人看的，會改。
    relative = ".aicc/notes/session-notes.md"
    assert any(entry.startswith(relative) for entry in result.blocked), (
        f"840 KB 的檔案沒有被擋下。uploaded={result.uploaded}／"
        f"blocked={result.blocked}"
    )
    assert relative not in result.uploaded
    copied = (
        drive
        / "AIControlCenter"
        / "projects"
        / result.project_id
        / ".aicc"
        / "notes"
        / "session-notes.md"
    )
    assert not copied.exists(), "金鑰已經在共用資料夾裡了"


def test_the_same_key_in_a_small_file_is_caught_too(
    tmp_path: Path, monkeypatch
) -> None:
    """前提檢查：**這把金鑰本來就抓得到。**

    少了這一條，上面那條有可能是因為「這串字根本沒有規則認得」而過的——
    那會是一條永遠綠的假檢查，而它守的正好是同一種病。
    """
    service, drive = _configured_sync(tmp_path, monkeypatch)
    project = _project(tmp_path)
    small = project / ".aicc" / "notes" / "tiny.md"
    small.parent.mkdir(parents=True, exist_ok=True)
    small.write_text(f"OPENAI_API_KEY={A_KEY}\n", encoding="utf-8")

    result = service.sync_project(project)

    assert any(
        entry.startswith(".aicc/notes/tiny.md") for entry in result.blocked
    ), result.blocked


def test_a_project_living_under_a_folder_named_node_modules_is_still_scanned(
    tmp_path: Path,
) -> None:
    """略過清單比對的是**專案裡面**那幾層，不是使用者的整條硬碟路徑。

    使用者會看到的（沒有這一條的話）：把專案放在任何一層叫 `node_modules`／
    `.git`／`.venv` 的資料夾底下，整個專案的每一個檔案都被靜悄悄略過，
    報告寫「掃了 0 個、沒有阻擋項目」，然後全部傳上去。

    擋不住什麼：專案**裡面**真的有一個 `node_modules` 的時候照樣略過，
    那是刻意的——那裡面的東西不是使用者寫的。
    """
    project = tmp_path / "node_modules" / "my-project"
    (project).mkdir(parents=True)
    (project / "config.md").write_text(f"api_key={A_KEY}\n", encoding="utf-8")

    report = SecurityScanner().scan_project(project)

    assert report.scanned_files == 1, (
        f"專案上面那一層叫 node_modules，整個專案就沒被掃："
        f"scanned={report.scanned_files}／skipped={report.skipped_files}"
    )
    assert report.has_blocking_findings is True


def test_a_single_file_check_without_a_project_root_never_skips_by_folder_name(
    tmp_path: Path,
) -> None:
    """沒有專案界線可以參考的時候，**一層都不略過**。

    `cloud_sync` 逐檔問「這一個能不能傳」的時候不帶 root——那條路上
    「路徑裡有 node_modules」這件事一點資訊都沒有，因為它可能是使用者放
    專案的地方。寧可多掃一個檔案，也不要整批不掃然後回報「沒有阻擋項目」。
    """
    lonely = tmp_path / "node_modules" / "notes.md"
    lonely.parent.mkdir(parents=True)
    lonely.write_text(f"api_key={A_KEY}\n", encoding="utf-8")

    report = SecurityScanner().scan_paths([lonely])

    assert report.scanned_files == 1
    assert report.has_blocking_findings is True
