"""決策紀錄那一頁：**「讀不到」與「還沒有」都不准長成「全部通過驗證」。**

## 使用者會看到什麼

側邊欄最下面那一頁叫「決策紀錄」，它不是給他自己看的——它是給他**拿去給
別人看**的（稽核、客戶、律師）。頁面最上方一顆燈，三種狀態：紅（有一筆被
改過）、金（有幾筆驗不了）、**薄荷綠加粗體「全部通過驗證」**。

2026-08-24 掃出兩件事，兩件的畫面完全一樣：

* **一筆紀錄都沒有的專案**——紀錄檔要等第一次 Decision Gate 才會被建出來，
  所以這是**預設狀態**，不是邊角。畫面：綠燈、「全部通過驗證」、
  「0 / 0 筆已驗證」。什麼都沒驗過，而它說全部通過。
* **有紀錄但讀不到的專案**（防毒隔離、另一支程式開著、資料夾權限）——
  實測一份 4 筆、鏈完好的紀錄被獨占鎖住時，`verify()` 回的是
  `total=0, verified=0, broken_at=None`、`ok is True`。畫面：**同一顆綠燈**。

第二種更貴：他匯出去的那份檔案是空的，而畫面剛跟他說驗過了。

## 這一份守的四句話

1. 讀不到的紀錄，`verify()` 不准回一個「查過了」的形狀。
2. 讀不到的紀錄，稽核頁不准畫那顆燈。
3. 零筆的專案，稽核頁不准畫那顆燈（頁面另外兩處照樣會說「0 個節點」與
   「這個範圍內沒有紀錄。」，所以空是看得到的——被拿掉的只有沒有根據的
   那個綠勾勾）。
4. 讀不到的時候不准匯出，而且匯出完成的對話框不准對零筆說「通過驗證」。
   匯出是**兩步**（頁面那顆「匯出這份紀錄」送的是 `export_preview`，
   `export_confirm` 要等預覽出來再按一次），兩步都要說話——只釘第二步的話，
   讀不到的時候實際走到的是第一步，而它可以安靜地什麼都不做。

## 這一份**擋不住**什麼（誠實講）

**擋不住「讀不到」在畫面上跟「還沒有」分得出來。**兩種現在都是「那一格
不出現」，而使用者看到的仍然是一頁空的紀錄——「有三筆，但我讀不到」這句話
在畫面上沒有地方可以講。那需要 `AICC-視覺概念/audit_v1.html` 的
`integrityHTML()` 補第四格（rose）再跑 `app/ui/web/build_pages.py`，
而那個檔案不在這一組手上。**寫出來是因為只寫擋得住的部分，下一個人會以為
那一塊已經安全了。**

也擋不住「頁面把 `integrity` 是 `null` 讀成別的意思」——頁面那條
`if(!ig) return ''` 是既有的、刻意的分支，但它沒有測試釘著。
"""
from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.services.decision_summary import summarise_project  # noqa: E402
from app.services.long_task import (  # noqa: E402
    DecisionLogEntry,
    DecisionLogStore,
    DecisionLogUnreadable,
)
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.audit_web_panel import AuditWebPanel  # noqa: E402

_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    ProjectRegistry.initialize_project(project)
    return project


def _log_with(project: Path, count: int) -> DecisionLogStore:
    store = DecisionLogStore(project)
    for index in range(count):
        store.append(
            DecisionLogEntry(
                run_id="run-1",
                action="ANSWERED",
                question=f"要覆寫 demo{index}.py 嗎？",
                answer="不要",
                answered_by="user",
            )
        )
    return store


@contextmanager
def _really_unreadable(path: Path):
    """真的讓那個檔案讀不到，**不是 monkeypatch 掉讀檔那一行**。

    Windows 走 `CreateFileW` 分享模式 0（另一支程式開著它、防毒隔離中、
    備份軟體正在讀，實務上都是這個形狀）；其他平台退回拿掉權限。

    為什麼堅持用真的：這一組修法要證明的正是「產品在**真實**的作業系統
    錯誤下不說謊」。把 `Path.read_text` 換成一個會丟例外的假物件，測到的
    是那個假物件，而 `os.path.isfile` 自己吞掉 OSError 這一半——本來的
    兩處 fail open 之一——假物件根本碰不到。
    """
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        handle = kernel32.CreateFileW(
            str(path), _GENERIC_READ, 0, None, _OPEN_EXISTING, 0, None
        )
        assert handle not in (0, -1, 2**64 - 1), (
            f"鎖不起來（GetLastError={ctypes.get_last_error()}），"
            "這一輪什麼都沒有測到"
        )
        try:
            yield
        finally:
            kernel32.CloseHandle(handle)
    else:  # pragma: no cover - 這個 repo 只在 Windows 上跑
        mode = path.stat().st_mode
        path.chmod(0)
        try:
            yield
        finally:
            path.chmod(mode)


