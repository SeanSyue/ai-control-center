"""Roadmap panel — renders roadmap_v1.html from ROADMAP.md.

The design handoff asks for this explicitly: "建議由 Python 解析 ROADMAP.md 產生，
不要另外手維護一份". A second hand-maintained list would drift from the real
roadmap within weeks.

ROADMAP.md states progress in prose ("狀態：第一版完成，已加入…") and lists work
under headers written three ways each - 已完成／已完成（日期）／已完成的基礎能力
for shipped work, 下一步／需要做／還沒做 for the rest. Matching those headers
exactly (rather than by prefix) silently dropped P6's entire completed list, so
they are matched by prefix here.

Phase state comes from the prose, not from the lists: every phase carries a
下一步 list, and those are future enhancements rather than evidence the phase is
unfinished - scoring on the lists alone marked all eleven phases "now", which
tells a reader nothing. Because that is still a heuristic over a sentence,
**the sentence itself is shown as the first item of every phase**, so a wrong
guess can never hide what the roadmap actually says.
"""
from __future__ import annotations

import re

from app.core import prompt_markers
from pathlib import Path

from app.ui.web_panel import WebViewPanel
from app.i18n import t

ROADMAP = Path(__file__).resolve().parents[2] / "ROADMAP.md"

HEADING = re.compile(r"^##\s+(P[0-9.]+)\s*[-–—]\s*(.+?)\s*$")
ANY_H2 = re.compile(r"^##\s+")
STATUS = re.compile(prompt_markers.ROADMAP_STATUS)
# ROADMAP.md writes these headers three ways each, including a parenthesised
# date ("已完成（2026-08-05）："), so match on the prefix rather than the whole
# line - an exact match silently skipped P6's entire completed list.
DONE_HEADER = re.compile(prompt_markers.ROADMAP_DONE_HEADER)
TODO_HEADER = re.compile(prompt_markers.ROADMAP_TODO_HEADER)
OTHER_HEADER = re.compile(r"^\S[^：:]*[：:]\s*$")
TOP_BULLET = re.compile(r"^-\s+(.+?)\s*$")
SUB_BULLET = re.compile(r"^\s{2,}-\s+(.+?)\s*$")

NOT_STARTED = prompt_markers.ROADMAP_NOT_STARTED
# `ROADMAP.md` 裡「這一階段做完了」的說法。**跟 NOT_STARTED／PARTIAL 同一族**
# ——它們比對的是那份文件的中文，不是畫面上的字，所以住在 marker 那一側。
COMPLETE = prompt_markers.ROADMAP_COMPLETE
PARTIAL = prompt_markers.ROADMAP_PARTIAL
# 「講了完成，可是又轉折回去」的那一族。見 `state_from_prose`。
UNFINISHED_TURN = prompt_markers.ROADMAP_UNFINISHED_TURN


def state_from_prose(status_text: str) -> str:
    """從那一句「狀態：⋯」猜這一階段走到哪。

    **這不是 fallback。**`state_from_items` 在 `status_text` 非空的時候拿它
    當主判準，而 ROADMAP.md 每一個階段都寫了那一句話——所以它幾乎總是主判準。
    2026-08-24 之前這裡寫的是「Fallback only: used when a phase lists no items
    at all」，那句話從它變成主判準的那天起就是假的，而它正好是唯一會讓人
    不去細看這個函式的那句話。

    ## 為什麼「完成」兩個字不夠

    實測 ROADMAP.md 判錯兩段，兩段都被畫成完成色、算進標題那句「N / 14
    階段完成」：

    * P12「**方向待決定**（2026-08-09 提出）。CLI 探索與用量記錄兩個服務層
      都已**完成**⋯」
    * P6「⋯已**完成**（2026-08-08）。**只剩**「線上檢查更新」，而那件事
      **卡在**一個**還沒**有的東西：發佈來源。」

    所以多一條規則：**同一句話裡同時出現完成詞與轉折詞，不准判 done，
    降成 now**。降級是安全的方向——把「做了一半」讀成「做完了」是說謊，
    反過來只是保守。

    ## 這一條擋不住什麼

    擋不住**沒有用到那幾個字的轉折**：「⋯已完成，但發佈來源還沒有著落」
    裡的「但」不在清單上（「但」太常出現在真的做完了的句子裡，收進來會
    每一段都降級）。這是啟發式，不是解析——所以那一句話本身永遠印在該階段
    的第一列，猜錯藏不住。
    """
    if any(k in status_text for k in NOT_STARTED):
        return "todo"
    if any(k in status_text for k in PARTIAL):
        return "now"
    if not any(k in status_text for k in COMPLETE):
        return "todo"
    if any(k in status_text for k in UNFINISHED_TURN):
        # 這句話自己講了「可是」。
        return "now"
    return "done"


