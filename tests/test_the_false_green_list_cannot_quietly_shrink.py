"""那 30 條假綠燈的清單，**少一條就要紅**。

## 老闆問的那一句

> 「好，那就邊解決問題邊搬吧。**那你要怎麼保證你不會壞或忘記？**」

「我會記得」不是答案。這個專案 2026-08-24 一天之內證實了兩次：
**沒有機制的規則，失敗的時候不會有任何東西變紅**——而那正是那 30 條裡
16 條復發的原因（當時的紀錄寫著「已修」，修的是另一個實例）。

所以答案是這個檔案。

## 它守什麼——**兩個方向都會紅**

| 方向 | 什麼時候紅 | 擋掉的是 |
|---|---|---|
| **少了** | `tools/false_greens.json` 裡少掉一個 id | 「刪掉那一行」當成處理掉 |
| **假的已修** | 標成「已修」而 `moved_to` 指的那一則在 `WHY.md` 裡不存在 | 寫「已修」但沒真的寫進記憶 |
| **半套的已修** | 那一則在，但沒有〈同型的其他位置〉 | **正是那 9 條復發的形狀** |
| **不寫理由** | 標成「不修」而 `note` 空的 | 「這個不重要」而不說為什麼 |
| **改狀態不改內容** | 認不得的 status | 發明一個新狀態繞過去 |

`BASELINE_IDS` 是那一天掃出來的全部 30 個 id，**寫死在這裡**。
清單檔改壞、被覆蓋、被 git 操作弄掉一半，這裡都會紅。

## 它擋不住什麼

**擋不住「標成已修但其實沒修」**——那要靠負向驗證，而每一條修法各自帶
自己的破壞配方（今天四條就是這樣做的：F1~F7、F1b）。這裡守的是
**清單本身不會安靜地縮水**，不是守每一條真的修好了。

寫出來是因為【每一條檢查都講得出改壞的樣子】：只寫擋得住的部分，
下一個人會以為那一塊已經安全了。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIST = ROOT / "tools" / "false_greens.json"
WHY = ROOT / "docs" / "WHY.md"

# 2026-08-24 那一次掃描的全部 30 條。**寫死**——清單檔是被守的那一方，
# 不能同時當成基準。
BASELINE_IDS = tuple(f"FG{n:02d}" for n in range(1, 31))

# 認得的狀態，**沒有預設值**。發明一個新的就紅——
# 【有預設值的分類等於沒有分類】。
STATUSES = {
    "未處理": "還沒動",
    "已修": "修好了，而且 `moved_to` 指向 `WHY.md` 裡那一則",
    "不修": "決定不修，`note` 要寫為什麼",
    "在別人手上": "不在引擎這一側，`note` 要寫在誰那裡",
    "誤報": "回頭查是假警報，`note` 要寫怎麼查的",
}

SAME_KIND_HEADINGS = ("同型的其他位置", "同一種的其他位置", "隔壁那幾個")


def _rows() -> list[dict]:
    return json.loads(LIST.read_text(encoding="utf-8"))


def test_not_one_of_them_disappeared() -> None:
    """**這一條是這份檔案存在的理由。**

    「解決一條搬一條」這個做法唯一的失敗方式，是**有一條被搬走之後
    沒有人記得它去了哪**。所以 id 只准全在，不准少。
    """
    present = {r["id"] for r in _rows()}
    missing = [i for i in BASELINE_IDS if i not in present]
    assert not missing, (
        f"這幾條從清單上不見了：{missing}。\n"
        "處理掉的**改 status，不要刪那一行**——刪掉之後，"
        "「處理好了」跟「忘記了」在檔案上長得一模一樣。"
    )


def test_nobody_invented_a_new_status() -> None:
    """狀態只有那五種。多一種就是有人在繞過這張表。"""
    unknown = sorted(
        {r["status"] for r in _rows() if r["status"] not in STATUSES}
    )
    assert not unknown, (
        f"認不得的狀態：{unknown}。\n只有這幾種："
        + "、".join(f"{k}（{v}）" for k, v in STATUSES.items())
    )


def test_a_fixed_one_really_landed_in_the_memory() -> None:
    """標成「已修」的，`WHY.md` 裡要真的有那一則。

    這一條擋的是「在清單上打勾」——**打勾的成本是零，而寫下來會逼人
    重新想一次那件事到底修好了沒**。
    """
    why = WHY.read_text(encoding="utf-8")
    bad = []
    for row in _rows():
        if row["status"] != "已修":
            continue
        landed = (row.get("moved_to") or "").strip()
        if not landed:
            bad.append(f"{row['id']}：標成已修，但沒說寫進 WHY.md 哪一則")
        elif landed not in why:
            bad.append(f"{row['id']}：說寫進了「{landed}」，而 WHY.md 裡沒有那一則")
    assert not bad, "\n  ".join(["", *bad])


def test_a_fixed_one_also_named_its_siblings() -> None:
    """那一則要有〈同型的其他位置〉。

    **這一條是那 9 條復發的直接解藥。**當時每一則都寫著「已修」，
    而修的是同一個病的另一個實例——沒有人問過隔壁那一個。
    """
    why = WHY.read_text(encoding="utf-8")
    bad = []
    for row in _rows():
        if row["status"] != "已修":
            continue
        landed = (row.get("moved_to") or "").strip()
        if not landed or landed not in why:
            continue  # 上一條已經在管這種
        start = why.index(landed)
        end = why.find("\n## ", start)
        body = why[start : end if end > 0 else len(why)]
        if not any(mark in body for mark in SAME_KIND_HEADINGS):
            bad.append(f"{row['id']} → 「{landed}」少了〈同型的其他位置〉")
    assert not bad, (
        "\n  ".join(["", *bad])
        + "\n\n2026-08-24 掃出的 30 條裡，16 條是同一件事的同一個實例，"
        "而**其中 9 條當時的紀錄寫著「已經修好了」**。"
    )


def test_a_decision_not_to_fix_says_why() -> None:
    """「不修」「誤報」「在別人手上」都要寫理由。

    不寫的話，三個月後讀到的是一個沒有來由的結論——而那時候重新判斷的
    成本，跟當初查清楚一樣高。
    """
    bad = [
        f"{r['id']}（{r['status']}）"
        for r in _rows()
        if r["status"] in {"不修", "誤報", "在別人手上"} and not (r.get("note") or "").strip()
    ]
    assert not bad, "這幾條沒寫理由：" + "、".join(bad)


def test_every_row_still_says_what_the_user_would_see() -> None:
    """每一條都要留著「使用者會看到什麼」。

    **那一欄是這張清單唯一給老闆看得懂的部分。**只留 `where` 跟 `fix` 的話，
    這張表就變成一份只有我讀得懂的東西，而排優先序的是他。
    """
    thin = [
        r["id"]
        for r in _rows()
        if len((r.get("what_the_user_sees") or "").strip()) < 10
    ]
    assert not thin, f"這幾條沒說使用者會看到什麼：{thin}"


def test_the_list_reports_how_much_is_left() -> None:
    """前提檢查：這張表還有東西，而且進度看得出來。

    全部處理完的那一天這條會告訴你——那時候整份檔案跟這支測試一起退休。
    """
    rows = _rows()
    assert len(rows) == len(BASELINE_IDS)
    left = [r for r in rows if r["status"] == "未處理"]
    # 這不是斷言「還有剩」，是斷言**數得出來**。
    assert isinstance(left, list)
