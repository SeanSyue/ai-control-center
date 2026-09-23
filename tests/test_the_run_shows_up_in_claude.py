r"""AICC 跑的對話，要出現在 Claude 桌面版自己的清單裡。

使用者付錢買的會員，工作卻在他自己的軟體裡「不存在」——2026-08-21 之前每次
測試他都要重講一次這件事。根因是 AICC 只寫自己的 `.aicc/sessions.json`，
從來沒碰過桌面版讀的那份索引。

這份守兩件事，第二件比第一件重要：

1. 登記得成功。
2. **登記不成功的時候，那件事要看得見。**

第二件才是真正的風險。那份索引的格式是逆向出來的，廠商改版會讓它無聲失效
——照樣寫、照樣沒有錯誤、而側邊欄就是不出現，跟「這次沒跑」在畫面上一模
一樣。一個只驗「成功路徑」的測試，會在那一天照樣全綠。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services.vendor_session_index import ClaudeSidebarIndex, Registration

ORG = "org-1234"
WORKSPACE = "workspace-5678"


def make_index(tmp_path: Path, with_workspace: bool = True) -> ClaudeSidebarIndex:
    root = tmp_path / "Claude" / "claude-code-sessions"
    if with_workspace:
        (root / ORG / WORKSPACE).mkdir(parents=True)
    else:
        root.mkdir(parents=True)
    return ClaudeSidebarIndex(index_root=root)


def entries(index: ClaudeSidebarIndex) -> list[dict]:
    workspace = index.workspace_dir()
    if workspace is None:
        return []
    out = []
    for path in sorted(workspace.glob("local_*.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


# --- 登記得成功嗎 -----------------------------------------------------------


def test_a_run_lands_in_the_sidebar_list(tmp_path: Path) -> None:
    index = make_index(tmp_path)

    outcome = index.register(
        cli_session_id="abc-123",
        cwd=r"C:\Users\x\Projects\demo",
        title="Claude Developer｜修好那個假綠燈",
        model="claude-sonnet-5",
    )

    assert outcome.ok, outcome.problem
    rows = entries(index)
    assert len(rows) == 1
    assert rows[0]["cliSessionId"] == "abc-123"
    assert rows[0]["title"] == "Claude Developer｜修好那個假綠燈"
    assert rows[0]["cwd"] == r"C:\Users\x\Projects\demo"
    assert rows[0]["isArchived"] is False


def test_the_same_conversation_twice_updates_instead_of_piling_up(
    tmp_path: Path,
) -> None:
    """一次執行會對同一個席位派好幾次工。

    每次都新增的話，使用者的側邊欄會被同一個任務洗版——那比看不到還糟，
    因為他得自己分辨十筆長得一樣的東西哪一筆是最後的。
    """
    index = make_index(tmp_path)

    index.register("abc-123", "C:/p", "第一次")
    second = index.register("abc-123", "C:/p", "第二次")

    assert second.ok
    rows = entries(index)
    assert len(rows) == 1
    assert rows[0]["title"] == "第二次"


def test_the_first_registration_time_is_kept_when_it_is_updated(
    tmp_path: Path,
) -> None:
    """更新的是「最後動過」，不是「什麼時候開始」。

    createdAt 被每次派工蓋掉的話，側邊欄會把一段跑了兩小時的對話顯示成
    「剛剛」，而使用者是靠時間找東西的。
    """
    index = make_index(tmp_path)

    index.register("abc-123", "C:/p", "第一次", created_ms=1_000_000)
    first = entries(index)[0]["createdAt"]
    index.register("abc-123", "C:/p", "第二次")
    row = entries(index)[0]

    assert row["createdAt"] == first
    assert row["lastActivityAt"] >= first


def test_the_title_is_marked_as_generated_not_typed_by_the_user(
    tmp_path: Path,
) -> None:
    """標成 user 會讓桌面版以為使用者親手打過這個名字，然後就不再更新它。"""
    index = make_index(tmp_path)

    index.register("abc-123", "C:/p", "AICC 取的名字")

    assert entries(index)[0]["titleSource"] == "auto"


# --- 失敗看得見嗎（這一段才是重點）-----------------------------------------


def test_a_missing_workspace_folder_is_reported_not_invented(
    tmp_path: Path,
) -> None:
    """找不到資料夾就老實說，**不准自己建一個**。

    那兩層是桌面版自己的識別碼。AICC 猜一組出來只會產生一個沒有人會讀的
    資料夾——而且回報成功。那正是「看起來很確定的錯誤」。
    """
    index = make_index(tmp_path, with_workspace=False)

    outcome = index.register("abc-123", "C:/p", "標題")

    assert not outcome.ok
    assert "桌面版" in outcome.problem
    assert list((tmp_path / "Claude" / "claude-code-sessions").iterdir()) == [], (
        "沒找到資料夾卻自己建了東西出來"
    )


def test_an_empty_session_id_is_refused(tmp_path: Path) -> None:
    index = make_index(tmp_path)

    outcome = index.register("   ", "C:/p", "標題")

    assert not outcome.ok
    assert entries(index) == []


def test_the_write_is_read_back_and_a_mismatch_is_reported(
    tmp_path: Path,
) -> None:
    """寫完要讀回來比對。

    這條守的是那個最貴的失敗方式：**無聲地壞掉**。如果哪天寫進去的東西
    不是我們以為的東西（欄位改名、序列化出錯），這裡要喊，而不是回一個
    ok=True 然後讓使用者對著空白的側邊欄困惑。
    """
    index = make_index(tmp_path)
    workspace = index.workspace_dir()
    assert workspace is not None
    target = workspace / "local_deadbeef.json"
    target.write_text(json.dumps({"cliSessionId": "別人"}), encoding="utf-8")

    outcome = index._verify(target, {"cliSessionId": "我", "cwd": "", "title": ""})

    assert not outcome.ok
    assert "對不上" in outcome.problem
    assert "cliSessionId" in outcome.problem


def test_an_unreadable_file_after_writing_is_reported(tmp_path: Path) -> None:
    index = make_index(tmp_path)
    workspace = index.workspace_dir()
    assert workspace is not None
    target = workspace / "local_broken.json"
    target.write_text("{ 這不是 JSON", encoding="utf-8")

    outcome = index._verify(target, {"cliSessionId": "x"})

    assert not outcome.ok
    assert "讀不回來" in outcome.problem


def test_extra_fields_from_a_newer_app_version_are_not_read_as_failure(
    tmp_path: Path,
) -> None:
    """廠商多加一個欄位不是失敗。

    比對只看「這一筆指向哪一段對話」的那幾個欄位。全欄位比對的話，桌面版
    每加一個設定，AICC 就會開始每次都喊登記失敗——而喊狼來了的守衛會被
    無視，然後真的壞掉那天也沒人看。
    """
    index = make_index(tmp_path)
    index.register("abc-123", "C:/p", "標題")

    workspace = index.workspace_dir()
    assert workspace is not None
    path = next(workspace.glob("local_*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["somethingNewInVersion300"] = True
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    again = index.register("abc-123", "C:/p", "標題")

    assert again.ok, again.problem


def test_the_summary_says_which_way_it_went() -> None:
    """給畫面用的一句話。兩種結果都要能被讀出來。"""
    assert "已登記" in Registration(True).summary
    assert "沒有登記" in Registration(False, "資料夾不見了").summary
    assert "資料夾不見了" in Registration(False, "資料夾不見了").summary


# --- AICC 自己的解析器讀得懂嗎 ----------------------------------------------


def test_what_we_wrote_is_what_aicc_itself_can_read_back(tmp_path: Path) -> None:
    """用另一支獨立寫的程式讀我們寫的東西。

    `session_scanner._claude_title_index` 是 2026-08-07 為了**讀**這份索引
    而逆向出來的，跟這個登記器沒有共用任何程式碼。它讀得出來，代表格式是
    對的——而不是「我寫的東西只有我自己看得懂」。
    """
    from app.services.session_scanner import SessionScanner

    index = make_index(tmp_path)
    index.register(
        cli_session_id="abc-123",
        cwd=r"C:\Users\x\Projects\demo",
        title="Claude Developer｜修好那個假綠燈",
        model="claude-sonnet-5",
    )

    scanner = SessionScanner()
    scanner._claude_index_roots = lambda: [index.index_root]
    read_back = scanner._claude_title_index()

    assert "abc-123" in read_back
    row = read_back["abc-123"]
    assert row["title"] == "Claude Developer｜修好那個假綠燈"
    assert row["cwd"] == r"C:\Users\x\Projects\demo"
    assert row["model"] == "claude-sonnet-5"
    assert row["archived"] is False
    assert row["created_at"] and row["updated_at"]


# --- 引擎真的有去做嗎 -------------------------------------------------------
#
# 上面每一條都可以全綠，而引擎一次都沒呼叫過這個登記器——那正是這個專案
# 反覆出現的缺陷（算出來了、沒有人讀它）。所以下面跑真的 WorkflowWorker。

import os  # noqa: E402

import pytest  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.timeline_store import TimelineStore  # noqa: E402
from tests.fakes import (  # noqa: E402
    cli_result,
    declare_verify_command,
    calls_after_intake,
    scripted_cli_runner_class,
    with_intake,
)

PASS_REVIEW = (
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)


def _run_worker(tmp_path: Path, monkeypatch, make_workspace: bool):
    appdata = tmp_path / "appdata"
    if make_workspace:
        (appdata / "Claude" / "claude-code-sessions" / ORG / WORKSPACE).mkdir(
            parents=True
        )
    monkeypatch.setenv("APPDATA", str(appdata))
    QApplication.instance() or QApplication([])

    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 修好那一頁\n", encoding="utf-8")
    declare_verify_command(project)

    script = [
        cli_result('{"result": "done", "session_id": "sess-claude-1"}'),
        cli_result("1 passed"),
        cli_result(PASS_REVIEW),
    ]
    runner_class = scripted_cli_runner_class(with_intake(script))
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
            max_fix_attempts=1,
        ),
    )
    completed: list = []
    worker.completed.connect(completed.append)
    worker.run()
    QApplication.instance().processEvents()
    assert completed, "這一輪沒跑完，底下每一條都會是恆真的"
    return completed[0], project, appdata, calls_after_intake(runner_class.calls)


def test_the_claude_call_carries_an_official_display_name(
    tmp_path: Path, monkeypatch
) -> None:
    """`--name` 是官方旗標，會出現在 `/resume` 選單。

    這是唯一一條完全走官方介面的路，所以它不准只是註解裡的一句話。
    """
    _, _, _, calls = _run_worker(tmp_path, monkeypatch, make_workspace=True)

    claude_call = next(c for c in calls if c["arguments"][0] == "claude")
    arguments = claude_call["arguments"]

    assert "--name" in arguments, arguments
    name = arguments[arguments.index("--name") + 1]
    assert name.startswith("AICC·"), name
    assert "project" in name, name
    assert "\n" not in name and len(name) <= 60


def test_a_finished_run_appears_in_the_sidebar_list(
    tmp_path: Path, monkeypatch
) -> None:
    """跑完之後，桌面版的清單裡要真的多一筆。"""
    _, project, appdata, _ = _run_worker(tmp_path, monkeypatch, make_workspace=True)

    root = appdata / "Claude" / "claude-code-sessions"
    written = list((root / ORG / WORKSPACE).glob("local_*.json"))

    assert written, "跑完了，但桌面版的清單裡一筆都沒有"
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in written]
    assert any(row["cliSessionId"] == "sess-claude-1" for row in rows), rows
    assert any(str(project) == row["cwd"] for row in rows), rows


def test_failing_to_register_leaves_a_visible_note_and_does_not_kill_the_run(
    tmp_path: Path, monkeypatch
) -> None:
    """**這是整份最重要的一條。**

    桌面版沒開過（或廠商換了地方），登記就會失敗。那時候兩件事都要成立：

    1. 執行照常跑完——登記失敗不是紅燈，沒有人證明任何東西壞了。
    2. **那件事要留下痕跡。**無聲失敗跟「這次沒跑」在畫面上一模一樣，而
       使用者會以為是 AICC 沒動。
    """
    result, project, _, _ = _run_worker(tmp_path, monkeypatch, make_workspace=False)

    assert result["workflow_status"] == "COMPLETED"

    events = TimelineStore(project).load_events(run_id=result["run_id"])
    notes = [e for e in events if e.event_type == "vendor_index_not_updated"]

    assert notes, "登記失敗了，但時間軸上一個字都沒有"
    assert "桌面版" in notes[0].content
    assert "逐字稿也還在" in notes[0].content


def test_register_itself_refuses_to_report_success_it_did_not_verify(
    tmp_path: Path, monkeypatch
) -> None:
    """讀回來比對這件事，要真的接在 `register()` 上。

    2026-08-21 的負向驗證抓到：原本只有一條直接呼叫 `_verify` 的測試，所以
    把 `register()` 尾巴那一行改成無條件回報成功，**整份還是全綠**——
    整個模組存在的理由沒有被測到。

    這裡讓寫出去的內容跟打算寫的不一樣（模擬序列化出錯，或是另一個行程在
    中間蓋掉檔案），然後要求 `register()` 自己說不。
    """
    from app.services import vendor_session_index as module

    index = make_index(tmp_path)
    monkeypatch.setattr(
        module.json, "dumps", lambda *a, **k: '{"cliSessionId": "不是我寫的"}'
    )

    outcome = index.register("abc-123", "C:/p", "標題")

    assert not outcome.ok, (
        "寫進去的東西不是我們以為的東西，register() 卻回報成功"
    )
    assert "對不上" in outcome.problem


# --- Codex 那一半：清單上的標題 ---------------------------------------------


def test_the_first_line_of_the_prompt_is_a_usable_title(tmp_path, monkeypatch) -> None:
    """Codex 的對話清單直接拿使用者的第一句話當標題。

    2026-08-21 查 Codex 自己的資料庫確認：`threads.title` 就等於
    `first_user_message`。所以 AICC 送出去的第一句話**就是**使用者在 Codex
    清單上看到的東西——原本是「AI Control Center 新 session 啟動提示：」，
    於是每一筆長得一模一樣，等於看得到卻認不出來。

    這條測的是真的派工送出去的內容（`input_text`），不是那個組字串的函式
    ——測函式的話，把它從呼叫路徑上拔掉，測試照樣全綠。
    """
    from app.core.orchestrator import Orchestrator

    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 修好 memory_budget 的假綠燈\n\n細節在這裡。\n", encoding="utf-8"
    )
    declare_verify_command(project)

    runner_class = scripted_cli_runner_class(
        [cli_result('{"result": "done", "session_id": "s1"}')]
    )
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    orchestrator = Orchestrator(
        project,
        settings=AIExecutionSettings(auto_session_bootstrap=True),
    )
    # 啟動脈絡的**內容**不是這條在測的東西，包在外面那一層才是。
    # 固定它，這條就只會因為包法變了而紅。
    monkeypatch.setattr(
        orchestrator, "_session_bootstrap_context", lambda task, provider: "（脈絡）"
    )
    orchestrator.run_claude()

    # 這一條直接叫 `run_claude()`，沒有經過工作流程，所以沒有需求確認那一步。
    sent = runner_class.calls[0]["input_text"]
    assert "（脈絡）" in sent, "啟動脈絡沒有被包進去，這條會恆真"

    first_line = sent.strip().splitlines()[0]

    assert first_line.startswith("AICC·"), first_line
    assert "project" in first_line, first_line
    assert "修好 memory_budget 的假綠燈" in first_line, first_line
    assert "啟動提示" not in first_line, (
        "第一句又變回那句每一筆都一樣的廢話了"
    )
