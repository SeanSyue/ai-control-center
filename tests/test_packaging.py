"""Guards for the two packaging files, which no other test touches.

Both fail quietly rather than loudly, which is why they need covering here:

* An unknown key in the installer's `[Messages]` section **compiles**. Inno
  Setup accepts it silently and simply never uses it, so a typo'd key ships as
  an English string in an otherwise Chinese wizard, and nothing reports it.
  Verified by compiling a script containing `ThisKeyDoesNotExistAtAll=x`:
  "Successful compile".

* A file missing from the spec's `datas` produces a working build with a blank
  panel. That has happened once already, which is why the HTML pages are listed
  there at all.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
ISS = PACKAGING / "AI Control Center.iss"
SPEC = PACKAGING / "AI Control Center.spec"
DEFAULT_ISL = Path(r"C:\Program Files (x86)\Inno Setup 6\Default.isl")


def _keys(text: str) -> list[str]:
    return re.findall(r"^([A-Za-z][A-Za-z0-9]*)=", text, re.M)


def _read_isl(path: Path) -> str:
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8", "replace")


@pytest.mark.skipif(not DEFAULT_ISL.is_file(), reason="這台機器沒有裝 Inno Setup 6")
def test_every_translated_message_key_really_exists() -> None:
    known = set(_keys(_read_isl(DEFAULT_ISL)))
    block = ISS.read_text(encoding="utf-8").split("[Messages]", 1)[1]
    block = block.split("[Files]", 1)[0]

    used = _keys(block)

    assert used, "[Messages] 區段是空的 —— 解析方式可能失效了"
    assert not [k for k in used if k not in known], (
        f"這些 key 在 Default.isl 裡不存在，安裝精靈會顯示英文原文："
        f"{[k for k in used if k not in known]}"
    )


def test_the_wizard_pages_the_user_reads_are_translated() -> None:
    # Not every one of Inno's 296 messages needs translating, but the pages
    # someone actually walks through do. Without this, "translated the
    # installer" could mean two buttons.
    block = ISS.read_text(encoding="utf-8")

    for key in (
        "WelcomeLabel1", "SelectDirDesc", "SelectTasksDesc",
        "ReadyLabel1", "InstallingLabel", "FinishedHeadingLabel",
        "ConfirmUninstall", "ButtonNext", "ButtonInstall", "ButtonFinish",
    ):
        assert re.search(rf"^{key}=", block, re.M), f"{key} 沒有中文版"


def test_the_installer_can_roll_back_a_failed_update() -> None:
    text = ISS.read_text(encoding="utf-8")

    # Rename-aside, not copy-aside: an upgrade should cost a directory rename,
    # not a 150 MB copy.
    assert "RenameFile" in text
    assert "DeinitializeSetup" in text
    # The restore has to be conditional on the install NOT having completed;
    # without that check a successful upgrade would be rolled back too.
    assert "InstallCompleted" in text


def test_the_installer_compares_versions_before_overwriting() -> None:
    text = ISS.read_text(encoding="utf-8")

    assert "InitializeSetup" in text
    assert "DisplayVersion" in text
    # A downgrade is nearly always a mistake, so it must ask rather than
    # proceed.
    assert "降級" in text


def test_the_icon_is_wired_into_both_packaging_files() -> None:
    # Three separate places need it and each covers a different surface: the
    # exe's icon (Explorer, taskbar), the installer's own icon, and the copy
    # QApplication.setWindowIcon reads at runtime for every window.
    spec = SPEC.read_text(encoding="utf-8")
    iss = ISS.read_text(encoding="utf-8")

    assert "icon='aicc.ico'" in spec
    assert "('aicc.ico', '.')" in spec
    assert "SetupIconFile=aicc.ico" in iss
    assert (PACKAGING / "aicc.ico").is_file(), "執行 tools/make_icon.py 產生圖示"
    assert (PACKAGING / "icon-source.png").is_file(), "aicc.ico 的來源圖不見了"


@pytest.mark.skipif(
    not (PACKAGING / "aicc.ico").is_file(), reason="還沒產生 aicc.ico"
)
def test_the_icon_carries_every_size_windows_asks_for() -> None:
    # Windows picks the nearest available size. With only the large frames
    # present it downscales 256px itself, and the mark turns to mush in the
    # taskbar - which is the size people actually look at.
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    icon = QIcon(str(PACKAGING / "aicc.ico"))

    sizes = sorted(size.width() for size in icon.availableSizes())
    assert sizes == [16, 24, 32, 48, 64, 128, 256], f"尺寸不齊：{sizes}"

    # The artwork is drawn on an opaque black backdrop; make_icon.py cuts the
    # rounded corners out. Without that step the tile is a black square on a
    # light taskbar, and nothing else in the build would notice.
    corner = icon.pixmap(256, 256).toImage().pixelColor(0, 0)
    assert corner.alpha() == 0, f"圓角沒有透明（alpha={corner.alpha()}）"


def test_the_web_pages_are_still_listed_as_data() -> None:
    # Leaving these out builds cleanly and produces blank panels - it has
    # happened once.
    spec = SPEC.read_text(encoding="utf-8")

    assert "app/ui/web" in spec
    assert "*.html" in spec and "*.css" in spec
