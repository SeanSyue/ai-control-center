"""這一輪新建的 `.env`，內容不准跟著提示詞出去。

## 改壞的樣子

`get_git_diff()` 2026-08-25 起會把**未追蹤的新檔案整份讀進來**接在 diff 後
面，而那一段文字會被貼進派給廠商 CLI 的複查與拍板提示詞。

`git ls-files --others --exclude-standard` 只擋得住有寫進 `.gitignore` 的
檔案。**那一輪自己新建的 `.env` 還來不及被誰忽略**——實測：一個內容是
`OPENAI_API_KEY=sk-...` 的新檔案，整行原文出現在 `get_git_diff()` 的回傳
值裡。`.venv/`、`node_modules/` 同理，只是外洩的是幾萬個檔案的內容。

`.aicc/knowledge/SECURITY_MODEL.md`：憑證與執行期狀態**永不同步或匯出**，
而且不准用「先遮蔽再送出」達成。

## 使用者會看到什麼

什麼都看不到——這正是它的貴處。那一輪的畫面、時間軸、四個章，跟一輪沒有
`.env` 的執行**長得一模一樣**；金鑰已經在別人的伺服器上，而沒有任何一個
地方變紅過。

## 但檔名要留著

整條刪掉也不行：那一輪真的產生了那個檔案，而拍板那一格是靠這一段文字判斷
「這一輪做了什麼」。**沒有那個檔案**跟**有那個檔案、內容不給看**要分得出來。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.orchestrator import Orchestrator
from app.i18n import t


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def git(project: Path, *args: str) -> None:
    completed = subprocess.run(
        [
            "git",
            "-c",
            "user.email=aicc@example.com",
            "-c",
            "user.name=aicc",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, completed.stderr


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    write(project / ".aicc" / "task.md", "# 任務\n")
    write(project / "existing.py", "原本就有的一行\n")
    # **刻意沒有 `.gitignore`**：有 `.gitignore` 的專案 `git ls-files
    # --others --exclude-standard` 本來就撈不到 `.env`，那個案例太順——
    # 掉了跟沒掉長得一樣。這一輪自己新建的憑證檔就是這個形狀。
    git(project, "init", "-q")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "first")
    return project


SECRET = "OPENAI_API_KEY=sk-realsecret1234567890abcdef"


def test_a_key_written_this_round_does_not_reach_the_prompt(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    write(project / ".env", f"{SECRET}\n")

    diff = Orchestrator(project).get_git_diff()

    assert SECRET not in diff, "金鑰整行出現在要送給廠商的那一段文字裡"
    assert "sk-realsecret" not in diff


def test_the_seat_that_stamps_still_learns_the_file_was_made(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    write(project / ".env", f"{SECRET}\n")

    diff = Orchestrator(project).get_git_diff()

    # 檔名在、而且講得出為什麼沒有內容。少了這一句，「這一輪沒建過 .env」
    # 跟「建了但不給看」在最後一關長得一樣。
    assert t("git.new_file.secret", path=".env") in diff


def test_a_dependency_folder_is_named_but_not_pasted_in(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    write(
        project / "node_modules" / "left-pad" / "index.js",
        "module.exports = 相依套件的內容;\n",
    )

    diff = Orchestrator(project).get_git_diff()

    assert "相依套件的內容" not in diff
    assert (
        t("git.new_file.runtime", path="node_modules/left-pad/index.js")
        in diff
    )


def test_an_ordinary_new_file_is_still_shown_in_full(tmp_path: Path) -> None:
    """擋過頭的話這一條會紅。

    一條會誤扣內容的規則比沒有更貴：拍板那一格會看到一堆「內容沒有放進來」，
    然後學會忽略那一句——包括真的是憑證的那一次。
    """
    project = make_project(tmp_path)
    write(project / "env_reader.py", "def 讀設定():\n    return 1\n")
    write(project / ".env.example", "OPENAI_API_KEY=請填這裡\n")

    diff = Orchestrator(project).get_git_diff()

    assert "def 讀設定():" in diff
    assert "OPENAI_API_KEY=請填這裡" in diff