def _panel(project: Path) -> AuditWebPanel:
    QApplication.instance() or QApplication([])
    return AuditWebPanel(project)


def test_the_lock_really_stops_a_read(tmp_path: Path) -> None:
    """前提檢查：鎖不住的話，底下每一條都在測一個沒有發生的錯誤。

    鎖失效的樣子跟修好了一模一樣——檔案讀得到，於是每一條斷言都成立，
    而它們一句話都沒有守到。
    """
    target = tmp_path / "x.jsonl"
    target.write_text("一行\n", encoding="utf-8")

    with _really_unreadable(target):
        with pytest.raises(OSError) as caught:
            target.read_text(encoding="utf-8")

    assert not isinstance(caught.value, FileNotFoundError), (
        "作業系統回的是「找不到檔案」，那是另一種情況（真的還沒有紀錄）"
    )
    assert target.read_text(encoding="utf-8") == "一行\n", "鎖放掉之後要讀得回來"


def test_a_decision_log_that_cannot_be_read_is_not_reported_as_verified(
    tmp_path: Path,
) -> None:
    """讀不到的紀錄不准回一個「查過了」的形狀。

    改壞的樣子：`_raw_entries()` 把 OSError 讀成空清單，於是四筆真紀錄
    在畫面上跟一個從來沒跑過的新專案完全一樣，而那顆燈是綠的。
    """
    project = _project(tmp_path)
    store = _log_with(project, 4)
    assert store.verify().verified == 4, "前提：鎖之前這份紀錄是驗得過的"

    with _really_unreadable(store.path):
        status = store.verify()
        with pytest.raises(DecisionLogUnreadable):
            store.list_entries()

    assert not status.ok, "讀不到卻回 ok——那顆燈就是綠的"
    assert status.unreadable, "沒有留下「為什麼讀不到」，使用者無從判斷能不能自己處理"
    assert str(store.path) in status.unreadable, "訊息要講是哪一個檔案"


def test_a_locked_log_makes_the_project_summary_say_it_could_not_be_read(
    tmp_path: Path,
) -> None:
    """決策摘要要把讀不到的專案列進「沒有列入計算」，不是算成 0。

    `decision_summary` 早就寫好這段保護（`unreadable` → `trustworthy` 為
    False），而它從寫下來那天起就是**死碼**：例外在 `_raw_entries()` 就被
    吃掉了，所以 `except Exception` 永遠等不到。

    改壞的樣子：讀不到的專案安靜地以 0 筆併進總數，而那份摘要是拿來
    回答「這個月有幾個判斷是人做的」的。
    """
    project = _project(tmp_path)
    store = _log_with(project, 3)

    with _really_unreadable(store.path):
        summary = summarise_project("專案", project)

    assert summary.unreadable, "讀不到的專案沒有留下原因"
    assert not summary.trustworthy, "讀不到的數字被當成可以加總的數字"


def test_a_project_with_no_decisions_makes_no_verification_claim(
    tmp_path: Path,
) -> None:
    """一筆都沒有的專案，那一格不准出現。

    改壞的樣子：頁面 `integrityHTML()` 的 `else` 分支把它畫成薄荷綠的
    「全部通過驗證 · 0 / 0 筆已驗證」——而這是每一個還沒觸發過 Decision
    Gate 的專案打開那一頁時看到的第一句話。
    """
    project = _project(tmp_path)
    assert not DecisionLogStore(project).path.exists(), "前提：這個專案還沒有紀錄檔"

    state = _panel(project).collect_state()

    assert state["integrity"] is None, (
        "零筆卻還在說話。頁面只有 bad／part／ok 三格，而 ok 是 else——"
        "推一個 dict 出去就是那顆綠燈"
    )
    assert state["entries"] == []


def test_a_record_that_cannot_be_read_is_not_drawn_as_verified(
    tmp_path: Path,
) -> None:
    """讀不到的時候，稽核頁一樣不准畫那顆燈——**而且不准整個面板炸掉**。

    改壞的樣子有兩種，兩種都出過：畫成綠燈（說謊），或者例外一路衝上
    `push_state()`（面板一片空白，而空白看起來就像「這個專案沒有紀錄」）。
    """
    project = _project(tmp_path)
    store = _log_with(project, 3)
    panel = _panel(project)

    with _really_unreadable(store.path):
        state = panel.collect_state()

    assert state["integrity"] is None, "讀不到卻還在替那份紀錄背書"
    assert state["entries"] == []


