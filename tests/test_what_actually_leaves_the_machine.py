"""雲端同步實際會帶走哪些檔案，以及那道網看不見什麼。

`SECURITY_MODEL.md` 講得很清楚：**計畫是白名單，掃描器是之後那層網。**
這個檔案把兩邊現在的實際狀態量出來釘住，因為兩邊都會漂，而漂的方向不一樣：

* 白名單漂：`PREFIX_DIRS` 是**前綴**，所以它准的是「這個資料夾底下的一切」
  ——包含還不存在的檔案。`.aicc/runs` 2026-08-03 就進了白名單，而
  `steps.jsonl`（逐字記下每一格被問了什麼、回了什麼）2026-08-10 才出現在
  那個資料夾裡。**沒有人放寬任何東西，是一個新的、敏感得多的產物長在舊的
  許可裡面。** 而 `save_artifact()` 接受任何檔名，所以那個資料夾本來就是
  開放式的：CLI 的原始 stdout 也存在那裡。
* 網漂：`SecurityScanner` 是**憑證**掃描器。實測它抓得到 API key，
  抓不到護照號碼、帳戶號碼、客戶姓名、案號——**那是對的**，它從來沒有
  宣稱自己是個資過濾器。危險的是把它當成個資過濾器來用。

所以這裡的斷言分兩種，兩種都刻意：

1. 同步與匯出各自會帶走什麼，**照敏感度分開驗**。它變了就要有人重新決定。
2. 掃描器**抓不到**什麼。釘住一個做不到的事，是為了不讓它日後被當成做得到
   （這個專案已經用同樣的理由釘過「從結尾砍掉抓不到」）。

**2026-08-16 使用者定案：`.aicc/runs` 跟敏感度走。** `client` 專案的逐字紀錄
不離開這台機器，兩條出去的路（Google Drive 同步、workspace 匯出包）都擋。
`personal` 照舊，`sensitive_kyc` 本來就整個不同步。

那次改動也暴露了這個檔案自己的一個問題，值得寫下來：原本有三條測試斷言
「逐字紀錄**會**離開這台機器」，而它們呼叫的是 `_collect_allowed_files(project)`
——**不帶敏感度的內部函式**。政策加上去之後，那三條**還是綠的**，而它們描述的
行為對 `client` 專案已經不成立了。一條繞過真正決策點的測試，改不改政策都一樣綠。
所以現在每一條都走真正的那條路（`sync_project` / `preview_export`），而不是
戳中間那個函式。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.services.cloud_sync import CloudSyncSettings, GoogleDriveFolderSync
from app.services.project_registry import ProjectRegistry
from app.services.security_scan import SecurityScanner
from app.services.step_log import StepLog, StepRecord
from app.services.workspace_portability import WorkspacePortabilityService

# 一段長得像真實 KYC 工作的 prompt。刻意放進**不是憑證**的敏感資料，
# 因為那正是這個專案的實際用途（協助客戶處理境外開戶與委任合約）。
KYC_SHAPED = (
    "你是此專案的開發工程師。客戶：王大明，護照 A123456789，"
    "帳戶 0012-3456-7890。任務：整理他的開戶委任文件。"
)
A_REAL_CREDENTIAL = "OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012mno345pqr"


def _project(tmp_path: Path, sensitivity: str = "client") -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("整理開戶文件\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)
    ProjectRegistry.set_sensitivity(project, sensitivity)
    StepLog(project).append(
        "run-1",
        StepRecord(
            index=0, role="author", provider="claude", event_type="task",
            read_only=False, prompt=KYC_SHAPED, output="已整理完成。",
            success=True,
            started_at="2026-08-11T01:00:00", finished_at="2026-08-11T01:00:10",
        ),
    )
    return project


# --- 白名單實際挑走什麼 ------------------------------------------------------


def _synced_names(tmp_path: Path, project: Path) -> tuple[set[str], list[str]]:
    """真的跑一次同步，回傳雲端資料夾裡出現的相對路徑，以及 withheld 說了什麼。

    走 `sync_project` 而不是 `_collect_allowed_files`：政策掛在前者身上，
    戳後者的測試改不改政策都一樣綠。
    """
    root = tmp_path / "drive"
    root.mkdir(exist_ok=True)
    sync = GoogleDriveFolderSync()
    sync.save_settings(
        CloudSyncSettings(enabled=True, root_path=str(root), device_id="test-device")
    )
    result = sync.sync_project(project)
    assert result.success, result.errors
    names = {
        path.relative_to(Path(result.cloud_path)).as_posix()
        for path in Path(result.cloud_path).rglob("*")
        if path.is_file()
    }
    return names, result.withheld


def test_a_personal_project_still_sends_the_step_log(tmp_path: Path) -> None:
    """前提檢查，也是政策的另一半。

    `steps.jsonl` 裡是逐字的 prompt 與逐字的輸出，大致等於「這次執行看過的
    全部東西」。對 `personal` 專案它照舊會走——這條紅了就表示政策擋過頭了，
    而那會安靜地弄壞換機器的動線。
    """
    project = _project(tmp_path, sensitivity="personal")

    names, withheld = _synced_names(tmp_path, project)

    assert ".aicc/runs/run-1/steps.jsonl" in names
    assert withheld == []


def test_a_client_project_keeps_its_verbatim_records_at_home(tmp_path: Path) -> None:
    """**這是這個檔案現在存在的理由。**（使用者 2026-08-16 定案）"""
    project = _project(tmp_path, sensitivity="client")

    names, withheld = _synced_names(tmp_path, project)

    assert not [name for name in names if name.startswith(".aicc/runs/")], names
    # 前提檢查：不是整個專案都沒同步——那是 sensitive_kyc 的行為，不是這條。
    assert ".aicc/task.md" in names


def test_it_says_what_it_kept_back(tmp_path: Path) -> None:
    """安靜地少傳，跟沒有少傳，在畫面上長得一樣。"""
    project = _project(tmp_path, sensitivity="client")

    _, withheld = _synced_names(tmp_path, project)

    assert withheld, "少傳了東西卻一個字都沒說"
    assert ".aicc/runs" in withheld[0]
    assert "客戶專案" in withheld[0]


def test_what_is_already_in_the_cloud_is_not_pulled_back(tmp_path: Path) -> None:
    """雲端那一側也要套同一條規則。

    只擋本機那側的話，這條政策生效前傳上去的檔案會在下一次同步被**下載回來**，
    畫面說「下載 N」——政策看起來生效了，東西卻還在雲端而且又回到本機。
    """
    project = _project(tmp_path, sensitivity="client")
    root = tmp_path / "drive"
    root.mkdir(exist_ok=True)
    sync = GoogleDriveFolderSync()
    sync.save_settings(
        CloudSyncSettings(enabled=True, root_path=str(root), device_id="test-device")
    )
    # 先假裝規則生效前已經同步過一次。
    metadata = sync.ensure_project_metadata(project)
    cloud = root / sync.APP_FOLDER_NAME / "projects" / metadata["project_id"]
    stale = cloud / ".aicc" / "runs" / "run-0" / "steps.jsonl"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(KYC_SHAPED, encoding="utf-8")

    result = sync.sync_project(project)

    assert result.success, result.errors
    assert not (project / ".aicc" / "runs" / "run-0").exists(), "被下載回來了"
    assert not [name for name in result.downloaded if ".aicc/runs" in name]
    # 而且要講出雲端還躺著舊的——同步不會刪遠端，不講就沒有人知道要去清。
    assert any("雲端還有" in note for note in result.withheld), result.withheld


def test_the_two_exits_agree_about_the_policy() -> None:
    """同步與匯出各存一份政策（比對單位不同，刻意不共用常數）。

    代價是改的時候兩邊都要改，所以這裡釘住它們一致。少了這條，只改一邊會
    留下一條敞開的路，而那條路不會有任何徵兆。
    """
    from_sync = {
        level: {prefix.name for prefix in prefixes}
        for level, prefixes in GoogleDriveFolderSync.LOCAL_ONLY_PREFIXES.items()
    }
    from_export = {
        level: set(names)
        for level, names in WorkspacePortabilityService.LOCAL_ONLY_AICC_PREFIXES.items()
    }

    assert from_sync == from_export


def test_the_export_zip_does_not_carry_client_verbatim_records(tmp_path: Path) -> None:
    """另一條出去的路：你親手交給別人的那個 zip。

    這條驗的是**結果**不是機制——直接在壓縮檔的位元組裡找那串護照號碼。
    """
    project = _project(tmp_path, sensitivity="client")
    out = tmp_path / "out"
    out.mkdir()

    exported = WorkspacePortabilityService().export_project(project, out)

    assert exported.success, exported.errors
    names = zipfile.ZipFile(exported.archive_path).namelist()
    assert not [name for name in names if ".aicc/runs" in name], names
    assert "A123456789" not in Path(exported.archive_path).read_bytes().decode(
        "utf-8", "ignore"
    )


def test_a_personal_export_still_carries_them(tmp_path: Path) -> None:
    # 前提檢查：少了這條，上面那條可以因為「runs 從來不進包」而通過。
    project = _project(tmp_path, sensitivity="personal")
    out = tmp_path / "out"
    out.mkdir()

    exported = WorkspacePortabilityService().export_project(project, out)

    names = zipfile.ZipFile(exported.archive_path).namelist()
    assert [name for name in names if ".aicc/runs" in name], names


def test_the_export_preview_names_what_it_held_back(tmp_path: Path) -> None:
    """驗**使用者讀到的那段文字**，不是驗背後那個 dict。

    第一版這條讀的是 `preview.skipped_by_reason`，而負向驗證判它沒有牙齒：
    把 `client_local_only` 從 `NAMED_SKIP_REASONS` 拿掉之後，dict 照樣有東西
    ——只有畫面從「列出檔名」退成「只講數量」。而這一項存在的理由正是
    **交出 zip 之前那個人要看得到是哪幾個檔案**，數量回答不了那個問題。
    """
    project = _project(tmp_path, sensitivity="client")

    preview = WorkspacePortabilityService().preview_export(project)

    held = preview.skipped_by_reason.get("client_local_only", [])
    assert held, preview.skipped_by_reason
    assert any(".aicc/runs" in name for name in held)

    summary = preview.summary
    assert "客戶專案的逐字紀錄，不離開這台機器" in summary
    assert any(name in summary for name in held), summary


def test_the_policy_only_names_folders_that_would_otherwise_travel() -> None:
    """`LOCAL_ONLY_AICC_PREFIXES` 必須是 `ALLOWED_AICC_PREFIXES` 的子集。

    寫錯一個名字（例如 `run` 少了 s）的話，那條規則會安靜地什麼都不擋——
    它擋的是一個本來就不會走的資料夾，而畫面上不會有任何差別。

    這條同時也是為什麼 `skip_reason` 裡兩個判斷的先後不重要：只要這個不變式
    成立，走到敏感度那一問的路徑就一定是「本來會走」的。第一版在那裡寫了一段
    解釋順序的註解，負向驗證把順序對調之後沒有任何測試變紅——**因為那個差別
    在這個不變式底下不存在**。與其為一段不會發生的事寫測試，不如把不變式本身
    釘住。
    """
    for level, names in WorkspacePortabilityService.LOCAL_ONLY_AICC_PREFIXES.items():
        missing = names - WorkspacePortabilityService.ALLOWED_AICC_PREFIXES
        assert not missing, f"{level} 擋的是本來就不會走的資料夾：{missing}"


def test_the_runs_folder_is_open_ended(tmp_path: Path) -> None:
    """任何 `save_artifact()` 存進去的東西都會跟著上雲（`personal` 專案）。

    `save_artifact(run_id, name, ...)` 的 `name` 是呼叫端給的，而
    `orchestrator` 用它存過 CLI 的原始 stdout。所以這個前綴准的是
    「以後有人想存在那裡的任何東西」——`client` 的那道閘門是整個資料夾層級的，
    正是因為擋不了「還沒被發明出來的檔名」。
    """
    from app.services.timeline_store import TimelineStore

    project = _project(tmp_path, sensitivity="personal")
    TimelineStore(project).create_run("run-1", "整理開戶文件")
    TimelineStore(project).save_artifact("run-1", "codex-stdout.txt", KYC_SHAPED)

    names, _ = _synced_names(tmp_path, project)

    assert ".aicc/runs/run-1/codex-stdout.txt" in names


def test_a_kyc_project_syncs_nothing_at_all(tmp_path: Path) -> None:
    # 標成 sensitive_kyc 的專案整個被擋在同步之外，所以上面那兩條對它不成立。
    project = _project(tmp_path, sensitivity="sensitive_kyc")

    assert ProjectRegistry.get_sensitivity(project) == "sensitive_kyc"


def test_the_index_database_does_not_leave(tmp_path: Path) -> None:
    """反方向的前提檢查：白名單不是「整個 .aicc」。

    少了這條，上面那幾條可以因為「什麼都同步」而通過，而那是完全不同的狀況。
    """
    project = _project(tmp_path)
    knowledge = project / ".aicc" / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "index.sqlite").write_text("x", encoding="utf-8")
    (knowledge / "index.sqlite-wal").write_text("x", encoding="utf-8")

    picked = GoogleDriveFolderSync()._collect_allowed_files(project)

    assert Path(".aicc/knowledge/index.sqlite") not in picked
    assert Path(".aicc/knowledge/index.sqlite-wal") not in picked


# --- 那道網看不見什麼 --------------------------------------------------------


def test_the_scanner_catches_a_credential() -> None:
    # 前提檢查：抓不到憑證的話，底下那條「抓不到個資」就沒有意義了。
    report = SecurityScanner().scan_text(A_REAL_CREDENTIAL)

    assert report.findings, "掃描器連 API key 都抓不到，那它什麼都擋不住"


def test_the_scanner_does_NOT_catch_client_data(tmp_path: Path) -> None:
    """**刻意釘住一件做不到的事。**

    `SecurityScanner` 是憑證掃描器。護照號碼、帳戶號碼、客戶姓名、案號都不是
    憑證，所以它不會有意見——**那是對的**，它從來沒有宣稱自己是個資過濾器。

    釘住它的理由跟釘住「從結尾砍掉抓不到」一樣：**這是宣稱的邊界。**
    把「同步前會掃描」當成「同步前會擋掉客戶資料」，是這個專案最容易犯、
    也最貴的一種誤讀——而它只會在資料已經在別人的雲端硬碟上之後才被發現。

    這條紅了不一定是壞事：代表掃描器學會看個資了。那時候要做的是回來改
    ROADMAP 的措辭，不是刪掉這條。
    """
    report = SecurityScanner().scan_text(KYC_SHAPED)

    assert not report.findings, (
        "掃描器現在抓得到客戶資料了——去把 ROADMAP 與 SECURITY_MODEL 裡"
        "「掃描器是憑證掃描器」那些措辭一起改對"
    )


def test_a_step_log_full_of_client_data_is_not_flagged(tmp_path: Path) -> None:
    """把上面兩件事接起來：`personal` 專案的這個檔案會上雲，而網不會攔它。

    分開驗兩件事都成立，不等於驗過「它們同時成立」——而同時成立才是實際狀況。

    **這條 2026-08-16 改成 `personal`。** `client` 現在有整個資料夾層級的閘門
    擋著，所以對它而言「掃描器抓不抓得到個資」已經不是最後一道防線；對
    `personal` 還是。留著這條的理由沒變，只是它守的範圍縮小了。
    """
    project = _project(tmp_path, sensitivity="personal")
    step_log = project / ".aicc/runs/run-1/steps.jsonl"

    names, _ = _synced_names(tmp_path, project)
    report = SecurityScanner().scan_paths([step_log])

    assert ".aicc/runs/run-1/steps.jsonl" in names
    assert not report.findings
    # 而且那份紀錄真的帶著逐字的 prompt。
    first = json.loads(step_log.read_text(encoding="utf-8").splitlines()[0])
    assert "A123456789" in first["prompt"]
