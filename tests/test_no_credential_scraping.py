"""AICC 不去挖使用者沒有交出來的憑證（`SECURITY_MODEL.md`）。

> Do not scrape OAuth tokens from Claude, ChatGPT, Codex, browsers, or desktop
> app caches.

這是整份安全模型裡最重要的一條——**它是「協作工具」跟「憑證竊取程式」之間那條
線**。而它偏偏也是最容易在善意之下被跨過的一條，因為 `cli_discovery` 為了找
執行檔，本來就會去翻 `~/.codex` 與 `%APPDATA%\\Claude`：

    ~/.codex/codex.cmd        ← 要找的
    ~/.codex/auth.json        ← 就在旁邊

「既然都走到這個資料夾了，順便讀一下 auth.json 就知道有沒有登入」是一句聽起來
很有效率的話，而且**它不會讓任何既有測試變紅**。登入狀態現在是問 CLI 自己
（`codex login status`），那是廠商的介面；讀 `auth.json` 是繞過它。

所以這裡驗兩件事：那個 glob 只撈得到執行檔，以及探索過程完全不讀那些資料夾裡
的任何檔案。
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services import cli_discovery  # noqa: E402

APP = Path("app")

# 兩支 CLI 與瀏覽器實際存憑證的地方。名字出現在原始碼裡就要有人解釋為什麼。
CREDENTIAL_FILES = {
    "auth.json",
    ".credentials.json",
    "credentials.json",
    "cookies.sqlite",
    "Login Data",
    "Cookies",
    "key4.db",
    "logins.json",
}
BROWSER_HINTS = {"User Data", "Default/Network", "Profile 1", "Local State"}

# 可以名正言順提到憑證檔名的兩種地方，理由不一樣：
#
#   security_scan  —— 要**認出**它們才擋得住它們被匯出。認出來跟讀進來是
#                     相反的兩件事。
#   line_notifier  —— AICC **自己的** `line_credentials.json`（名字裡剛好含
#                     「credentials.json」）。使用者親手交出來的憑證，存在
#                     `%APPDATA%`、DPAPI 加密——安全模型明講那正是
#                     「Must Stay Local」的意思。禁止的是另一件事：
#                     去讀使用者**沒有**交給你的那些。
ALLOWED_TO_NAME_THEM = {
    "app/services/security_scan.py",
    "app/services/line_notifier.py",
}


def _fake_bundled_root(tmp_path: Path) -> Path:
    root = tmp_path / ".codex"
    root.mkdir()
    (root / "auth.json").write_text('{"access_token": "secret"}', encoding="utf-8")
    (root / "config.toml").write_text("model = 'gpt-5'\n", encoding="utf-8")
    (root / "history.jsonl").write_text('{"text": "私人對話"}\n', encoding="utf-8")
    (root / "codex.cmd").write_text("@echo off\n", encoding="utf-8")
    # 真實的 `~/.codex` 底下有子資料夾，而其中一個剛好就叫 `codex` 的機率不低
    # （npm 的套件目錄就是這樣命名）。少了它，`is_file()` 那道過濾拿掉也不會
    # 有任何測試變紅——負向驗證就是這樣抓到這個 fixture 太乾淨的。
    (root / "codex").mkdir()
    (root / "sessions").mkdir()
    return root


# --- glob 只撈執行檔 ---------------------------------------------------------


def test_the_glob_only_picks_up_executables(tmp_path: Path) -> None:
    root = _fake_bundled_root(tmp_path)

    found = cli_discovery._executables(root, "codex")

    names = {path.name for path in found}
    assert names == {"codex.cmd"}, names


def test_no_credential_file_can_match_even_by_stem(tmp_path: Path) -> None:
    """把憑證檔取成 CLI 的名字也不行。

    `_executables` 有一個「沒有副檔名」的樣式（npm 會放一個無副檔名的 shell
    script），所以一個叫 `codex` 的檔案是會被撈到的。這條確認那個樣式不會
    連帶把 `codex.json` 之類的東西也吃進來。
    """
    root = tmp_path / ".codex"
    root.mkdir()
    (root / "codex.json").write_text('{"access_token": "x"}', encoding="utf-8")
    (root / "codex.credentials").write_text("x", encoding="utf-8")

    assert cli_discovery._executables(root, "codex") == []


def test_a_folder_named_like_the_cli_is_not_offered_as_one(tmp_path: Path) -> None:
    """資料夾不是執行檔。

    這條要單獨建一個「只有資料夾、沒有執行檔」的情境。上面那個 fixture 蓋不到
    ——那裡 `codex.cmd` 跟 `codex/` 同層，而樣式的偏好順序（`.exe` → `.cmd` →
    `.bat` → 無副檔名）加上「一個資料夾只留一個」的規則，會讓 `.cmd` 先被記下，
    資料夾根本沒機會出線。負向驗證因此判 `is_file()` 那道過濾沒有牙齒，
    而真相是**它只在沒有執行檔可選的時候才作用**。

    沒有它的話，AICC 會把一個資料夾當成候選拿去 `--version`，然後在使用者面前
    說「找到了」——找到一個跑不起來的東西。
    """
    root = tmp_path / ".codex"
    (root / "codex").mkdir(parents=True)

    assert cli_discovery._executables(root, "codex") == []


# --- 探索過程不讀那些檔案 ----------------------------------------------------


def test_discovery_reads_no_file_in_the_bundled_folders(
    tmp_path: Path, monkeypatch
) -> None:
    """整趟探索不可以讀任何一個檔案的內容。

    這條攔的是實際行為而不是字串：任何 `read_text` / `read_bytes` / `open`
    都會炸。所以「順便讀一下 auth.json」不管寫成什麼樣子都過不了。
    """
    root = _fake_bundled_root(tmp_path)
    monkeypatch.setattr(cli_discovery, "_bundled_roots", lambda provider: [root])
    monkeypatch.setattr(cli_discovery, "_npm_global_root", lambda: None)
    monkeypatch.setattr(cli_discovery.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_discovery, "_tool_version", lambda path: "codex-cli 1.0")
    monkeypatch.setattr(
        cli_discovery, "_signed_in", lambda path, provider="": True
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("探索過程讀了檔案內容")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)

    found = cli_discovery.discover("codex")

    assert [path.path.name for path in found] == ["codex.cmd"]


def test_sign_in_state_comes_from_the_cli_not_from_a_file() -> None:
    """登入與否要問 CLI，不是去讀它的憑證檔。

    `codex login status` 是廠商自己的介面；讀 `auth.json` 是繞過它，而且會在
    格式改版的那天安靜地給出錯的答案——**一個宣稱「已登入」而其實沒有的檢查，
    比沒有檢查更糟**，因為它把失敗推遲到執行到一半。
    """
    source = (APP / "services" / "startup_diagnostics.py").read_text(
        encoding="utf-8-sig"
    )
    probes = source.split("probes = {")[1].split("}")[0]

    assert '"auth", "status"' in probes, probes
    assert '"login", "status"' in probes, probes
    assert "auth.json" not in source


# --- 原始碼裡不出現那些路徑 --------------------------------------------------


def _files_mentioning(needles: set[str]) -> dict[str, set[str]]:
    hits: dict[str, set[str]] = {}
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8-sig")
        found = {needle for needle in needles if needle in text}
        if found:
            hits[path.as_posix()] = found
    return hits


def test_only_the_scanner_names_credential_files() -> None:
    """認出來（為了擋下）跟讀進來（為了用）是相反的兩件事。

    掃描器需要知道 `credentials.json` 長什麼樣才擋得住它被匯出，所以它可以
    提。其他地方提到就要有人說明理由。
    """
    hits = _files_mentioning(CREDENTIAL_FILES)

    unexpected = {
        path: names for path, names in hits.items() if path not in ALLOWED_TO_NAME_THEM
    }
    assert not unexpected, f"這些地方提到了憑證檔名：{unexpected}"


def test_no_browser_profile_paths_anywhere() -> None:
    # 瀏覽器的 cookie 與登入資料庫，連提都不該提——AICC 沒有任何理由知道
    # 那些路徑長什麼樣。
    assert _files_mentioning(BROWSER_HINTS) == {}


def test_the_bundled_roots_are_only_used_for_looking_up_executables() -> None:
    """`_bundled_roots()` 的回傳值只能餵給 `_executables()`。

    它回的是兩個放著憑證的資料夾。今天只有一個呼叫端，而**下一個呼叫端就是
    這條規則被跨過的那一刻**——所以呼叫端的數量本身受管制。
    """
    source = (APP / "services" / "cli_discovery.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    callers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "_bundled_roots"
    ]
    assert len(callers) == 2, (
        f"`_bundled_roots()` 有 {len(callers)} 個呼叫端了。"
        "多出來的那個在拿一個放憑證的資料夾做別的事——先讀 SECURITY_MODEL.md。"
    )
