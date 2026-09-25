"""安裝程式辨認舊安裝，但不把這視為可以刪除整個資料夾的授權。

## 改壞的樣子

2026-09-25 外部審查（A12）：`CurStepChanged(ssInstall)` 只要選定的安裝資料夾
存在，就把整個資料夾改名成 `<資料夾>.previous`，裝完再整棵刪掉；已經存在的
`.previous` 也先整棵刪掉。它從來沒問過那是不是自己的東西——選錯一個資料夾，
裡面的檔案就沒了。

## 這一份量的是什麼，量不到什麼（誠實講）

這裡讀的是 Inno Setup 腳本的**文字結構**：那幾道檢查在不在、有沒有排在
改名與刪除之前。它不是真的跑一次安裝——真的跑會在使用者的帳號裡寫登錄檔、
建捷徑，而這個專案 2026-08-21 就在一次測試裡刪掉過使用者的 Roaming 資料夾。
腳本有沒有寫對語法，由 `ISCC.exe` 編譯那一步回答。
"""
from __future__ import annotations

from pathlib import Path
import re

ISS = Path(__file__).resolve().parents[1] / "packaging" / "AI Control Center.iss"


def _code() -> str:
    text = ISS.read_text(encoding="utf-8-sig")
    return text[text.index("[Code]"):]


def _block(code: str, header: str) -> str:
    """從 `header` 那一行到下一個頂層 `end;`（行首、沒有縮排）。"""
    start = code.index(header)
    end = code.index("\nend;", start)
    return code[start:end]


def _install_branch() -> str:
    body = _block(_code(), "procedure CurStepChanged")
    start = body.index("if CurStep = ssInstall then")
    end = body.index("else if CurStep = ssPostInstall then")
    return body[start:end]


def test_ownership_is_proven_by_the_products_own_executable() -> None:
    # Legacy test name retained for recipe compatibility. This is recognition,
    # not proof that every neighboring file belongs to AICC.
    body = _block(_code(), "function IsAiccInstall")
    body = re.sub(r"\{(?!#).*?\}|\(\*.*?\*\)|//[^\n]*", "", body, flags=re.S)
    body = " ".join(body.split())
    assert body == (
        "function IsAiccInstall(Dir: String): Boolean; begin "
        "Result := FileExists(AddBackslash(Dir) + '{#MyAppExeName}');"
    ), "辨識函式不能被額外的 Result := True 繞過"


def test_a_folder_that_is_not_ours_and_not_empty_is_what_gets_refused() -> None:
    body = _block(_code(), "function NotOursAndNotEmpty")
    assert "not IsAiccInstall(" in body and "not DirIsEmpty(" in body


def test_the_folder_page_refuses_a_foreign_folder() -> None:
    body = _block(_code(), "function NextButtonClick")
    assert "wpSelectDir" in body
    assert "NotOursAndNotEmpty(WizardDirValue())" in body, (
        "選資料夾那一頁不再擋一個有別的東西的資料夾"
    )
    assert "Result := False" in body


def test_a_silent_install_is_stopped_too() -> None:
    """無聲安裝不會出現選資料夾那一頁，所以安裝那一刻要再擋一次。"""
    branch = _install_branch()
    guard = branch.index("if NotOursAndNotEmpty(ExpandConstant('{app}')) then")
    assert "Abort();" in branch[guard:branch.index("if IsAiccInstall(", guard)]


def test_the_old_folder_is_only_renamed_when_it_is_an_aicc_install() -> None:
    branch = _install_branch()
    rename = branch.index("RenameFile(ExpandConstant('{app}'), BackupDir)")
    guard = branch.find("if IsAiccInstall(ExpandConstant('{app}')) then")
    assert 0 <= guard < rename, (
        "安裝資料夾沒有辨認產品執行檔就被改名"
    )


def test_backups_and_partial_installations_are_not_deleted() -> None:
    assert not re.search(r"\b(?:DelTree|DeleteFile|RemoveDir)\s*\(", _code(), re.I)
    script = ISS.read_text(encoding="utf-8-sig")
    assert "[InstallDelete]" not in script
    assert "[UninstallDelete]" not in script


def test_existing_backup_names_are_preserved() -> None:
    body = _block(_code(), "function UnusedSibling")
    assert "while DirExists(Result) or FileExists(Result) do" in body
    assert "Index := Index + 1" in body
    assert "Suffix + '.' + IntToStr(Index)" in body
    assert "BackupDir := UnusedSibling(ExpandConstant('{app}'), '.previous')" in _install_branch()


def test_backup_failure_stops_installation() -> None:
    branch = _install_branch()
    guard = branch.index("if not BackupMade then")
    assert "Abort();" in branch[guard:]
    assert "MB_YESNO" not in branch[guard:]


def test_failed_upgrade_preserves_partial_directory_before_restoring() -> None:
    body = _block(_code(), "procedure DeinitializeSetup")
    preserve = body.index("if not RenameFile(ExpandConstant('{app}'), PartialDir) then")
    restore = body.index("if RenameFile(BackupDir, ExpandConstant('{app}')) then")
    assert preserve < restore
    assert "Exit;" in body[preserve:restore]
    assert "UnusedSibling(ExpandConstant('{app}'), '.incomplete')" in body
