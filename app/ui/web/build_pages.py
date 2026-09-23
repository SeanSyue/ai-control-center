"""Copy the design pages into app/ui/web/ ready to be embedded.

The design folder is the visual authority. This script copies each page here and
makes exactly one mechanical change: it injects

    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>

before the page's own <script>. Every page already calls
`new QWebChannel(qt.webChannelTransport, ...)`, but none of them load the class
- in a browser that is harmless (they fall back to DEMO data because
`qt.webChannelTransport` is undefined), inside QWebEngineView it is a
ReferenceError and the page renders nothing. This is landmine #1 in
V4_100趴還原_QWebEngineView.md §3.

The <style> block of every page is hashed before and after; a mismatch fails the
build, so the "CSS 不要改" rule cannot be broken by accident.

Run from the repo root:
    .\\.venv\\Scripts\\python.exe app\\ui\\web\\build_pages.py
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

# The design pages ship with this repository. Point AICC_DESIGN_DIR at
# another folder to build from a design copy kept outside the repo.
DESIGN = Path(os.environ.get("AICC_DESIGN_DIR") or Path(__file__).resolve().parents[3] / "design-pages")
HERE = Path(__file__).parent

PAGES = [
    "dashboard_v5.html",
    # The 陣容 page. It took conversation_library_v2.html's slot in ROADMAP P10
    # and that page is no longer copied: nothing instantiates a panel naming it,
    # so every build put an unreachable page into app/ui/web and every run of
    # verify_web_pages counted it as one of the passing pages. The design folder
    # still ships it, so a rollback is one line here plus a PAGE constant.
    "lineup_v1.html",
    "knowledge_v1.html",
    "documents_v1.html",
    "settings_v1.html",
    "roadmap_v1.html",
    # The decision record (ROADMAP P8「稽核日誌」). Last in the sidebar behind a
    # divider: the other eight pages are for doing the work, this one is for
    # proving the work was done.
    "audit_v1.html",
    # 登入與安裝（憑證入口）。**不在側邊欄**——它掛成 `MainWindow` 的一個
    # 屬性（2026-08-26 拍板），所以 `page_wiring` 認得它、`orphan_pages()`
    # 不會把它列成孤兒。加檔名而**沒有**面板的話，這一行就是在製造一個
    # 假綠燈：頁面被複製過去、沒有人開得了它，而 verify 把它算成通過的
    # 頁面之一。conversation_library_v2.html 就是這樣過了整個 P10。
    "credentials_v1.html",
    # The shell is a page, not an asset: as an asset it was copied raw and
    # skipped the CSP injection, and it is the one view that is always on
    # screen and shows the project name.
    "shell.html",
]
ASSETS = ["aicc.css", "shell.js"]

CHANNEL_TAG = '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>\n'

# Defence in depth behind escape_state(). The panels are fed conversation
# titles and snippets scanned off the local machine, so a page that renders one
# unescaped runs whatever it contains - measured on all three list pages before
# the Python-side escaping went in.
#
# Injected here rather than written into the design pages so that a page pulled
# fresh from the design folder can never arrive without it: forgetting is not an
# available outcome. `qrc:` is listed because that is where Qt serves
# qwebchannel.js from, and 'unsafe-inline' is unavoidable - every page is one
# file with inline <style> and <script>, which is the whole point of the design
# handoff. What it does buy is `connect-src 'none'`: injected script can no
# longer reach the network, which is the step between "runs in the panel" and
# "sends your conversations somewhere".
CSP_TAG = (
    '<meta http-equiv="Content-Security-Policy" content="'
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' qrc:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'"
    '">\n'
)


def style_digest(text: str) -> str:
    """Hash every <style> block so a CSS edit cannot slip through."""
    blocks = re.findall(r"<style>.*?</style>", text, re.S)
    return hashlib.sha256("".join(blocks).encode("utf-8")).hexdigest()


def build_page(name: str) -> tuple[bool, str]:
    src = DESIGN / name
    if not src.is_file():
        return False, "設計稿不存在"
    text = src.read_text(encoding="utf-8")

    if "qrc:///qtwebchannel/qwebchannel.js" in text:
        note = "已含 qwebchannel.js，未變更"
        composed = text
    else:
        match = re.search(r"[ \t]*<script>", text)
        if not match:
            return False, "找不到 <script> 進入點"
        composed = text[: match.start()] + CHANNEL_TAG + text[match.start():]
        note = "已注入 qwebchannel.js"

    if "Content-Security-Policy" not in composed:
        # Straight after <head>, so it applies to everything the page loads.
        match = re.search(r"<head>", composed, re.I)
        if not match:
            return False, "找不到 <head>，無法注入 CSP"
        composed = (
            composed[: match.end()] + "\n" + CSP_TAG + composed[match.end():]
        )
        note += "、已注入 CSP"

    if style_digest(text) != style_digest(composed):
        return False, "CSS 被更動，中止"

    (HERE / name).write_text(composed, encoding="utf-8")
    return True, note


def main() -> int:
    if not DESIGN.is_dir():
        print(f"找不到設計資料夾：{DESIGN}", file=sys.stderr)
        return 1

    failed = 0
    for name in PAGES:
        ok, note = build_page(name)
        size = (HERE / name).stat().st_size if ok else 0
        flag = "OK " if ok else "!! "
        print(f"{flag}{name:<32}{size:>8,} bytes  {note}")
        failed += 0 if ok else 1

    for name in ASSETS:
        src = DESIGN / name
        if not src.is_file():
            print(f"!! {name:<32}       -  設計稿不存在")
            failed += 1
            continue
        shutil.copy2(src, HERE / name)
        print(f"OK {name:<32}{(HERE / name).stat().st_size:>8,} bytes  原樣複製")

    if failed:
        print(f"\n{failed} 個項目失敗", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
