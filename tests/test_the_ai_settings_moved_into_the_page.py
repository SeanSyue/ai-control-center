"""那條原生設定列不准再釘在版面上吃掉頁面高度。

2026-08-27 老闆打開 App 之後的原話：

    > AI 執行設定那邊一樣吃到頁面，不能按照我剛剛講的去改嗎?

他前一則講的「那樣」是：

    > 把選 claude 和 codex 的地方和 **AI 執行設定的地方**，像是網站一樣和
    > 工作線放在一個頁面，然後滾輪往下就可以往下看到其他設定之類的呢

2026-09-01 那 22 個控制項與任務編輯器搬進陣容頁。**物件留著**（執行路徑
仍然跟它拿設定與任務，而它現在跟著硬碟走），但它不在版面上。

## 為什麼要一條測試守著

「又被加回版面上」在程式碼上是**一行**，而它的症狀是每一頁矮一截——
看起來就像頁面本來就長那樣。他為這件事講過兩次。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLayout, QWidget  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402

BUILT = Path("app/ui/web/lineup_v1.html")


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    ProjectRegistry.initialize_project(project)

    from app.ui.main_window import MainWindow

    made = MainWindow(project)
    yield made
    made.hide()
    made.setParent(None)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def _in_some_layout(widget: QWidget) -> bool:
    """這個 widget 有沒有被某一個版面排進去。

    找的是**它自己**在不在任何一個 `QLayout` 的項目裡——`parent()` 問不出
    這件事：`setParent()` 之後它還是子節點，只是沒有人替它安排位置。
    而「吃不吃掉頁面高度」正好是後面那一件。
    """
    parent = widget.parentWidget()
    while parent is not None:
        for layout in parent.findChildren(QLayout):
            for i in range(layout.count()):
                if layout.itemAt(i).widget() is widget:
                    return True
        parent = parent.parentWidget()
    return False


def test_the_native_settings_bar_is_not_in_the_layout_any_more(window) -> None:
    panel = window.ai_settings_panel
    assert panel is not None, "面板物件被刪掉了 —— 執行路徑還在跟它拿設定與任務"
    assert not _in_some_layout(panel), (
        "AI 執行設定又被排進版面了 —— 它會把每一頁的高度吃掉一截，"
        "而他為這件事講過兩次"
    )
    assert panel.isHidden(), (
        "它沒有在版面上，卻是顯示的 —— 一個沒有父版面的 widget 會變成"
        "一個獨立浮在畫面上的視窗"
    )


def test_the_run_still_has_somewhere_to_read_the_settings_from(window) -> None:
    """搬家不准把執行路徑弄斷。

    面板是**影子**不是廢物：`current_settings()`／`save_task()` 還在用它，
    而它跑之前會 `refresh_from_disk()`。
    """
    panel = window.ai_settings_panel
    assert hasattr(panel, "refresh_from_disk"), (
        "少了從硬碟重讀那一支 —— 網頁改的設定不會生效"
    )
    settings = panel.current_settings()
    assert settings.claude_model, "拿不到設定了"


def test_the_page_actually_draws_that_section(window) -> None:
    """搬過去的那一段真的在頁面產物裡。

    只把原生那一條拿掉、網頁沒接上的話，那 22 個設定**一個入口都沒有**
    ——而畫面上看起來只是變乾淨了。
    """
    html = BUILT.read_text(encoding="utf-8")
    assert 'id="runset"' in html, "陣容頁上沒有 AI 執行設定那一段"
    assert "data-rset" in html, "那一段沒有任何控制項"
    assert "apply_run_profile" in html, "出廠組合那幾張按了不會有事"
    assert "toggle_run_settings" in html, "收合狀態沒有告訴引擎"


def test_the_page_gets_every_control_the_native_panel_has(window) -> None:
    """推過去的控制項數量，要跟那份唯一的宣告對得上。

    少推一格的樣子，跟那一格不存在一模一樣。
    """
    from app.services import run_settings_options as opts

    controls = window.lineup_panel.collect_state()["lineup"]["run_settings"]["controls"]
    keys = {c["key"] for c in controls}
    want = set(opts.CHOICES) | set(opts.SPINS) | set(opts.TOGGLES)
    assert keys == want, (
        f"少推的：{sorted(want - keys)}；多推的：{sorted(keys - want)}"
    )


def test_pressing_run_rereads_what_the_web_wrote(window, monkeypatch) -> None:
    """按下開始執行的**第一件事**，是把設定與任務從硬碟重讀一次。

    ⛔ 這一條是負向驗證逼出來的：`test_the_run_uses_what_he_typed_in_the_web`
    證明了「重讀之後值是對的」，但把 `start_workflow()` 裡那一行**刪掉**
    它照樣全綠——**它驗的是那支方法會動，不是有人叫它**。

    症狀：他在陣容頁改好設定、打好任務，按下開始執行，
    跑起來用的是那個看不見的面板上沒動過的舊值。
    """
    from app.services.ai_settings import AISettingsStore

    project = window.project_path
    store = AISettingsStore(project)
    settings = store.load_project()
    before = settings.claude_model
    settings.claude_model = "opus" if before != "opus" else "haiku"
    wanted = settings.claude_model
    store.save_project(settings)
    (project / ".aicc" / "task.md").write_text("題目在這裡。\n", encoding="utf-8")

    seen: dict[str, object] = {}
    real_save = window.ai_settings_panel.save_task

    def _spy_save(*a, **k):
        # `save_task()` 把面板那個框寫進硬碟。它**當下**手上是什麼，
        # 就是他這一輪的題目——這才是要量的東西。
        seen["task"] = window.ai_settings_panel.task_input.toPlainText().strip()
        return real_save(*a, **k)

    def _capture(*_a, **kw):
        # 真的要開跑的那一刻，設定長什麼樣。
        settings = kw.get("settings")
        seen["model"] = getattr(settings, "claude_model", None)

    monkeypatch.setattr(window.ai_settings_panel, "save_task", _spy_save)
    # 那個「本次可能消耗較多額度」的確認框是 modal，在 offscreen 下會卡死。
    monkeypatch.setattr(type(window), "_is_high_usage", staticmethod(lambda _s: False))
    monkeypatch.setattr(window, "_launch_worker", _capture)

    window.start_workflow()

    assert seen.get("task") == "題目在這裡。", (
        "按下開始執行的時候，那個看不見的面板拿的不是硬碟上的任務 —— "
        f"它拿的是 {seen.get('task')!r}，而他在網頁打的會被這一份蓋掉"
    )
    assert seen.get("model") == wanted, (
        "按下開始執行的時候沒有重讀設定 —— 他在陣容頁改的那一格不會生效"
        f"（跑起來用的是 {seen.get('model')!r}，他選的是 {wanted!r}）"
    )
