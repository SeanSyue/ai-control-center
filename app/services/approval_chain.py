"""章：誰蓋的、蓋在哪一版、憑什麼。

2026-08-21 定案的簽核鏈，這裡是它的資料與規則層。**不碰畫面**——介面那邊
的九題決定停在 `AICC-視覺概念/UI決定紀錄-2026-08-21.md`，動工前提就是這個
檔案先存在。

## 為什麼章不能只是「這一關過了」

一句話：**章是回溯的憑據，不是放行的開關。**

把簽核畫成一串紅綠燈，形狀在講「這關過了就往下」。定案要的是另一件事：
章會累積，而最終拍板要求**所有的章蓋在同一個內容版本上**。少一個章不是
「卡在那一關」，是「整件事還不能拍板」——所以這裡回報的是**還缺哪幾個**，
不是「走到第幾格」。

## 機器章與審查章為什麼要分開

- **機器章**記錄「跑了什麼指令、輸出是什麼、怎麼判定的」。
- **審查章**記錄「我看了、我同意」。

兩者都是真的章，但**守衛只認機器章**。理由是這個專案付過學費的那一條：
一個沒有證據的「我同意」，讀起來跟一個跑過測試的「通過」一模一樣，而它
們的可信度差了一個數量級。所以：

- 機器章沒有 `evidence` 就建不出來（`ApprovalChainError`）。
- 審查章不准帶 `result`——「我同意」不是一個量出來的結果。

這兩條寫在 `Stamp.__post_init__`，不是寫在註解裡。**寫在註解裡的規則會漏。**

## 「沒得驗」是第三種結果，不是失敗

`Result.GATED` 存在的理由是實測換來的：這個專案的測試印出「1334 passed」，
離開碼卻因為關閉時崩潰而不是 0。照離開碼判，引擎會派人去修一個沒壞的東西。

「這件事沒有宣告怎麼驗」跟「驗了，紅的」是兩種完全不同的處境，混在一起
就會**派人去修一個沒壞的東西**。所以 GATED 不是 FAIL 的一種。

## 失效範圍由依賴決定

內容改了，蓋在舊版上的章失效——但**只有依賴它的那些跟著失效，不是全部
一起劃掉**。算不出依賴的時候唯一「誠實」的退路是全部劃掉，而那是說謊：
它會逼使用者重做不需要重做的關。

所以 `Stamp.depends_on` 是必要欄位而不是選配：拿不到依賴就不要蓋這個章。
"""
from __future__ import annotations

from app.i18n import t

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ApprovalChainError(ValueError):
    """一個蓋不出來的章。

    刻意在建構的時候就丟，不是在拍板的時候才檢查：一個違反規則的章一旦
    進了鏈子，後面每一次讀它的人都要重新判斷它算不算數，而其中一定有人
    會忘記判斷。
    """


class StampKind(str, Enum):
    MACHINE = "machine"
    REVIEW = "review"


