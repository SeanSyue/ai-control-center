r"""叫外部指令的時候，不准彈出黑色主控台視窗。

AICC 是桌面程式，但它做事的方式是去叫命令列工具。在 Windows 上，從 GUI 程式
啟動一個主控台程式，作業系統會**替它開一個黑視窗**——除非明著說不要。

2026-08-22 使用者的原話：「開啟時很慢，會**閃過很多 CMD 視窗**，這不行，
體驗很差。」查出來是五個啟動點裡只有一個記得加那個旗標。

## 為什麼這要是一支測試，而不是一條規矩

漏掉旗標**不會報錯**。程式照跑、結果照樣正確、測試照樣全綠——差別只在使用者
的螢幕上多閃一個黑框。沒有任何既有的檢查看得到那件事。

所以這裡用 `ast` 走過整個 `app/`，把每一個真的會啟動外部程式的呼叫找出來，
要求它明著帶 `creationflags`。用 `ast` 不用字串比對，是因為呼叫常常跨很多行，
而跨行的東西用 regex 抓，抓不到的時候會安靜地放行。

## 故意要開視窗的怎麼辦

在那一行附近寫下 `SHOWS_A_WINDOW_ON_PURPOSE`。目前只有一個地方需要：用檔案
總管把某個檔案框起來給使用者看——那件事的全部意義就是開一個視窗。

刻意要求寫出那個字，而不是預設放行：**放行要留下痕跡**，下一個讀的人才知道
那不是漏掉的。
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

# 這兩個是真的會生出一個新行程的。`subprocess.PIPE`、型別註記、註解裡提到的
# 名字都不算——那正是字串比對會誤判的地方。
LAUNCHERS = {"Popen", "run", "call", "check_call", "check_output"}

MARKER = "SHOWS_A_WINDOW_ON_PURPOSE"


def _launch_calls(tree: ast.AST) -> list[ast.Call]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in LAUNCHERS:
            continue
        # `subprocess.run(...)` —— 只認這個模組，別人身上的 .run() 不算。
        if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            found.append(node)
    return found


def test_every_external_command_is_launched_without_a_console_window() -> None:
    offences: list[str] = []

    for source in sorted(APP.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover - 壞掉的檔案別的測試會抓
            continue
        lines = text.splitlines()

        for call in _launch_calls(tree):
            has_flag = any(
                kw.arg == "creationflags" for kw in call.keywords
            )
            # `**kwargs` 展開的情況：呼叫端可能在字典裡放了旗標，這裡看不到，
            # 所以不當成違規——但它必須自己在附近寫出理由或帶明著的旗標。
            if has_flag:
                continue

            window = "\n".join(
                lines[max(0, call.lineno - 6) : call.lineno + 2]
            )
            if MARKER in window:
                continue

            offences.append(
                f"{source.relative_to(APP.parent)}:{call.lineno} "
                f"subprocess.{call.func.attr}(…) 沒有帶 creationflags"
            )

    assert not offences, (
        "這些地方啟動外部程式時會在 Windows 上閃出一個黑色主控台視窗。\n"
        "加上 `creationflags=NO_WINDOW`（`app/adapters/no_console.py`），\n"
        f"或者在附近寫下 {MARKER} 說明它就是要開視窗：\n  "
        + "\n  ".join(offences)
    )


# 允許開視窗的地方，以及**為什麼非開不可**。
#
# 這裡原本是一個數字（`len(users) <= 1`）。數字擋得住「變多」，擋不住「換人」
# ——把檔案總管那一處的記號刪掉、貼到另一個檔案上，數量一樣是 1，而那個新的
# 黑視窗沒有任何人看過。2026-08-24 加憑證入口的時候順手換掉。
WINDOW_IS_THE_POINT = {
    "app/ui/text_view_dialog.py":
        "「在資料夾中顯示」：開一個檔案總管視窗就是這個功能的全部意義，"
        "加上 NO_WINDOW 等於什麼都沒發生。",
    "app/services/credential_entry.py":
        "「帶我去登入」：OAuth 的網址、瀏覽器、要貼回去的那組碼，"
        "整件事都發生在那個主控台裡。藏起來的話，使用者會看到一顆按鈕"
        "按下去什麼都沒發生，而登入流程正在一個他看不到的地方等他回答。",
}


def test_the_marker_is_only_used_where_a_window_is_the_whole_point() -> None:
    """放行不能變成免死金牌。

    一個字串就能讓守衛閉嘴的話，最省事的修法永遠是貼那個字串。所以**每一個
    用它的檔案都要點名**，而點名旁邊要寫得出理由。
    """
    users = {
        source.relative_to(APP.parent).as_posix()
        for source in sorted(APP.rglob("*.py"))
        if MARKER in source.read_text(encoding="utf-8")
        and source.name != "no_console.py"  # 定義它的地方
    }

    unexpected = users - set(WINDOW_IS_THE_POINT)
    assert not unexpected, (
        f"{MARKER} 出現在沒有人看過的地方：{sorted(unexpected)}\n"
        "要開視窗可以，但請在 WINDOW_IS_THE_POINT 裡寫下為什麼非開不可。"
    )

    gone = set(WINDOW_IS_THE_POINT) - users
    assert not gone, (
        f"這幾個地方不再開視窗了，清單沒有跟著改：{sorted(gone)}\n"
        "留著過期的放行等於替下一個人先開好一扇門。"
    )


def test_the_flag_is_looked_up_not_hard_coded() -> None:
    """`CREATE_NO_WINDOW` 只有 Windows 的 subprocess 有。

    寫死數字（0x08000000）在別的系統上會傳一個沒有意義的旗標進去；直接
    `subprocess.CREATE_NO_WINDOW` 則會在 import 時就炸。用 getattr 取，
    取不到就是 0——「沒有額外旗標」，行為不變。
    """
    source = (APP / "adapters" / "no_console.py").read_text(encoding="utf-8")

    assert 'getattr(subprocess, "CREATE_NO_WINDOW", 0)' in source
    assert "0x08000000" not in source
