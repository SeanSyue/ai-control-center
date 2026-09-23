"""AICC 自己會執行哪些指令（`SECURITY_MODEL.md`）。

> Do not auto-run destructive commands, package installs, schema migrations,
> Git reset/rebase/push, system setting changes, or external service changes.

`gate_veto()` 守的是**模型交給模型**的那條路（Codex 說可以做，Claude 要去做）。
它守不到的是另一條：**AICC 自己寫在原始碼裡、不問任何人就會跑的指令。**
那一條在這之前沒有任何東西看著。

會壞掉的方式很具體，而且聽起來都很合理：

* 「執行前先 `git stash` 一下比較安全」
* 「跑完自動 commit，免得使用者忘記」
* 「偵測到缺套件就順手 `npm install -g`」

每一個都會讓使用者的 repo 在沒有人按過同意的情況下被改，而**現有的測試一條
都不會紅**——它們驗的是工作流程的結果，不是工作流程動了什麼手。

所以這個檔案不跑任何指令，它讀原始碼：把 `app/` 底下每一個會被送去執行的
argv 列表抓出來，逐一比對允許清單。
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path("app")

# 只讀，不寫。要加東西進來的人請先讀上面那段安全模型。
ALLOWED_GIT = {"diff", "status", "rev-parse", "log", "show", "ls-files"}

# 這兩個是刻意的例外，各自有很窄的理由：
#   taskkill  —— 「停止」按鈕，砍的是 AICC 自己啟動的那棵行程樹（帶 PID），
#                不是系統設定，也不是別人的行程。
#   explorer  —— 「在資料夾中顯示」，開一個檔案總管視窗，不改任何東西。
ALLOWED_FIRST_WORDS = {
    "git", "claude", "codex", "npm", "pytest", "taskkill", "explorer",
}

# 指令名稱本身就足以判定的那些。出現在 argv 裡就是違規，不用看子指令。
NEVER = {"pip", "rm", "del", "rmdir", "format", "reg", "netsh", "shutdown"}


def _argv_literals() -> list[tuple[Path, int, list[str]]]:
    """每一個「看起來會被拿去執行」的字串列表。

    抓的是列表字面值而不是呼叫點：同一個 argv 可能先組好再傳，而**繞過這條
    檢查最簡單的方法就是多繞一層變數**。從字面值下手，繞過它得先把指令拆成
    不是字面值的東西——那在 review 裡看得出來。
    """
    found: list[tuple[Path, int, list[str]]] = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)):
                continue
            parts = [
                item.value
                for item in node.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
            if not parts or len(parts) != len(node.elts):
                continue
            if parts[0] in ALLOWED_FIRST_WORDS or parts[0] in NEVER:
                found.append((path, node.lineno, parts))
    return found


LAUNCHERS = {"run", "Popen"}

# 會動態組指令的地方，逐一列出來。每一個都有理由：
#   cli_runner          —— 通用執行器，argv 由呼叫端給（那些呼叫端在下面被檢查）
#   startup_diagnostics —— Windows 上要包成 shell 字串才跑得動 .cmd shim
#   session_scanner     —— codex app-server 的參數依設定而變
#   orchestrator        —— claude／codex 的 prompt 與旗標依設定而變
#
# **這份清單的用途不是描述現況，是讓「多一個動態組指令的地方」必須先改測試。**
# 上面那個字面值掃描擋不住 `[GIT, "status"]` 這種寫法——負向驗證證實過——
# 所以真正的防線是這裡：新增一個組指令的地方，就得有人回頭讀一次安全模型。
#   text_view_dialog    —— 「在資料夾中顯示」：`explorer /select,<路徑>`。
#                          路徑是變數，所以字面值掃描看不到它——**這一條是這個
#                          檢查第一次跑就抓出來的**，剛好證明了它要防的是什麼。
#   credential_entry    —— 「帶我去登入」：argv 的字面值就在那個檔案裡
#                          （`("claude", "setup-token")`），變數只有 `shutil.which`
#                          解出來的**絕對路徑**。使用者輸入一個字都進不去——
#                          那個模組刻意沒有任何一個函式收得下憑證或指令參數，
#                          `tests/test_the_credential_door_cannot_take_a_key.py` 看著。
DYNAMIC_COMMAND_SITES = {
    "app/services/credential_entry.py",
    "app/adapters/cli_runner.py",
    "app/services/startup_diagnostics.py",
    "app/services/session_scanner.py",
    "app/core/orchestrator.py",
    "app/ui/text_view_dialog.py",
}


def _dynamic_launch_sites() -> set[str]:
    """把指令交給 subprocess／runner，而 argv 不是純字面值的地方。"""
    sites: set[str] = set()
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in LAUNCHERS:
                continue
            first = node.args[0]
            if isinstance(first, (ast.List, ast.Tuple)) and all(
                isinstance(item, ast.Constant) for item in first.elts
            ):
                continue          # 純字面值，上面那條已經看過了
            sites.add(path.as_posix())
    return sites


def test_no_new_place_builds_a_command_out_of_variables() -> None:
    """字面值掃描的補洞。

    `["git", "status"]` 會被上面抓到，`[GIT, "status"]` 不會——而那正是繞過
    它最省事的寫法。所以動態組指令的**地點**本身受管制：清單以外的地方出現
    一個，這條就紅。
    """
    unexpected = _dynamic_launch_sites() - DYNAMIC_COMMAND_SITES

    assert not unexpected, (
        f"這些地方開始用變數組指令，安全模型要求先有人看過：{sorted(unexpected)}"
    )


def test_the_scan_finds_the_commands_we_know_about() -> None:
    """先確認這條檢查真的看得到東西。

    一個什麼都沒抓到的掃描，會讓底下每一條斷言都變成恆真——而它看起來
    跟「全部通過」一模一樣。
    """
    argvs = _argv_literals()

    first_words = {parts[0] for _, _, parts in argvs}
    assert "git" in first_words, f"連 git 都沒抓到，掃描壞了：{argvs}"
    assert len(argvs) >= 3, argvs


def test_every_git_command_is_read_only() -> None:
    for path, line, parts in _argv_literals():
        if parts[0] != "git":
            continue
        subcommand = parts[1] if len(parts) > 1 else ""
        assert subcommand in ALLOWED_GIT, (
            f"{path}:{line} 執行了 git {subcommand}——"
            f"AICC 不自動改使用者的 repo（只允許 {sorted(ALLOWED_GIT)}）"
        )


def test_no_git_command_writes_even_with_a_flag() -> None:
    """`git diff` 是唯讀的，`git diff --output=x` 不是。

    子指令白名單擋不住用旗標改行為的寫法，所以危險旗標另外看一次。
    """
    dangerous = {"--hard", "--force", "-f", "--output", "--write-tree", "--amend"}
    for path, line, parts in _argv_literals():
        if parts[0] != "git":
            continue
        offending = dangerous & set(parts)
        assert not offending, f"{path}:{line} 帶了 {offending}"


def test_nothing_installs_packages() -> None:
    """「偵測到缺套件就順手裝一下」是安全模型點名禁止的。

    AICC 只**告訴**使用者安裝指令（`install_command()` 回的是一個字串），
    從來不自己執行。這條守住那個界線。
    """
    for path, line, parts in _argv_literals():
        assert parts[0] not in NEVER, f"{path}:{line} 出現 {parts[0]}"
        if parts[0] == "npm":
            assert "install" not in parts, (
                f"{path}:{line} 會自己跑 npm install——安裝要由使用者決定"
            )


def test_the_two_exceptions_stay_narrow() -> None:
    """`taskkill` 與 `explorer` 是刻意放行的，但理由很窄。

    `taskkill` 只能砍 AICC 自己啟動的那棵行程樹（所以一定帶 `/PID`）。
    少了那個限制，一個 `taskkill /IM` 就會去砍使用者其他同名的程式。
    """
    for path, line, parts in _argv_literals():
        if parts[0] == "taskkill":
            assert "/PID" in parts, f"{path}:{line} 的 taskkill 沒有指定 PID"
            assert "/IM" not in parts, f"{path}:{line} 用檔名砍行程"
        if parts[0] == "explorer":
            assert not any(part.startswith("/n,") for part in parts), parts


def test_a_variable_command_inside_an_allowed_file_is_NOT_caught() -> None:
    """這條檢查抓不到什麼——照 `test_truncating_the_end_is_NOT_detected` 的先例
    寫出來，免得有人日後把它的宣稱放大。

    `["git", "stash"]` 會紅（上面三條驗過），但 `[GIT, "stash"]` 寫在
    `orchestrator.py` 裡不會——那個檔案本來就在動態組指令的清單上，而**原始碼
    層級的檢查看不出一個變數在執行時會是什麼**。負向驗證確認過這個洞是真的。

    能證明 / 不能證明：

    | 能 | 不能 |
    |---|---|
    | 有人直接寫下一個破壞性指令 | 有人刻意把它藏在變數後面 |
    | 有人在新的檔案裡開始組指令 | 已經在清單上的檔案裡做同樣的事 |

    要補上它需要執行期的攔截（在 `CliRunner.run` 裡檢查 argv），那是另一件事
    ——而且真正的威脅不是有人為了躲測試而混淆，是有人覺得「先 stash 一下比較
    安全」而順手寫下去。那一種抓得到。
    """
    source = (APP / "core" / "orchestrator.py").read_text(encoding="utf-8-sig")

    assert '"git",' in source, "前提變了，這條的說明要跟著改"
    # 這裡不斷言「抓不到」——那會變成一條在保護漏洞的測試。它只是把界線寫下來。


def test_the_allowlist_itself_stays_small() -> None:
    # 這條不是在測程式，是在讓「加一個新指令」這件事必須先改測試——
    # 也就是必須有人回頭讀一次上面那段安全模型。
    assert ALLOWED_FIRST_WORDS == {
        "git", "claude", "codex", "npm", "pytest", "taskkill", "explorer",
    }
