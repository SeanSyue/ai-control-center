"""找東西要問索引，而且索引要自己講出它可能是舊的。

老闆 2026-08-22 的原話：

> 現在要翻記憶或找檔案　**很多時候靠猜的或靠寫死的程式**

諷刺的是 AICC 早就有全文索引，引擎每次派工都用它挑文件——**但只有引擎跟畫面
用得到**。在終端機裡工作的人（或 AI）手上只有 `grep` 跟記憶裡的檔名，而
`grep` 找得到「這個字串在哪一行」，找不到「這件事寫在哪」。

`tools/find.py` 把既有的 `search()` 接到命令列上。這一份守的是它**唯一會騙人
的兩個地方**：

1. **索引是舊的，而舊索引給的答案讀起來跟新鮮的一模一樣。**
2. **「沒比中任何東西所以撈了最近更新的」讀起來跟「比中了」一模一樣**——那正是
   `reason == "recent"` 存在的理由，而它必須被印出來。

第三件是相反方向的：**完全找不到的時候要拆開再找一次**。中文沒有空白，整句
常常是一個比不到任何東西的長 token，而「找不到」會被讀成「這件事沒有寫下來」
——然後有人去重寫一份已經存在的文件。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIND = ROOT / "tools" / "find.py"


def run_find(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(FIND), "--project", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
    )


@pytest.fixture()
def indexed(tmp_path: Path) -> Path:
    """一個已經建好索引的小專案。"""
    from app.services.project_knowledge import ProjectKnowledgeStore

    project = tmp_path / "proj"
    (project / ".aicc").mkdir(parents=True)
    (project / "決定.md").write_text(
        "# 決定\n\n章要畫在執行時間軸那一頁，不是陣容頁。\n",
        encoding="utf-8",
    )
    (project / "別的.md").write_text(
        "# 別的\n\n這一份跟上面那件事沒有關係，講的是備份路徑。\n",
        encoding="utf-8",
    )
    ProjectKnowledgeStore(project).rebuild_project_index()
    return project


def test_it_finds_the_document_that_answers_the_question(
    indexed: Path,
) -> None:
    """問一件事，找得到寫著那件事的檔案。

    這是這支工具唯一的用途——找不到就沒有理由存在。
    """
    done = run_find(indexed, "執行時間軸")

    assert done.returncode == 0, done.stderr
    assert "決定.md" in done.stdout
    assert "別的.md" not in done.stdout, (
        "無關的檔案也被撈出來了——那等於回到 grep"
    )


def test_a_stale_index_says_so(indexed: Path) -> None:
    """索引沒跟上檔案的時候，**它要自己講**。

    這是這支工具唯一會騙人的地方：一個過期的索引給出的答案，讀起來跟新鮮的
    一模一樣。使用者不會知道自己拿到的是三天前的專案。
    """
    (indexed / "決定.md").write_text(
        "# 決定\n\n改過了：章要畫在別的地方。\n", encoding="utf-8"
    )

    done = run_find(indexed, "執行時間軸")

    assert "已經被改過但索引還是舊的" in done.stdout, (
        "檔案改了、索引沒跟上，而它一個字都沒說：\n" + done.stdout
    )
    assert "--reindex" in done.stdout, "說了問題卻沒說怎麼修"


def test_a_deleted_file_is_reported_too(indexed: Path) -> None:
    """原始檔不在了也要講。

    索引裡那一段還在，所以它照樣會被撈出來——而使用者照著那個路徑去開檔案，
    會發現檔案不存在，然後懷疑的是自己記錯了。
    """
    (indexed / "別的.md").unlink()

    done = run_find(indexed, "備份路徑")

    assert "原始檔已經不在了" in done.stdout, done.stdout


def test_nothing_matched_is_not_reported_as_a_match(indexed: Path) -> None:
    """撈不到任何東西的時候，回的是「最近更新的」——那件事要寫出來。

    `reason == "recent"` 的意思是**沒有為什麼**。不印出來的話，它讀起來跟
    一個好的比中一模一樣，而使用者會照著一段跟他的問題毫無關係的文字做事。
    """
    from app.services.project_knowledge import ProjectKnowledgeStore

    store = ProjectKnowledgeStore(indexed)
    hits = store.search("")

    assert hits, "空查詢一段都撈不到，這條會恆真"
    assert all(hit.reason == "recent" for hit in hits)

    source = FIND.read_text(encoding="utf-8")
    assert '"recent": "**沒有為什麼**' in source, (
        "`recent` 沒有被翻成人話。它是三種理由裡唯一需要被讀懂的那一個"
    )


def test_a_query_that_finds_nothing_is_widened_before_giving_up(
    indexed: Path,
) -> None:
    """整句比不到就拆開再比一次，而且要講它拆過。

    中文沒有空白，一整句對分詞器常常是一個比不到任何東西的長 token。直接
    回「找不到」的話，那句話會被讀成「這件事沒有寫下來」——然後有人去重寫
    一份已經存在的文件。

    這裡的取捨跟 `search()` 內部那條**相反**，而且是刻意的：那裡怕的是
    「部分成功看起來像完全成功」，這裡怕的是「完全失敗看起來像不存在」。
    """
    done = run_find(indexed, "章", "要", "畫", "在", "哪一頁")

    assert done.returncode == 0, done.stderr
    assert "拆成單詞再找一次" in done.stdout, (
        "整句比不到就直接放棄了：\n" + done.stdout
    )
    assert "決定.md" in done.stdout, "拆開之後還是沒找到那份檔案"


def test_it_can_take_in_a_folder_that_is_not_the_project(
    indexed: Path, tmp_path: Path
) -> None:
    """隔壁資料夾也要收得進來。

    今天要找的答案有一半在 `AICC-視覺概念/`——那是設計原始檔的家，不在這個
    專案的資料夾底下。收不進來的話，這支工具對「往返文件裡講過什麼」完全
    無能為力，而那正是最常需要回頭查的東西。
    """
    outside = tmp_path / "隔壁"
    outside.mkdir()
    (outside / "往返.md").write_text(
        "# 往返\n\n這一份在專案資料夾外面，講的是側邊欄的順序。\n",
        encoding="utf-8",
    )

    added = run_find(indexed, "--add", str(outside), "側邊欄的順序")

    assert added.returncode == 0, added.stderr
    assert "往返.md" in added.stdout, added.stdout


def test_it_does_not_die_when_it_has_bad_news(indexed: Path) -> None:
    """它要印得出符號而不崩潰。

    2026-08-22 的實例：設計端那支頁面驗證死在印失敗符號那一行，於是十頁只
    驗了兩頁——而且**只在有東西要報的時候才炸**，全綠的時候看起來完全正常。
    這一支從第一天就帶著同一段修法。
    """
    source = FIND.read_text(encoding="utf-8")
    # 前綴比對：這個專案的工具帶著 `errors="replace"`（印不出來的字變 `?`，
    # 而不是整支死掉），多了那個參數不代表沒做。
    assert 'reconfigure(encoding="utf-8"' in source
    assert "sys.stderr" in source, (
        "只設了 stdout。traceback 走 stderr，會在印出來的路上再炸一次"
    )
