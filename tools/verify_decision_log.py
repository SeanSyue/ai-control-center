"""Check an exported decision log's chain, without opening AICC.

    python tools/verify_decision_log.py 決策紀錄_csv-summary_20260809.jsonl

Standalone on purpose. A log verified only by the program that wrote it is
worth less to whoever is asking for it - the point of handing someone this file
is that they can check it themselves, on their machine, without trusting ours.

**What a pass means:** no line was edited or removed after it was written.
**What it does not mean:** that the log is honest. Anyone who can edit the file
can recompute the whole chain. Detecting a single-point edit is the entire
claim - see ROADMAP P8.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.long_task import GENESIS, _digest  # noqa: E402
from app.services.decision_export import load_chain  # noqa: E402

# 這支工具會印非 ASCII，而 Windows 的預設輸出編碼是 cp950：編不出來的字元會讓
# 整支變成 traceback。危險的地方是**它只在有話要說的時候才炸**——`✗` 只在失敗
# 時才印，所以一切正常的時候完全看不出來。
# 這一行不是為了好看：一支工具的輸出就是它唯一的產物。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

BAR = "=" * 62


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    path = Path(argv[1]).expanduser()
    if not path.is_file():
        print(f"X 找不到檔案：{path}")
        return 2

    entries = load_chain(path)
    print(BAR)
    print(f"檔案：{path.name}")
    print(f"筆數：{len(entries)}")

    prev = GENESIS
    verified = unchained = 0

    for index, payload in enumerate(entries):
        if "hash" not in payload:
            unchained += 1
            prev = GENESIS
            continue
        if str(payload.get("prev_hash", "")) != prev:
            print(f"\nX 第 {index + 1} 筆與前一筆接不上——中間有紀錄被刪除或插入")
            print(f"   {_summarise(payload)}")
            return 1
        if _digest(payload, prev) != str(payload.get("hash", "")):
            print(f"\nX 第 {index + 1} 筆內容與指紋不符——這一筆被改過")
            print(f"   {_summarise(payload)}")
            return 1
        verified += 1
        prev = str(payload["hash"])

    print(f"\nOK {verified} 筆通過驗證")
    if unchained:
        print(
            f"   另有 {unchained} 筆沒有指紋（指紋機制上線前寫入），無法驗證——"
            "這不是損壞"
        )
    print("\n通過的意思是：沒有任何一筆在寫入之後被修改或刪除。")
    print("它不能證明紀錄本身誠實——能編輯檔案的人也能把整條鏈重算一次。")
    return 0


def _summarise(payload: dict) -> str:
    question = str(payload.get("question", ""))[:40]
    return f"{payload.get('created_at', '')}  {payload.get('action', '')}  {question}"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
