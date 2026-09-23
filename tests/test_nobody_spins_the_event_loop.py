"""不准自己寫 `while ...: processEvents()`。

## 這條是被一個當了三次的行程逼出來的

N9（「對話搜尋偶發把程式打掉」）2026-08-24 查出來的根因是一個**看起來
完全正常**的等待迴圈：

```python
while panel._scanning and time.monotonic() < deadline:
    app.processEvents()
```

量到的：

| 寫法 | 當掉率 |
|---|---|
| `while ...: processEvents()`（沒有 sleep）| **8 / 290 ≈ 2.8%** |
| `QEventLoop` ＋ `QTimer` | 1 / 270 |
| `processEvents()` ＋ `sleep(0.01)` | **0 / 280** |

死法是 `abort()`／`Segmentation fault`——**沒有 traceback、沒有 Qt 訊息、
沒有哪一條測試被指名**。在全套裡它看起來就是「跑一跑整個 pytest 消失了」。

## 為什麼 sleep 是那條線

`qt_helpers.wait_for_signal` 的說明裡早就寫過一半：

> `time.sleep()` between `processEvents()` calls does release the GIL,
> letting the worker thread actually run.

不放開 GIL 的話，背景執行緒**拿不到任何 Python 執行時間**，而主執行緒
在那個緊迴圈裡不斷重入 Qt 的事件派送。

## 所以這一條擋的不是「效能不好」

**同一個 repo 裡，一個對的寫法（`wait_for_signal`）跟一個會當機的寫法
並存了很久，而它們看起來一模一樣——只差一行。**

用 `qt_helpers.wait_until()`。
"""
from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

# 定義那兩支正確寫法的地方。它們自己就是 processEvents ＋ sleep。
THE_HELPERS = "qt_helpers.py"


def _calls_named(node: ast.AST, name: str) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called == name:
            return True
    return False


def _spinning_loops() -> list[str]:
    found: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.name == THE_HELPERS or path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.While, ast.For)):
                continue
            body = ast.Module(body=node.body, type_ignores=[])
            if not _calls_named(body, "processEvents"):
                continue
            # 有 sleep 就放行——那正是它不會當機的原因。
            if _calls_named(body, "sleep"):
                continue
            found.append(f"{path.name}:{node.lineno}")
    return found


def test_the_scan_can_see_a_loop() -> None:
    """前提檢查。

    看不到任何 `processEvents` 的話，底下那條是恆真的——而它會在有人
    重新引進那個緊迴圈的時候安靜地放行。
    """
    seen = 0
    for path in TESTS.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        if _calls_named(tree, "processEvents"):
            seen += 1
    assert seen >= 3, f"只有 {seen} 個檔案在叫 processEvents，這條檢查等於空轉"


def test_nobody_spins_the_event_loop_without_letting_go() -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是 N9 的樣子：全套跑一跑，整個 pytest 消失，
    而報告上不會有任何一條測試被指名。
    """
    spinning = _spinning_loops()
    assert not spinning, (
        "這幾處在迴圈裡不放手地叫 processEvents：\n  "
        + "\n  ".join(spinning)
        + "\n\n用 `qt_helpers.wait_until()`（或 `wait_for_signal()`）。"
        "它們中間有 `time.sleep()`，而那一行不是效能調整——"
        "**它是那個迴圈不會讓整個行程當掉的原因**（290 次裡 8 次）。"
    )


def test_the_helper_itself_lets_go() -> None:
    """那兩支共用的自己要放手，不然上面那條只是把問題搬進一個檔案。"""
    source = (TESTS / THE_HELPERS).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    waiters = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in ("wait_for_signal", "wait_until")
    ]
    assert len(waiters) == 2, [n.name for n in waiters]
    for node in waiters:
        assert _calls_named(node, "processEvents"), f"{node.name} 沒有推事件"
        assert _calls_named(node, "sleep"), (
            f"{node.name} 沒有 sleep——**那一行是它不會當機的原因**"
        )