def test_the_export_button_says_something_when_the_log_cannot_be_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """他按的那一顆按鈕叫「匯出這份紀錄」，而它**先走預覽**。

    `audit_v1.html` 那顆按鈕送出的是 `export_preview`；`export_confirm` 要等
    預覽視窗出來、人再按一次才會發生。所以紀錄檔讀不到的時候，**實際走到的
    第一條路是這一條**，底下那一條在真實流程裡根本到不了——只釘底下那一條
    等於把有測試的那一半放在使用者碰不到的地方。

    改壞的樣子：預覽這一步安靜地 `return`。畫面上一個字都不會變，按鈕按下去
    像沒按到，而人想得到的下一步是再按一次。沒有任何一句話告訴他那份紀錄
    讀不到，於是他會以為是介面卡住了，不是他的檔案出了事。
    """
    project = _project(tmp_path)
    store = _log_with(project, 3)

    said: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text, *a, **k: said.append((title, text))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(
            lambda parent, title, text, *a, **k: said.append(("information", text))
        ),
    )

    panel = _panel(project)
    with _really_unreadable(store.path):
        panel.handle_action("export_preview", "")
        state = panel.collect_state()

    assert said, "按下「匯出這份紀錄」之後畫面上一個字都沒有變"
    title, text = said[0]
    assert title != "information", "讀不到卻用了「完成」那種語氣"
    assert str(store.path) in text, "沒講是哪一個檔案讀不到，使用者無從處理"
    assert state["preview"] is None, (
        "讀不到卻還是端出一份預覽——那份預覽下面就是那顆可以按的匯出鍵，"
        "而預覽裡是空的"
    )


def test_exporting_a_record_you_cannot_read_refuses_instead_of_writing_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """讀不到就不准匯出，而且要說是哪一個檔案讀不到。

    改壞的樣子：`export()` 拿 `is_file()` 問（`os.path.isfile` 同樣吞掉
    OSError），於是寫出一個**空的** .jsonl 加一份寫著「紀錄筆數：0」的
    markdown——**跟一個乾淨的新專案匯出來的一模一樣**，而那正是他要交給
    別人的那份檔案。
    """
    project = _project(tmp_path)
    store = _log_with(project, 3)
    destination = tmp_path / "out"
    destination.mkdir()

    said: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.ui.audit_web_panel.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(destination),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text, *a, **k: said.append((title, text))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(
            lambda parent, title, text, *a, **k: said.append(("information", text))
        ),
    )

    panel = _panel(project)
    with _really_unreadable(store.path):
        panel.handle_action("export_confirm", "")

    assert not list(destination.iterdir()), "讀不到卻還是寫出了檔案"
    assert said, "什麼都沒說——按下匯出之後畫面上一個字都沒有變"
    title, text = said[0]
    assert title != "information", "讀不到卻用了「完成」那種語氣"
    assert str(store.path) in text, "沒講是哪一個檔案讀不到，使用者無從處理"


def test_the_export_dialog_does_not_tell_an_empty_log_it_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """零筆匯出完成的對話框，不准講「通過完整性驗證」。

    改壞的樣子：對話框印「0 筆，0 筆通過完整性驗證」。那是一句**關於驗證
    的結論**，而根本沒有東西被驗過——他剛把一份空檔案交出去，而視窗跟他說
    驗過了。匯出的 markdown 自己 2026-08-10 就講對了
    （「（這個專案還沒有任何 Decision Gate 紀錄。）」），只有這個出口沒跟上。
    """
    from app.i18n import t

    project = _project(tmp_path)
    destination = tmp_path / "out"
    destination.mkdir()

    said: list[str] = []
    monkeypatch.setattr(
        "app.ui.audit_web_panel.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(destination),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda parent, title, text, *a, **k: said.append(text)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text, *a, **k: said.append(text)),
    )

    _panel(project).handle_action("export_confirm", "")

    assert said, "匯出完成什麼都沒說"
    assert "通過完整性驗證" not in said[0], (
        "零筆卻宣稱通過驗證——沒有東西被驗過，那句話沒有依據"
    )
    assert t("export.no_records") in said[0], (
        "沒有講出「這個專案還沒有任何 Decision Gate 紀錄」，"
        "使用者不知道那份檔案為什麼是空的"
    )