def state_from_items(items: list[dict], status_text: str) -> str:
    """Phase state.

    The prose leads. Every phase in ROADMAP.md carries a 下一步 list, and those
    are future enhancements rather than proof the phase is unfinished - scoring
    on the lists alone marked all eleven phases "now", which tells the reader
    nothing. The lists are still parsed, because they are what the page shows.
    """
    # 那一列的標題**兩邊都用 `t()`**，所以它們永遠一致。這裡跟
    # `lane_for` 那個坑不一樣：那個值進過硬碟、跨過版本；這一列是同一次
    # 呼叫裡建出來又馬上比掉的，不會有機會漂開。
    real = [i for i in items if i["title"] != t("dash.detail.state")]
    if status_text:
        guess = state_from_prose(status_text)
        # 一段敘述說「還沒開始」、但底下的已完成清單有東西——那就是在跑，
        # 不是沒動。（這裡原本引用 P6「仍未開始」當例子，而 ROADMAP.md:868
        # 那句話早就整段改掉了，現在一個字都不剩——**過期的例子比沒有例子
        # 更貴**：它會讓下一個人以為這條路徑有人在走。今天真的踩得到的是
        # P11「已文件化，尚未驗證」那一種寫法。）
        if guess == "todo" and any(i["done"] for i in real):
            return "now"
        return guess
    if any(i["done"] for i in real):
        return "now"
    return "todo"


def parse_roadmap(text: str) -> list[dict]:
    phases: list[dict] = []
    current: dict | None = None
    status_text = ""
    collecting: bool | None = None      # True=done list, False=todo list, None=neither

    for raw in text.splitlines():
        line = raw.rstrip()

        if current is not None and ANY_H2.match(line) and not HEADING.match(line):
            # A non-phase section (e.g. 版本計畫) closes the current phase.
            current["state"] = state_from_items(current["items"], status_text)
            current = None
            collecting = None
            continue

        heading = HEADING.match(line)
        if heading:
            if current is not None:
                current["state"] = state_from_items(current["items"], status_text)
            current = {
                "id": heading.group(1),
                "title": heading.group(2),
                "version": "",
                "state": "todo",
                "items": [],
            }
            phases.append(current)
            status_text = ""
            collecting = None
            continue

        if current is None:
            continue

        status = STATUS.match(line)
        if status:
            status_text = status.group(1)
            # The original sentence leads every phase, so no heuristic can hide
            # what the roadmap actually says.
            current["items"].insert(
                0,
                {"title": t("dash.detail.state"), "note": status_text, "done": True},
            )
            continue

        if DONE_HEADER.match(line):
            collecting = True
            continue
        if TODO_HEADER.match(line):
            collecting = False
            continue

        sub = SUB_BULLET.match(raw)
        if sub and collecting is not None and current["items"]:
            # Nested bullets become a note on the parent item rather than
            # separate rows, which keeps the list readable.
            last = current["items"][-1]
            last["note"] = (last["note"] + "、" if last["note"] else "") + sub.group(1)
            continue

        top = TOP_BULLET.match(line)
        if top and collecting is not None:
            current["items"].append(
                {"title": top.group(1), "note": "", "done": collecting}
            )
            continue

        # Any other header ends the current list so unrelated bullets are not
        # swept into the previous section.
        if OTHER_HEADER.match(line):
            collecting = None

    if current is not None:
        current["state"] = state_from_items(current["items"], status_text)
    return phases


class RoadmapWebPanel(WebViewPanel):
    PAGE = "roadmap_v1.html"

    def set_project(self, project_path) -> None:
        """Accepted for interface parity with the native RoadmapPanel.

        The roadmap belongs to AICC itself, not to whichever project is in
        focus, so switching projects does not change what this shows. The
        method exists so MainWindow can swap the panels with one line.
        """
        self.push_state()

    def collect_state(self) -> dict:
        try:
            text = ROADMAP.read_text(encoding="utf-8")
        except OSError:
            return {"mode": self._mode, "phases": []}
        return {"mode": self._mode, "phases": parse_roadmap(text)}
