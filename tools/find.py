r"""在這個專案裡找東西——**問索引，不要用猜的。**

## 為什麼要有這一支

老闆 2026-08-22 的原話：

> 現在要翻記憶或找檔案　**很多時候靠猜的或靠寫死的程式**

這是真的，而且諷刺的地方在於：**AICC 早就有一個全文索引**
（`.aicc/knowledge/index.sqlite`，`chunks_fts`），引擎每次派工都用它挑要送
哪幾份文件——**但只有引擎跟畫面用得到它**。人（或者 Claude Code、Codex 這種
在終端機裡工作的 AI）要找東西的時候，手上只有 `grep` 跟記憶裡的檔名。

`grep` 找得到「這個字串在哪一行」，找不到「這件事寫在哪」。中文尤其嚴重：
問「駁回怎麼分發」，`grep 駁回` 會撈出一百行註解，而真正回答那個問題的段落
可能一次都沒有出現那兩個字。

所以這一支把既有的 `ProjectKnowledgeStore.search()` 接到命令列上。它已經處理
好一件別人會做錯的事：FTS5 的預設分詞把一整串中文當成一個 token，所以
「開戶討論」比不到「開戶討論紀錄」——那份實作因此**同時**跑 FTS 與 LIKE，
不是「FTS 找不到才退回 LIKE」。實測過：三段含「開戶討論」，LIKE 三段都找得到，
只跑 FTS 的版本回兩段。**部分成功比完全失敗更糟**，因為它看起來像成功。

## 怎麼用

    .venv\Scripts\python.exe tools\find.py "驗收標準怎麼被駁回"
    .venv\Scripts\python.exe tools\find.py 章 --limit 5 --full

每一筆會印出**它為什麼被挑到**（`keyword` / `substring` / `recent`）。
`recent` 是最重要的那一個：它的意思是「**沒有為什麼**」——查詢字串裡沒有可用
的詞，所以撈的是最近更新的東西，跟你問的可能毫無關係。那一行不印出來的話，
它讀起來會跟一個好的比中一模一樣。

## 索引不是自己會長的

索引是 `rebuild_project_index()` 建的，跑過一次之後不會自己跟上檔案改動。
所以這裡會印出索引是什麼時候建的、以及有幾份文件已經過期——**過期的索引
給出的答案讀起來跟新鮮的一模一樣**，這是這支工具唯一會騙人的地方。

要更新：

    .venv\Scripts\python.exe tools\find.py --reindex
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    # Windows 主控台預設 cp950，印不出中文以外的符號會整支崩潰——而且**只在
    # 有東西要報的時候**才炸。同一個 bug 讓設計端那支頁面驗證十頁只驗了兩頁。
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.services.project_knowledge import (  # noqa: E402
    KnowledgeChunk,
    ProjectKnowledgeStore,
)

# 為什麼被挑到，翻成一句人話。`recent` 那一句刻意講得很難聽——它就是難聽。
WHY = {
    "keyword": "關鍵字比中",
    "substring": "字串比中（中文分詞比不到，用包含比的）",
    "recent": "**沒有為什麼**——查詢裡沒有可用的詞，這是最近更新的東西",
}


def _one_line(text: str, width: int = 100) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _relative(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except Exception:
        return path


def show(hits: list[KnowledgeChunk], full: bool) -> None:
    if not hits:
        print("索引裡找不到相關的段落。")
        print("　　索引可能是舊的（用 --reindex 重建），或者這件事真的沒有寫下來。")
        return

    for order, hit in enumerate(hits, start=1):
        why = WHY.get(hit.reason, hit.reason)
        terms = "、".join(hit.matched_terms) if hit.matched_terms else "—"
        print(f"\n{order}. {_relative(hit.source_path)}　第 {hit.chunk_index} 段")
        print(f"　　為什麼：{why}　比中：{terms}")
        if full:
            for line in hit.content.strip().splitlines():
                print(f"　　│ {line}")
        else:
            print(f"　　{_one_line(hit.content)}")


def report_freshness(store: ProjectKnowledgeStore) -> None:
    """索引是什麼時候建的、有幾份已經過期。

    **這一段不准省。**一個過期的索引給出的答案，讀起來跟新鮮的一模一樣——
    那正是這支工具唯一會騙人的地方，所以它必須自己講出來。
    """
    try:
        stale = store.stale_document_ids()
        dead = store.dead_document_ids()
        total = len(store.list_documents())
    except Exception as exc:  # pragma: no cover - 索引壞掉時照樣要能用
        print(f"（讀不到索引狀態：{exc}）")
        return

    print(f"索引：{total} 份文件", end="")
    if stale:
        print(f"，**{len(stale)} 份已經被改過但索引還是舊的**", end="")
    if dead:
        print(f"，{len(dead)} 份的原始檔已經不在了", end="")
    print()
    if stale or dead:
        print("　　→ 這次的答案可能是過期的。重建：tools\\find.py --reindex")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="在這個專案裡找東西——問索引，不要用猜的。"
    )
    parser.add_argument("query", nargs="*", help="要找什麼（可以是一整句話）")
    parser.add_argument("--limit", type=int, default=8, help="最多印幾段")
    parser.add_argument(
        "--full", action="store_true", help="印出整段，不要只印一行"
    )
    parser.add_argument(
        "--reindex", action="store_true", help="重建索引再找"
    )
    parser.add_argument(
        "--add",
        metavar="資料夾",
        help="把另一個資料夾也收進索引（例如隔壁的設計原始檔）",
    )
    parser.add_argument(
        "--project", default=str(ROOT), help="專案根目錄（預設是這一個）"
    )
    args = parser.parse_args(argv)

    store = ProjectKnowledgeStore(Path(args.project))

    if args.add:
        # 這個專案的索引只掃自己的資料夾，而**今天要找的答案有一半在隔壁**
        # （`AICC-視覺概念/` 的往返文件）。收進來之後它們是 `imported`，
        # 跟專案自己的檔案分得出來。
        print(f"收進索引：{args.add}")
        added = store.import_folder(Path(args.add))
        print(f"完成：{added.summary}")
        if not args.query:
            return 0

    if args.reindex:
        print("重建索引中⋯⋯")
        result = store.rebuild_project_index()
        print(f"完成：{result.summary}")
        if not args.query:
            return 0

    if not args.query:
        parser.print_help()
        return 2

    query = " ".join(args.query)
    report_freshness(store)
    hits = store.search(query, max_chunks=args.limit)

    if not hits and len(args.query) > 1:
        # 整句比不到的時候，**拆開再比一次**，並且明講拆過。
        #
        # 中文沒有空白，所以一句「章要放在哪一頁」對分詞器來說常常是一個
        # 比不到任何東西的長 token。這裡的取捨跟 `search()` 裡那條相反，
        # 而且是刻意的：那裡怕的是「部分成功看起來像完全成功」，這裡怕的是
        # **完全失敗看起來像「這件事沒有寫下來」**——而那句話會讓人去重寫
        # 一份已經存在的文件。
        print("\n（整句比不到，拆成單詞再找一次）")
        seen: set[tuple[str, int]] = set()
        widened: list[KnowledgeChunk] = []
        for word in args.query:
            for hit in store.search(word, max_chunks=args.limit):
                key = (hit.source_path, hit.chunk_index)
                if key not in seen:
                    seen.add(key)
                    widened.append(hit)
        hits = widened[: args.limit]

    show(hits, full=args.full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
