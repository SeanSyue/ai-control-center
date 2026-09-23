r"""The suite must not read or write the settings the user's app actually uses.

`QSettings("Sean", "AI Control Center")` is the installed app's real store - on
Windows, `HKEY_CURRENT_USER\Software\Sean\AI Control Center`. Tests used to
write into it, which cost twice over: it edited the user's app behind their
back, and it made `test_the_mode_is_remembered_between_runs` flip red whenever
a second process touched the same key between its write and its read. A flake
that reads "FAILED" is worse than no test, because it sends the next fix at
something that was never broken.

`tests/conftest.py` redirects the whole process into a throwaway INI file. This
file is what makes that redirect a mechanism instead of a note: it goes red if
the redirect is removed, if it lands too late to catch the module-level THEME,
if a panel pins its own storage format and slips out of the sandbox, or if a
test reaches around Qt for the real store on its own.

Deliberately no `importorskip` here. PySide6 is a pinned requirement of this
project, so this guard has no way to skip itself into silence.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.web_panel import THEME  # noqa: E402

from conftest import (  # noqa: E402
    FORMAT_PINNING_PREFIXES,
    QSETTINGS_SANDBOX,
    REAL_STORE_MARKERS,
)

TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent / "app"


def _is_inside(path: str, directory: Path) -> bool:
    try:
        Path(path).resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def test_the_shared_store_is_a_sandbox_file_not_the_users_real_one() -> None:
    QApplication.instance() or QApplication([])

    settings = QSettings("Sean", "AI Control Center")

    assert settings.format() == QSettings.Format.IniFormat, (
        "QSettings is back on the native store - on Windows that is the real "
        "registry key the installed app reads"
    )
    assert _is_inside(settings.fileName(), QSETTINGS_SANDBOX), (
        f"settings are being written to {settings.fileName()!r}, which is "
        f"outside the sandbox {str(QSETTINGS_SANDBOX)!r}"
    )


def test_the_theme_bus_was_redirected_before_it_was_built() -> None:
    """THEME builds its QSettings at import time, so the sandbox has to be in
    place before the first test module is imported - not in a fixture."""
    QApplication.instance() or QApplication([])

    assert _is_inside(THEME._settings.fileName(), QSETTINGS_SANDBOX), (
        f"THEME kept the real store ({THEME._settings.fileName()!r}); the "
        "redirect in conftest.py ran too late to catch it"
    )


def test_a_setting_written_by_a_test_lands_in_the_sandbox_on_disk() -> None:
    QApplication.instance() or QApplication([])
    settings = QSettings("Sean", "AI Control Center")

    # Check where it points *before* writing. If the sandbox is ever broken,
    # this test must not be the one that plants a stray value in the user's
    # real store while proving that it can.
    written = Path(settings.fileName())
    assert _is_inside(str(written), QSETTINGS_SANDBOX), (
        f"refusing to write: {settings.fileName()!r} is the real store"
    )

    settings.setValue("guard/proof", "written-by-the-test-suite")
    settings.sync()

    assert written.is_file(), "the sandbox store was never actually created"
    assert "written-by-the-test-suite" in written.read_text(encoding="utf-8")


# 合成樣本用的那個名字。**拼起來**，不寫成字面——寫成字面的話這一支
# 自己會被上面那條掃描抓到（它掃 `tests/` 底下每一支，包含自己）。
MARKER_FOR_SAMPLES = "Path" + ".home()"


def _prose_lines(source: str) -> set[int]:
    """**說明文字**佔的每一行：docstring 與 `#` 註解。

    兩者的共同點是**它們執行不了任何東西**——一段在解釋「為什麼不要那樣
    寫」的話，不會繞過沙箱。

    **一般字串不算**：`marker = "winreg"` 那種寫法照樣繞得過去，所以它
    要繼續紅。

    ## 為什麼放掉它們

    2026-08-25 這一支對兩段說明文字誤報：一段 docstring 寫著「第一版寫的
    是 ⋯ 那個家目錄解析器」，一段 `#` 註解在解釋這個放行本身。
    而這一支自己的說明早就寫著：

    > Failing it would teach the next person that this guard cries wolf,
    > **which is how a guard stops being read.**

    解析不了的檔案回空集合——**往嚴的那一邊倒**。
    """
    import ast
    import io
    import tokenize

    out: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                out.add(token.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        out.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return out


def test_no_test_reaches_around_the_sandbox_for_the_real_store() -> None:
    """The sandbox only covers code that goes through QSettings' default
    format. A test that opens the registry itself, or asks for the native
    format by name, walks straight past it - so name those ways and forbid
    them."""
    offences: list[str] = []
    for source in sorted(TESTS_DIR.glob("*.py")):
        if source.name == "conftest.py":
            continue  # the one file whose job is to set the sandbox up
        text = source.read_text(encoding="utf-8")
        for line_number, marker, why in _offences_in(text):
            offences.append(f"{source.name}:{line_number} uses {marker!r} - {why}")

    assert not offences, (
        "these tests reach past the QSettings sandbox and can touch the real "
        "user store:\n" + "\n".join(offences)
    )


def test_no_panel_pins_its_own_settings_format() -> None:
    """The sandbox works by changing QSettings' *default* format. Any call that
    names a format or a scope explicitly ignores that default and goes to the
    real store even under pytest, so app code must keep using the plain
    `QSettings(organisation, application)` form."""
    offences: list[str] = []
    for source in sorted(APP_DIR.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for prefix in FORMAT_PINNING_PREFIXES:
                if prefix in line:
                    offences.append(
                        f"{source.relative_to(APP_DIR.parent)}:{line_number}: "
                        f"{line.strip()}"
                    )

    assert not offences, (
        "these QSettings calls pick their storage explicitly, so the test "
        "sandbox cannot redirect them:\n" + "\n".join(offences)
    )


# 那條掃描的**合成樣本**。
#
# 為什麼要有它：整個 `tests/` 現在是乾淨的，所以「把放行改成全部放行」
# 這種破壞**不會讓任何東西變紅**——不是因為守衛好，是因為沒有東西可抓。
# 2026-08-25 的破壞驗證當場證實：R1／R2 兩條都是綠的。
#
# 一個合成樣本讓那條掃描每一次都真的判斷一次。
_SAMPLE_WITH_PROSE_ONLY = '''"""這一段在解釋為什麼不要用 {m}。"""
# 註解裡也提到 {m}，同樣只是說明。
def fine():
    return 1
'''.replace("{m}", "PLACEHOLDER")

_SAMPLE_WITH_REAL_CODE = '''def not_fine():
    return PLACEHOLDER
'''


def _offences_in(source: str) -> list[tuple[int, str, str]]:
    """一段原始碼裡，哪幾行真的繞過了沙箱。

    **這是唯一的一份判斷**：上面那條掃描真實檔案的測試呼叫它，
    底下兩條餵合成樣本的測試也呼叫它。

    第一版不是這樣——底下那兩條自己重寫了一遍同樣的迴圈，於是破壞驗證
    R1「把放行改成全部放行」照樣是綠的：它改的是真的那一份，
    而測試量的是自己那一份。**兩份會漂開，一份不會。**
    （這個病 2026-08-24 在 `tools/zero_callers.py` 的測試上記過一次，
    而我在寫這一支的 docstring 時抄了那句話，然後還是犯了。）
    """
    prose = _prose_lines(source)
    out: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        # 說明文字（docstring 與註解）裡提到那個名字，不算——
        # 理由寫在 `_prose_lines` 上面。
        if line_number in prose:
            continue
        for marker, why in REAL_STORE_MARKERS:
            if marker in line:
                out.append((line_number, marker, why))
    return out


def test_prose_that_mentions_a_marker_is_not_an_offence() -> None:
    """docstring 與註解裡提到那個名字，**不算**。

    改壞的樣子：2026-08-25 之前這條掃描是逐行字串比對，於是一段在解釋
    「為什麼不要那樣寫」的 docstring 被判成違規。
    **誤報會教下一個人把這條檢查當成狼來了。**
    """
    sample = _SAMPLE_WITH_PROSE_ONLY.replace("PLACEHOLDER", MARKER_FOR_SAMPLES)
    assert _offences_in(sample) == [], _offences_in(sample)


def test_real_code_that_uses_a_marker_is_still_an_offence() -> None:
    """**放行不准變成全部放行。**

    改壞的樣子：把那個放行寫成 `if True: continue`——整條掃描就變成
    一個永遠回空清單的東西，而它看起來跟一條乾淨的掃描一模一樣。
    """
    sample = _SAMPLE_WITH_REAL_CODE.replace("PLACEHOLDER", MARKER_FOR_SAMPLES)
    assert _offences_in(sample), (
        f"一行真的用了 {MARKER_FOR_SAMPLES} 的程式碼沒有被抓到"
    )
