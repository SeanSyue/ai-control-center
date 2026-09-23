r"""把 AICC 跑的對話登記到廠商自己的清單裡。

## 這在解決什麼

AICC 用 `claude -p` 呼叫 CLI，CLI 自己會把逐字稿寫進
`~/.claude/projects/<專案路徑編碼>/<session>.jsonl`——**那部分一直都是對的**，
而且因為 AICC 用專案路徑當 cwd，專案分組跟兩個角色的對話分離也都已經成立。

缺的只有一件事：桌面版的側邊欄讀的是另一份索引

    %APPDATA%\Claude\claude-code-sessions\<org>\<workspace>\local_<uuid>.json

一段對話一個小檔案。AICC 從來沒寫過那裡，所以使用者在自己的 Claude 裡看不到
AICC 幫他跑的東西——他付錢買的會員，工作卻在他的軟體裡「不存在」。

## 為什麼這件事需要拍板

`SECURITY_MODEL.md` 第一段寫著「只走官方介面」。**這個檔案不是官方介面**：
格式是逆向出來的（AICC 的 `session_scanner` 早就在讀它，註解寫明是從 app
2.1.221 逆向的），沒有文件，廠商改版就會壞。

2026-08-21 老闆明確要求「在官方介面都要能看，同時側邊欄也要出現」，所以這條
是他鬆綁的，不是自己開的。

## 所以它必須做到一件事：**壞掉要看得見**

逆向出來的東西最糟的失敗方式不是壞掉，是**無聲地壞掉**——某次改版之後
AICC 照樣寫檔案、照樣沒有錯誤，而側邊欄就是不出現，然後沒有人知道。那跟
「這次沒跑」在畫面上一模一樣。

所以 `register()` 寫完之後會**把檔案讀回來、比對關鍵欄位**，對不上就回報
`problem`。它從不丟例外——登記失敗不該弄死一次真的執行——但它也絕不假裝
成功。
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from app.i18n import t

# 這幾個欄位是「這一筆到底指向哪一段對話」的答案。讀回來的時候只比對它們：
# 廠商多加一個欄位不該被讀成失敗，但這幾個對不上就是真的沒登記到。
_IDENTIFYING_FIELDS = ("cliSessionId", "cwd", "title")


@dataclass(frozen=True)
class Registration:
    """登記的結果。`ok` 為假的時候 `problem` 是寫給人看的一句話。"""

    ok: bool
    problem: str = ""
    path: str = ""

    @property
    def summary(self) -> str:
        if self.ok:
            return t("vendor.registered")
        return f"沒有登記到 Claude 的對話清單：{self.problem}"


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class ClaudeSidebarIndex:
    """桌面版側邊欄那份清單。

    `index_root` 可以指定，預設從 `%APPDATA%` 推。指定得出來是為了測試——
    這個類別**必須**能在不碰使用者真實資料夾的情況下被完整測過，那是 2026-08-21
    那次誤刪之後的硬規矩。
    """

    FOLDER = "claude-code-sessions"

    def __init__(self, index_root: Path | None = None) -> None:
        if index_root is not None:
            self.index_root = Path(index_root)
        else:
            appdata = os.environ.get("APPDATA", "")
            self.index_root = (
                Path(appdata) / "Claude" / self.FOLDER if appdata else Path()
            )

    # --- 找地方 -------------------------------------------------------------

    def workspace_dir(self) -> Path | None:
        """`<org>/<workspace>` 那一層，找不到就回 None。

        **刻意不自己建。**那兩層是桌面版自己的識別碼，AICC 猜一組出來只會
        產生一個沒有人會讀的資料夾——而且看起來像是成功了。找不到就老實說
        「請先用桌面版開一次」。
        """
        if not self.index_root.is_dir():
            return None
        candidates = [p for p in self.index_root.glob("*/*") if p.is_dir()]
        if not candidates:
            return None
        # 有好幾個的時候挑檔案最多的：那是使用者實際在用的那一個。
        return max(candidates, key=lambda d: len(list(d.glob("local_*.json"))))

    def _existing_entry(self, workspace: Path, cli_session_id: str) -> Path | None:
        for path in sorted(workspace.glob("local_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                if str(data.get("cliSessionId", "")).strip() == cli_session_id:
                    return path
        return None

    # --- 登記 ---------------------------------------------------------------

    def register(
        self,
        cli_session_id: str,
        cwd: Path | str,
        title: str,
        model: str = "",
        created_ms: int | None = None,
    ) -> Registration:
        """登記一段對話，或更新已經登記過的那一筆。

        同一段對話重複呼叫會**更新**而不是再長一筆：AICC 一次執行會對同一個
        席位派好幾次工，每次都新增的話，使用者的側邊欄會被同一個任務洗版。
        """
        cli_session_id = (cli_session_id or "").strip()
        if not cli_session_id:
            return Registration(False, t("vendor.no_session_id"))

        workspace = self.workspace_dir()
        if workspace is None:
            return Registration(
                False,
                t("vendor.folder_missing"),
            )

        now = _now_ms()
        existing = self._existing_entry(workspace, cli_session_id)
        if existing is not None:
            try:
                entry = json.loads(existing.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                entry = {}
            target = existing
        else:
            entry = {}
            target = workspace / f"local_{uuid.uuid4()}.json"

        entry.update(
            {
                "sessionId": entry.get("sessionId") or target.stem,
                "cliSessionId": cli_session_id,
                "cwd": str(cwd),
                "originCwd": entry.get("originCwd") or str(cwd),
                "createdAt": entry.get("createdAt") or (created_ms or now),
                "lastActivityAt": now,
                "lastFocusedAt": entry.get("lastFocusedAt") or now,
                "title": title.strip() or cli_session_id,
                # 「auto」＝程式取的名字。標成 user 會讓桌面版以為使用者親手
                # 打過這個標題，然後就不會再自動更新它。
                "titleSource": "auto",
                "isArchived": False,
            }
        )
        if model:
            entry["model"] = model

        try:
            target.write_text(
                json.dumps(entry, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            return Registration(False, f"寫不進去：{exc}", str(target))

        return self._verify(target, entry)

    def _verify(self, target: Path, expected: dict) -> Registration:
        """寫完讀回來比對。

        這是這個檔案存在的一半理由。逆向出來的格式最糟的失敗方式不是壞掉，
        是**無聲地壞掉**：照樣寫、照樣沒有錯誤、而側邊欄就是不出現。讀回來
        比對至少能抓到「檔案沒真的落地」與「寫進去的東西不是我以為的東西」。

        它抓不到的是「格式對但廠商已經改讀別的地方」——那個只有使用者打開
        桌面版才會發現。所以 `Registration.problem` 要被顯示出來，不是被
        吞掉。
        """
        try:
            written = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return Registration(False, f"寫完之後讀不回來：{exc}", str(target))

        if not isinstance(written, dict):
            return Registration(False, t("vendor.not_a_record"), str(target))

        mismatched = [
            field
            for field in _IDENTIFYING_FIELDS
            if str(written.get(field, "")) != str(expected.get(field, ""))
        ]
        if mismatched:
            return Registration(
                False,
                t("vendor.readback_mismatch") + "、".join(mismatched),
                str(target),
            )

        return Registration(True, "", str(target))