class Result(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    # 「沒有宣告怎麼驗」。不是 FAIL 的一種——見模組說明。
    GATED = "gated"


# 章蓋在什麼事情上。刻意是常數而不是自由字串：subject 是判斷「驗收標準的
# 章跟實作的章是不是同一個角色蓋的」的依據，拼錯一個字那條檢查就靜靜地
# 不再成立，而畫面上完全看不出來。
SUBJECT_ACCEPTANCE = "acceptance"   # 怎樣算做完
SUBJECT_WORK = "work"               # 實作本身
SUBJECT_VERIFY = "verify"           # 驗證跑過了

SUBJECTS = (SUBJECT_ACCEPTANCE, SUBJECT_WORK, SUBJECT_VERIFY)


def content_digest(content: str) -> str:
    """一份內容的版本碼。

    章綁在這個值上。它必須從內容本身算出來——**畫面不准自己編一個版本碼**，
    因為那樣整個失效機制都是假的：內容變了而版本碼沒變，舊的章看起來還
    有效。守著這件事的是「值」那個維度的檢查（推一個不可能寫死的值進去，
    看它出不出現）。
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Stamp:
    """一個章。

    `role` 用的是 `app/core/lineup.py` 既有的詞彙（author / reviewer /
    finisher / planner）加上 `USER`，不另立一套。使用者看到的名字（執行／
    複查／核准）是顯示層的事。
    """

    stamp_id: str
    kind: StampKind
    role: str
    subject: str
    # 蓋章當下那份內容的版本碼。章跟著它走。
    content_digest: str
    at: str
    # 機器章專用：跑了什麼、怎麼判定的。沒有它就不是機器章。
    evidence: str = ""
    # 審查章專用：**為什麼同意**。老闆 2026-08-21 的原話是「簽核章需要有
    # 時間戳記 對應**邏輯審核**還有你說的版本」——那個「邏輯審核」就是這一格。
    #
    # 一開始這裡是空的（審查章不准帶任何內容），於是章只回答得了「誰、何時、
    # 哪一版」，回答不了「憑什麼」。而事後回溯的時候，最需要的正是憑什麼。
    reasoning: str = ""
    result: Result | None = None
    # 這個章建立在哪些章之上。空的 tuple 是合法的（第一個章沒有前提），
    # 但它必須是「確實沒有前提」，不是「懶得算」——見模組說明。
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.stamp_id.strip():
            raise ApprovalChainError(t("stamp.needs_index"))
        if not self.role.strip():
            raise ApprovalChainError(t("stamp.needs_role"))
        if self.subject not in SUBJECTS:
            raise ApprovalChainError(
                f"不認得的蓋章對象 {self.subject!r}；"
                f"只能是 {', '.join(SUBJECTS)}"
            )
        if not self.content_digest.strip():
            raise ApprovalChainError(
                t("stamp.needs_digest")
            )

        if self.kind is StampKind.MACHINE:
            if self.result is None:
                raise ApprovalChainError(t("stamp.machine.needs_result"))
            if not self.evidence.strip():
                raise ApprovalChainError(
                    t("stamp.machine.needs_evidence")
                )
            if self.reasoning.strip():
                raise ApprovalChainError(
                    t("stamp.machine.no_reasoning")
                )
        else:
            if self.result is not None:
                raise ApprovalChainError(
                    t("stamp.review.no_result")
                )
            if self.evidence.strip():
                raise ApprovalChainError(
                    t("stamp.review.no_evidence")
                )
            if not self.reasoning.strip():
                raise ApprovalChainError(
                    t("stamp.review.needs_reasoning")
                )


@dataclass(frozen=True)
class Versions:
    """此刻每一種被蓋章的東西各自是哪一版。

    為什麼不是一個字串：**驗收標準與交付物是兩份會各自改動的東西**。

    用同一個版本碼的話會有一個看不出來的錯誤——訂標準的那一格在動手之前
    先訂下驗收標準、蓋了章，然後執行的那一格交出東西，版本碼變了，**那個
    章當場失效**。畫面上看起來會是「沒有人訂標準」，而事實是訂了，只是被
    算成過期的。

    分開之後，兩者的關係由 `depends_on` 表達而不是由版本碼表達：實作的章
    建立在驗收標準的章之上，所以**標準改了，蓋在舊標準上的實作與驗證跟著
    失效**——那正是要的行為，而且是自動的。

    這一條在 2026-08-22 變得非做不可：定案【驗收標準要能被駁回】讓標準本身
    成為一份會改版的東西，而不是跑之前定死的常數。
    """

    criteria: str
    deliverable: str

    def for_subject(self, subject: str) -> str:
        return (
            self.criteria if subject == SUBJECT_ACCEPTANCE else self.deliverable
        )

    @classmethod
    def of(cls, criteria: str, deliverable: str) -> "Versions":
        """從內容本身算，不是從外面接一個版本碼進來。"""
        return cls(
            criteria=content_digest(criteria),
            deliverable=content_digest(deliverable),
        )


@dataclass(frozen=True)
class Required:
    """拍板前必須有的一個章。

    這份清單由角色表格決定（一人／兩人／三人），**由外面傳進來**。這個
    模組不准自己發明「應該要幾個章」——那是角色表格的事，寫死在這裡就
    會跟表格漂開，而且漂開的時候不會有任何東西變紅。
    """

    role: str
    subject: str
    kind: StampKind = StampKind.MACHINE


class Verdict(str, Enum):
    READY = "ready"
    # 章數不足。
    MISSING = "missing"
    # 章都在，但蓋在不同版本的內容上——所以沒有任何一版是「大家都同意」的。
    SPLIT_VERSION = "split_version"
    # 訂驗收標準的跟做事的是同一個角色。
    SELF_REVIEW = "self_review"
    # 有東西沒得驗。不是失敗。
    GATED = "gated"
    # 有機器章是紅的。
    FAILED = "failed"


@dataclass(frozen=True)
class Readiness:
    """能不能拍板，以及為什麼不能。

    `missing` 給的是「還缺哪幾個」而不是「走到第幾關」：定案原文是
    **「少一個章不是卡在那一關，是整件事還不能拍板」**。回報成關卡序號
    就等於把一個被否決的形狀又做回來。
    """

    verdict: Verdict
    missing: tuple[Required, ...] = ()
    # SPLIT_VERSION 時：現場出現過哪些版本碼。
    versions: tuple[str, ...] = ()
    # SELF_REVIEW 時：是哪個角色同時訂標準又做事。
    conflicting_role: str = ""
    # GATED / FAILED 時：是哪些章。
    blocking: tuple[Stamp, ...] = ()

    @property
    def can_decide(self) -> bool:
        return self.verdict is Verdict.READY

    @property
    def short(self) -> str:
        """一句話版本，給顯示層用。

        刻意在這裡而不是在畫面上拼字串：同一句話會出現在時間軸、決策橫幅
        與匯出報告三個地方，各拼一次就會有三種說法，而使用者會以為那是
        三件不同的事。
        """
        if self.verdict is Verdict.READY:
            return t("chain.decidable")
        if self.verdict is Verdict.MISSING:
            return f"缺 {len(self.missing)} 個章"
        if self.verdict is Verdict.SPLIT_VERSION:
            return t("chain.split_version")
        if self.verdict is Verdict.SELF_REVIEW:
            return f"「{self.conflicting_role}」同時訂了標準又做了事"
        if self.verdict is Verdict.GATED:
            return t("undecidable.GATED.stage")
        return t("undecidable.FAILED.label")


class ApprovalChain:
    """一次任務累積起來的章。

    刻意不是「目前走到第幾關」的狀態機：章會累積、會因為內容改變而失效、
    而且失效之後可以重新蓋上。狀態機表達不了「回頭補一個章」這件事，而
    那正是駁回之後會發生的事。
    """

    def __init__(self, stamps: tuple[Stamp, ...] = ()) -> None:
        self._stamps: list[Stamp] = list(stamps)

    @property
    def stamps(self) -> tuple[Stamp, ...]:
        return tuple(self._stamps)

    def add(self, stamp: Stamp) -> "ApprovalChain":
        if any(existing.stamp_id == stamp.stamp_id for existing in self._stamps):
            raise ApprovalChainError(f"章 {stamp.stamp_id} 已經在鏈子裡了")
        unknown = [
            parent
            for parent in stamp.depends_on
            if not any(existing.stamp_id == parent for existing in self._stamps)
        ]
        if unknown:
            raise ApprovalChainError(
                t("chain.builds_on_missing")
                + "、".join(unknown)
            )
        return ApprovalChain(tuple(self._stamps) + (stamp,))

    def live(self, versions: Versions) -> tuple[Stamp, ...]:
        """在目前這一版內容上，還算數的章。

        兩件事會讓一個章不算數：它蓋在別的版本上，或者它依賴的章不算數了。
        第二條是「失效範圍由依賴決定」的實作——**沒有依賴關係的章不會被
        牽連**，這是硬條件。

        比對的是**它那一類東西**現在的版本（見 `Versions`）：驗收標準的章
        比驗收標準，實作與驗證的章比交付物。
        """
        alive: dict[str, Stamp] = {}
        # 同一個人、同一件事、同一種章，**後蓋的蓋掉先蓋的**。
        #
        # 沒有這一條的話，一次跑紅了、修好了、再跑綠了的執行，鏈子上會同時
        # 留著那個紅的與那個綠的——而「有紅的」排在「缺章」前面，所以拍板
        # 檢查會說「有檢查是紅的」。**那句話是對的，只是它講的是已經被修掉
        # 的那一次。**
        #
        # 只在**同一版內容**上成立：內容換了版本碼就不同，上面那條比對已經
        # 把它擋在外面了。所以這不是「用新的蓋掉舊的證據」，是「對同一份東西
        # 重跑了一次，以最後那次為準」。
        supersedes: dict[tuple[str, str, StampKind], str] = {}
        for stamp in self._stamps:
            if stamp.content_digest != versions.for_subject(stamp.subject):
                continue
            if any(parent not in alive for parent in stamp.depends_on):
                continue
            key = (stamp.role, stamp.subject, stamp.kind)
            previous = supersedes.get(key)
            if previous is not None:
                alive.pop(previous, None)
            supersedes[key] = stamp.stamp_id
            alive[stamp.stamp_id] = stamp
        return tuple(alive.values())

    def invalidated_by(self, versions: Versions) -> tuple[Stamp, ...]:
        """換成這一版之後，哪些章會掉下來。

        給畫面用的：它要能寫出「因為【複查】在 10:42 改了驗收標準」，而不是
        默默把章變灰。只改樣式不改文字，使用者會以為那只是視覺效果。
        """
        surviving = {stamp.stamp_id for stamp in self.live(versions)}
        return tuple(
            stamp for stamp in self._stamps if stamp.stamp_id not in surviving
        )

    def readiness(
        self,
        versions: Versions,
        required: tuple[Required, ...],
    ) -> Readiness:
        """能不能拍板。

        檢查的順序是刻意的，因為**先報哪一個決定使用者先去修哪一個**：

        1. 自己驗自己——這一條是結構性的，補再多章都不會變好。
        2. 有紅的——先修壞掉的，再談缺不缺。
        3. 沒得驗——這一格是 gated 不是 error，要走「請設定」而不是「請修」。
        4. 版本分岔——章都在但不在同一版，補章沒有用，要先收斂內容。
        5. 缺章——上面都沒事才輪到「還缺幾個」。
        """
        live = self.live(versions)

        conflicting = self._self_review_role(live)
        if conflicting:
            return Readiness(
                verdict=Verdict.SELF_REVIEW, conflicting_role=conflicting
            )

        failed = tuple(s for s in live if s.result is Result.FAIL)
        if failed:
            return Readiness(verdict=Verdict.FAILED, blocking=failed)

        gated = tuple(s for s in live if s.result is Result.GATED)
        if gated:
            return Readiness(verdict=Verdict.GATED, blocking=gated)

        # 只看綁在交付物上的章。驗收標準本來就是另一份東西、另一條版本線，
        # 把它算進來的話，「有人訂了標準、有人交了東西」這個完全正常的狀態
        # 會被報成版本分岔。
        seen = tuple(
            sorted(
                {
                    s.content_digest
                    for s in self._stamps
                    if s.subject != SUBJECT_ACCEPTANCE and s.content_digest
                }
            )
        )
        missing = tuple(
            item
            for item in required
            if not any(
                s.role == item.role
                and s.subject == item.subject
                and s.kind is item.kind
                for s in live
            )
        )
        if missing:
            # 版本分岔比缺章更值得先講：章其實蓋過了，只是蓋在別版上。
            # 報成「缺章」會讓使用者再蓋一次，然後得到同樣的結果。
            stale = self.invalidated_by(versions)
            if stale and len(seen) > 1:
                return Readiness(
                    verdict=Verdict.SPLIT_VERSION,
                    missing=missing,
                    versions=seen,
                    blocking=stale,
                )
            return Readiness(verdict=Verdict.MISSING, missing=missing)

        return Readiness(verdict=Verdict.READY)

    @staticmethod
    def _self_review_role(live: tuple[Stamp, ...]) -> str:
        """訂驗收標準的跟做事的是不是同一個。

        定案原文：「引擎要檢查『驗收標準的章』與『實作的章』不是同一個角色
        蓋的，一樣就擋。」它防的不是「一定要有第二個 AI」——一人表格由使用者
        本人訂標準是合法的，因為使用者不是做事的那一個。防的是**自己出考卷
        自己考**。

        「實作的章」只算**機器章**。同一個 subject 上的審查章意思是「我看過
        這份實作」，那是檢查不是動手——而複查角色**本來就應該**同時訂標準
        又看過作者的東西，那正是它的工作。把兩者混在一起會讓正確的流程被
        判成違規，而使用者完全看不出為什麼。
        """
        acceptance = {s.role for s in live if s.subject == SUBJECT_ACCEPTANCE}
        did_the_work = {
            s.role
            for s in live
            if s.subject == SUBJECT_WORK and s.kind is StampKind.MACHINE
        }
        overlap = sorted(acceptance & did_the_work)
        return overlap[0] if overlap else ""


# 使用者本人。放在這裡而不是 lineup.py：那份列的是 AI 席位，而使用者不是
# 一個席位——他不會被派任務，但他蓋的章跟 AI 蓋的一樣算數。
ROLE_USER = "user"

# 程式本身。驗證那個章是「跑了專案宣告的指令、離開碼是這個」——**沒有任何
# 一個席位做了那件事**。
#
# 以前它掛在 `reviewer` 名下。節點名字寫死成四個的時候那還說得過去；名字改成
# 使用者自己取之後就會出事：陣容排成「發想／規劃／UI／實作／審閱」的專案裡
# 根本沒有一格叫 reviewer，於是拍板檢查會永遠說「缺一個 reviewer 的驗證章」，
# 而那一格不存在，補不出來。**一個永遠補不了的缺，跟一個假的通過一樣壞。**
ROLE_ENGINE = "engine"


STAMP_LOG_FILE = "stamps.jsonl"


class StampLog:
    """一次執行蓋過的章，一次執行一個檔。

    形狀刻意跟 `app/services/step_log.py` 一樣：append-only、一次執行一個
    檔、讀壞的那一行就跳過。**記帳失敗不准把執行弄死**——記帳存在的理由是
    讓失敗變便宜，把它變成一種新的失敗方式，比沒有它更糟。

    掉一行的代價是那一個章要重蓋，而那正是這個檔案存在之前的狀態。
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = Path(project_path).resolve()
        self.runs_dir = self.project_path / ".aicc" / "runs"

    def path_for(self, run_id: str) -> Path:
        return self.runs_dir / run_id / STAMP_LOG_FILE

    def append(self, run_id: str, stamp: Stamp) -> None:
        if not run_id:
            return
        try:
            path = self.path_for(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_as_row(stamp), ensure_ascii=False) + "\n")
        except Exception:
            return

    def read(self, run_id: str) -> ApprovalChain:
        """這次執行到目前為止的鏈子。

        讀不出來的行跳過，不是整份放棄：最實際的壞法是行程在寫到一半時被
        殺掉，那只該損失最後一個章。

        建不出 `Stamp` 的資料也跳過——一個違反規則的章（例如沒有證據的機器
        章）不准因為「它已經在檔案裡了」就取得豁免。**規則在讀的時候跟在寫
        的時候是同一套**，否則存檔就變成繞過檢查的路。
        """
        path = self.path_for(run_id)
        if not path.is_file():
            return ApprovalChain()

        chain = ApprovalChain()
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            stamp = _from_row(raw)
            if stamp is None:
                continue
            try:
                chain = chain.add(stamp)
            except ApprovalChainError:
                continue
        return chain


def _as_row(stamp: Stamp) -> dict:
    return {
        "stamp_id": stamp.stamp_id,
        "kind": stamp.kind.value,
        "role": stamp.role,
        "subject": stamp.subject,
        "content_digest": stamp.content_digest,
        "at": stamp.at,
        "evidence": stamp.evidence,
        "reasoning": stamp.reasoning,
        "result": stamp.result.value if stamp.result else None,
        "depends_on": list(stamp.depends_on),
    }


def _from_row(raw: dict) -> Stamp | None:
    try:
        result_raw = raw.get("result")
        return Stamp(
            stamp_id=str(raw.get("stamp_id", "")),
            kind=StampKind(str(raw.get("kind", ""))),
            role=str(raw.get("role", "")),
            subject=str(raw.get("subject", "")),
            content_digest=str(raw.get("content_digest", "")),
            at=str(raw.get("at", "")),
            evidence=str(raw.get("evidence", "")),
            reasoning=str(raw.get("reasoning", "")),
            result=Result(str(result_raw)) if result_raw else None,
            depends_on=tuple(
                str(item)
                for item in (raw.get("depends_on") or [])
            ),
        )
    except (ApprovalChainError, ValueError, TypeError):
        return None
