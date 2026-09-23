"""AI 執行設定搬進網頁之後，**跑的必須是他在網頁上看到的那一份**。

2026-08-27 老闆的原話：

    > AI 執行設定那邊一樣吃到頁面，不能按照我剛剛講的去改嗎?

那 22 個控制項與任務編輯器 2026-09-01 搬進陣容頁，原生面板從主視窗的版面上
拿掉（物件留著當影子）。搬家造出來的新風險是**同一份設定有兩個入口**：

| 入口 | 寫到哪 |
|---|---|
| 陣容頁那一段 | 硬碟（`AISettingsStore`、`.aicc/task.md`）|
| 那個看不見的原生面板 | 它自己的 widget |

而執行路徑跟**面板**拿（`current_settings()`／`save_task()`）。

> 不同步的話會發生這件事：他在網頁把模型改成 opus、把任務打好，按下開始
> 執行——跑起來用的是舊模型，而 `save_task()` 還會**拿面板那份空的任務
> 蓋掉他剛剛寫的**。空的任務會擋下整輪，而他明明看得到自己打的字。

**這個形狀沒有任何既有測試看得到**：兩邊都「有值」，只是不是同一個值，
而畫面上完全正常。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.ai_settings import AISettingsStore  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.ai_settings_panel import AISettingsPanel  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    ProjectRegistry.initialize_project(project)
    return project


@pytest.fixture
def both(tmp_path, monkeypatch):
    """兩個入口，指著同一個專案。

    **不要用 module 層級的 fixture**：活過整個 module 的 Qt widget 沒有人收，
    而這個 repo 的收尾走 `app/ui/shutdown.py` ＋ `pytest_sessionfinish`
    ——躺在 module 命名空間裡的面板不在那條路上（同一個坑
    `test_the_web_and_the_native_panel_offer_the_same_choices` 踩過，
    死法是收尾 `0xC0000374`）。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = _project(tmp_path)
    native = AISettingsPanel(project)
    web = LineupWebPanel(project)
    web._scanned = True
    web._hits = {"claude": [], "codex": []}
    yield native, web, project
    native.setParent(None)
    native.deleteLater()
    web.deleteLater()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def test_a_setting_changed_in_the_web_is_what_the_run_gets(both) -> None:
    """他在網頁改了模型，跑的就要是那一個。"""
    native, web, _project_path = both
    assert native.current_settings().claude_model != "opus", (
        "出廠值本來就是 opus 的話，這一條斷言沒有對照組"
    )

    web.handle_action("set_run_setting", "claude_model:opus")

    # 執行路徑跑之前會做的那一件事。
    native.refresh_from_disk()
    assert native.current_settings().claude_model == "opus", (
        "他在網頁改了模型，而跑起來用的是那個看不見的面板上的舊值"
    )


def test_the_task_typed_in_the_web_is_not_overwritten_by_the_hidden_panel(both) -> None:
    """**這一條是這份檔案存在的理由。**

    `save_task()` 把面板那個（看不見的）任務框寫進 `.aicc/task.md`。
    面板沒有跟著硬碟走的話，它會拿一份舊的、甚至空的，蓋掉他剛剛在
    網頁上打的東西——而那是他這一輪唯一的題目。
    """
    native, web, project = both
    web.handle_action("set_task", "把陣容頁的工作線改成整頁可捲。")
    task_file = project / ".aicc" / "task.md"
    assert task_file.read_text(encoding="utf-8").strip() == (
        "把陣容頁的工作線改成整頁可捲。"
    )

    native.refresh_from_disk()
    assert native.save_task() is True, "重讀之後任務不該是空的"
    assert task_file.read_text(encoding="utf-8").strip() == (
        "把陣容頁的工作線改成整頁可捲。"
    ), "那個看不見的面板把他在網頁打的任務蓋掉了"


def test_a_factory_profile_never_touches_the_permission_mode(both) -> None:
    """套用出廠組合**不准動權限模式**。

    原生面板那一支的註解寫著為什麼，一個字都不能少：

        在設定頁把某個客戶專案設成「手動授權」，接著在這個面板碰一下
        組合下拉選單，它**無聲地變回「安全自動」**。

    三張表是**額度**預設（模型、推理強度、輪數、上下文），
    安全姿態跟著使用者走。網頁這一段是同一份設定的第二個入口——
    **第二個入口把第一個入口的安全設定悄悄改掉，是這個產品最不能出的那一種事。**
    """
    _native, web, project = both
    store = AISettingsStore(project)
    settings = store.load_project()
    settings.claude_permission_mode = "plan"
    store.save_project(settings)

    web.handle_action("apply_run_profile", "deep")

    after = store.load_project()
    assert after.claude_permission_mode == "plan", (
        "套一張出廠組合把權限模式改掉了 —— 安全姿態跟著使用者走，"
        "不跟著額度預設走"
    )
    assert after.profile_name == "deep", "那張組合根本沒套上去，這一條沒有對照組"


def test_a_factory_profile_is_asked_for_by_id_not_by_its_chinese_name(both) -> None:
    """送中文一律擋。

    `AISettingsStore.profile()` 吃代號。收中文的話，英文版上線那天三張表
    一張都對不上——**而畫面看起來完全正常**。
    這個專案的硬規則：不准拿中文當查詢的鍵。
    """
    _native, web, project = both
    store = AISettingsStore(project)
    before = store.load_project().profile_name

    web.handle_action("apply_run_profile", "深入")

    assert store.load_project().profile_name == before, (
        "送中文竟然套用成功了 —— 那條路在英文版上線那天會整個斷掉"
    )


def test_the_handoff_task_lands_somewhere_he_can_see(both) -> None:
    """交接任務要寫進硬碟，不能只留在那個看不見的 widget 上。

    面板不在版面上之後，「只存在 widget 上」＝**只存在一個沒有人看得到的
    地方**：他被告知「已切換成計畫模式」，而陣容頁上那顆任務入口是空的。
    """
    native, web, project = both
    native.set_planning_task("幫我想一份把記憶層接上版本控制的計畫。", planning_rounds=2)

    assert AISettingsStore(project).load_project().workflow_mode == "planning", (
        "序列沒有寫進硬碟 —— 陣容頁那一段還會寫著開發序列"
    )
    task = web.collect_state()["lineup"]["task"]
    assert task["file"] == "planning-request.md", (
        "計畫序列寫的是 planning-request.md，畫面上要看得出來寫的是哪一個"
    )
    assert "記憶層" in task["text"], (
        "交接過來的任務在陣容頁上看不到 —— 他會以為什麼都沒發生"
    )
