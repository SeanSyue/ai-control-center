"""這個專案的共同記憶，實際送出去多少——以及有沒有東西被切掉。

這支工具原本問錯了問題。它的通過條件是「**只要有任何一個模式拿得到全部就算
OK**」，而 2026-08-16 之後拿得到全部的是 `strict`（深入）——**出廠預設是
`minimal`（省額度）**。所以它永遠是綠燈，而預設模式下八份全被切成 750 字，
`SECURITY_MODEL.md` 斷在「執行期狀態（DB、鎖、快取、暫」，「暫存」兩個字被切成
兩半。一個量錯對象的綠燈：它量的是「有沒有一種設定做得到」，而使用者
在乎的是「**我這台機器上實際會用的設定，做不做得到**」。

2026-08-21 拿掉截斷之後，這支工具換了角色：

- 不再判斷「有沒有超標」——沒有上限了，超不了標。
- 改成**回報實際送出去多少**（每份幾字、合計幾字、約幾個 token）。
- 判定條件改成**每一個使用者選得到的設定，八份都要完整送到**，而且
  `MEMORY_MUST_ARRIVE_WHOLE`（安全規則、不准自己決定的清單）要逐字對得上
  硬碟原文。任何一項不成立就回非零。

它現在正常情況下當然會通過——那是對的。這支工具的價值不在於今天會不會紅，
而在於**哪天有人在組裝路徑上重新加了一刀，它會紅**。跑的是真的組裝函式
（`build_context`），不是一份「應該會這樣」的複述。

不呼叫任何 CLI、不花錢、唯讀。

    python tools/memory_budget.py [專案路徑]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import rule_layers  # noqa: E402
from app.core.lineup import VALID_DUTIES  # noqa: E402
from app.services.project_knowledge import (  # noqa: E402
    CONTEXT_PROFILES,
    MEMORY_FILES,
    MEMORY_MUST_ARRIVE_WHOLE,
    MemoryTruncatedError,
    ProjectKnowledgeStore,
)

# 印中文到被導向的 stdout 時，cp950 會炸。這個專案已經因為同一件事修過六支工具
# ——而且它們都只在「有話要說」的時候才炸。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # stderr 也要：traceback 走那一條，只設 stdout 的話它會在印出
    # 崩潰訊息的路上再崩潰一次，而使用者拿到一個沒頭沒尾的錯誤。
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 這支工具問的問題是「使用者實際會用的設定送得出去嗎」，所以探針要打在使用者
# 選得到的每一個設定上，不是打在最寬鬆的那一個。
PROBE_TASK = "共同記憶完整性檢查"


def classify(context: str, title: str) -> str:
    """一份記憶在組出來的脈絡裡是什麼狀態。

    「節錄」與「只留名」是舊分配邏輯的產物，現在的組裝路徑不會產生它們。
    留著這兩個分類是因為**它們就是這支工具要抓的東西**：認得出來才報得出來。
    少了它們，一個被截斷的檔案會被字面比對當成「完整」——第一次寫這個判斷時
    就是這樣錯的，探針回報 0/8 而實際上八份都在。
    """
    if f"### {title}\n（這次沒有送出" in context:
        return "只留名"
    if f"### {title}（節錄 " in context:
        return "節錄"
    if f"### {title}\n" in context:
        return "完整"
    return "沒出現"


def report(project: Path) -> int:
    """回傳 exit code：0 = 每個設定下八份都完整送到，1 = 有東西沒送到。"""
    store = ProjectKnowledgeStore(project)
    lengths: dict[str, int] = {}
    for file_name in MEMORY_FILES:
        path = store.knowledge_path / file_name
        if not path.is_file():
            continue
        lengths[file_name] = len(path.read_text(encoding="utf-8").strip())

    if not lengths:
        print(f"UNAVAILABLE: {project} 底下沒有共同記憶檔。")
        return 1

    total = sum(lengths.values())
    print(f"專案：{project}")
    print(f"共同記憶 {len(lengths)} 份，合計 {total:,} 字\n")
    for file_name, length in lengths.items():
        must = "  ←不准截斷" if file_name in MEMORY_MUST_ARRIVE_WHOLE else ""
        print(f"   {file_name:<24}{length:>7,}{must}")

    # 這才是「實際送出去多少」：跑真的組裝函式，量它吐出來的字串。
    print()
    print(f"   {'設定':<10}{'完整':>7}{'送出字數':>12}{'約 tokens':>12}")
    problems: list[str] = []
    for name in CONTEXT_PROFILES:
        try:
            context = store.build_context(PROBE_TASK, context_profile=name)
        except MemoryTruncatedError as error:
            # 組裝端自己就攔下來了。這是好消息（機制有牙齒），但仍然是紅燈。
            print(f"   {name:<10}   組裝失敗：{error}")
            problems.append(f"{name}：{error}")
            continue
        memory = store.build_memory_context()
        complete = sum(
            1 for title in MEMORY_FILES.values() if classify(context, title) == "完整"
        )
        print(
            f"   {name:<10}{complete:>5}/{len(lengths)}"
            f"{len(memory):>12,}{len(memory) // 3:>12,}"
        )
        for file_name, title in MEMORY_FILES.items():
            state = classify(context, title)
            if state != "完整":
                problems.append(f"{name}／{file_name}：{state}")
        for file_name in MEMORY_FILES:
            path = store.knowledge_path / file_name
            if not path.is_file():
                continue
            body = path.read_text(encoding="utf-8").strip()
            if body and body not in context:
                problems.append(f"{name}／{file_name}：全文對不上硬碟原文")

    # ---- 派工那條路：底座＋專案規則逐字送，其餘只給指標 ----------------
    #
    # **這一段補的正是這支工具最容易變成假綠燈的地方。**上面那一段量的是
    # `build_context()` 不帶職責的那條路——而那條路是給**人**看的文件
    # （交接包、啟動脈絡）走的。派工走的是另一條。
    #
    # 少了這一段，【規則分三層】上線的那一天，這支工具會繼續對著一條沒有
    # 人在跑的路徑印綠燈。
    print()
    print("   派工時每一格實際收到什麼（規則分三層）")
    for duty in VALID_DUTIES:
        try:
            dispatched = store.build_dispatch_context(duty)
        except MemoryTruncatedError as error:
            print(f"   {duty:<10}   組裝失敗：{error}")
            problems.append(f"派工／{duty}：{error}")
            continue

        pushed = [
            name
            for name in rule_layers.pushed_files(True)
            if (store.knowledge_path / name).is_file()
        ]
        pointed = [
            name
            for name in rule_layers.read_before_starting(duty)
            if (store.knowledge_path / name).is_file()
        ]
        print(
            f"   {duty:<10}{len(dispatched):>9,} 字"
            f"{len(dispatched) // 3:>8,} token"
            f"   逐字：{'、'.join(pushed)}"
            f"   點名：{'、'.join(pointed) or '（無）'}"
        )
        # 逐字送的那幾份一個字都不准少。**比的是硬碟原文**，不是標題還在
        # 不在——尾巴被剪掉的時候標題還在。
        for file_name in pushed:
            body = (
                (store.knowledge_path / file_name)
                .read_text(encoding="utf-8")
                .strip()
            )
            if body and body not in dispatched:
                problems.append(
                    f"派工／{duty}／{file_name}：全文對不上硬碟原文"
                )
        # 點名的那幾份要真的被點到名。只給索引不點名，等於把「AI 可能不去
        # 讀」那個風險原封不動留著。
        for file_name in pointed:
            if file_name not in dispatched:
                problems.append(f"派工／{duty}／{file_name}：沒有被點名")

    print()
    if not problems:
        print(
            "OK：\n"
            "  · 給人看的文件：八份共同記憶都逐字比對硬碟原文、完整送到。\n"
            "  · 派工：每一種職責的底座都逐字送到，該點名的都點到了。"
        )
        return 0
    print("有東西沒有完整送到 prompt：")
    for line in problems:
        print(f"   !! {line}")
    print(
        "\n      共同記憶不該被任何機制截斷——被截斷的安全規則讀起來跟完整的\n"
        "      一模一樣。查組裝路徑上是不是有人重新加了字數上限\n"
        "      （`build_memory_context` / `build_context` / `ContextProfile`）。\n"
        "      **不要用調高上限解決**，那只是把同一刀往後挪。"
    )
    return 1


def main() -> int:
    raw = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    project = Path(raw).expanduser().resolve()
    if not project.is_dir():
        print(f"UNAVAILABLE: 找不到專案資料夾 {project}")
        return 1
    return report(project)


if __name__ == "__main__":
    raise SystemExit(main())
