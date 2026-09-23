"""Pure parsing/normalization helpers used by Orchestrator.

These functions take explicit arguments and depend only on the text/JSON
that Claude or Codex already returned - no Orchestrator instance state,
no CLI subprocess calls, no filesystem access. Kept in their own module
so they can be unit tested directly without constructing an Orchestrator
or mocking a CLI runner.
"""

from __future__ import annotations

from app.core import prompt_markers

from app.i18n import t

import json
import re
from dataclasses import dataclass

from app.adapters.cli_runner import CliResult
from app.core.prompt_builder import (
    APPROVE_NO,
    APPROVE_OK,
    CONSULT,
    INTAKE_ASK,
    INTAKE_SETTLED,
    REJECT_CRITERIA,
)


# The Decision Gate's floor, as a check rather than as a request.
#
# `ai_settings.GATE_FLOOR` names four things Codex is *told* to escalate, and
# `gate_category_text` builds them into the prompt. That is advisory: it works
# by Codex agreeing. These patterns are the same floor enforced on the way
# back - if Codex returns CONTINUE and its own instruction says to force-push,
# the run stops for a person regardless of what it decided (ROADMAP P3
# 「在自動批准前加入更強的 secret／刪除／系統命令檢查」).
#
# **Deliberately specific, not broad.** A veto that fires on the word 「刪除」
# would fire on half of this user's tasks, and a gate that always stops is a
# gate people click through without reading - which is worse than no gate,
# because it looks like oversight. So these match commands and unambiguous
# imperatives, not topics: 「討論刪除功能的設計」 passes, 「rm -rf」 does not.
#
# Each entry is (label, pattern). The label is what the audit log records, so
# it has to say which rule fired, not just that one did.
GATE_VETO_PATTERNS: tuple[tuple[str, str], ...] = (
    ("veto.destructive_delete", r"\brm\s+-[a-z]*[rf]"),
    ("veto.destructive_delete", r"\bRemove-Item\b[^\n]*-Recurse"),
    ("veto.database_drop", r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b"),
    ("veto.database_wipe", r"\bDELETE\s+FROM\b(?![^\n]*\bWHERE\b)"),
    ("veto.rewrite_history", r"\bgit\s+(reset\s+--hard|rebase|filter-repo|filter-branch)\b"),
    ("veto.force_push", r"\bgit\s+push\b[^\n]*(--force|-f)\b"),
    ("veto.system_settings", r"\b(reg\s+add|reg\s+delete|Set-ItemProperty\s+-Path\s+HK)"),
    ("veto.system_settings", r"\b(sc\s+config|Set-Service|schtasks\s+/create)\b"),
    ("veto.permissions", r"\b(icacls|takeown|chmod\s+[0-7]{3}|chown)\b"),
)

# Roles that may not be waived by 「Codex 說沒問題」 even when no pattern
# matched, because the evidence is a secret rather than a phrasing.
GATE_VETO_SECRET_LABEL = "veto.secrets"

# 機密檢查自己壞掉的時候回這一句。**寫死不走 `t()`**——壞掉的那個東西
# 有可能就是 i18n，而在報告故障的路上再掉進同一個洞，等於什麼都沒報。
#
# 內容講的是「沒有人在看」，不是「有問題」：那兩件事要他做的下一步不同。
SECRET_CHECK_BROKE = "機密檢查沒有跑成功，所以這一輪沒有人在看有沒有金鑰或密碼外洩——請你自己確認一次再放行。"


@dataclass(frozen=True)
class BlockedInstruction:
    """使用者交辦裡的一句話，以及擋下它的那一條護欄。

    兩邊都**驗證過**才會有這個物件存在：`quote` 逐字出現在任務原文裡，
    `rule_text` 逐字等於 `prompt_builder.GUARDRAILS` 裡的一句。
    驗不過的整組在 `blocked_instructions()` 裡就被丟掉了，不會走到這裡。
    """

    quote: str
    rule_id: str
    rule_text: str


# 一句話短到這個長度以下就不算證據。
#
# 「。」是任務裡的子字串、「的」也是——照抄一個字就能通過驗證的話，
# 「逐字比對」這件事等於沒有做。四個字是一個**很寬**的下限（它擋掉的只有
# 標點與單字），寬在這裡是對的：這個門檻的用途是擋掉退化的比對，不是
# 判斷句子有沒有意義。
BLOCKED_QUOTE_MIN_CHARS = 4


def _folded(text: str) -> str:
    """折疊空白之後的樣子。**比對只放寬到這裡為止。**"""
    return " ".join(text.split())


def blocked_instructions(task: str, output: str) -> tuple[BlockedInstruction, ...]:
    """AI 說它照護欄擋掉了使用者的哪幾句話——**而且驗證過**。

    2026-08-27 拍板（❓15）的第二半：「畫面要明說哪一句被擋下來」。

    ## 這裡為什麼不是在猜

    被否決過的做法是**寫關鍵字去猜使用者的意圖**（`if "commit" in task`）：
    沒有樣本、猜錯會很有自信地說錯話，而「不要 commit」跟「請先 commit」
    在關鍵字眼裡一模一樣。

    這裡反過來走：**AI 講，引擎驗。**提示詞（`prompt_builder.RULES_WIN`）
    規定了一個固定格式，這一支照那個格式讀——讀的是**AICC 自己規定的形狀**，
    跟 `ASK_USER:`、`退給：／理由：` 同一種東西，不是在解讀自然語言。

    然後兩件事都要對得上，缺一整組丟掉：

    1. `你寫的` 那一句要**逐字出現在任務原文裡**（只折疊空白）。
       編出來的句子過不了這一關。
    2. `擋它的規則` 要**逐字等於**我們自己寫進提示詞的某一條護欄。
       編出來的規則過不了這一關。

    ## 漏掉比說錯好

    AI 沒有寫那一段的時候，這裡回空的——使用者少看到一件事。
    反過來（顯示一件沒發生的事）會讓他去改一個他根本沒寫錯的句子，
    而且會讓他從此不信這一列。**所以驗不過就丟，不留一個「可能是」。**
    """
    from app.core.prompt_builder import guardrail_for

    folded_task = _folded(task)
    if not folded_task:
        return ()

    found: list[BlockedInstruction] = []
    seen: set[tuple[str, str]] = set()
    pattern = re.compile(
        re.escape(prompt_markers.BLOCKED_OPEN)
        + r"(?P<body>.*?)"
        + re.escape(prompt_markers.BLOCKED_CLOSE),
        re.DOTALL,
    )
    for match in pattern.finditer(output or ""):
        body = match.group("body")
        quote_match = re.search(
            prompt_markers.BLOCKED_QUOTE_PATTERN, body, re.MULTILINE
        )
        rule_match = re.search(
            prompt_markers.BLOCKED_RULE_PATTERN, body, re.MULTILINE
        )
        if quote_match is None or rule_match is None:
            continue
        quote = _folded(quote_match.group("quote"))
        if len(quote) < BLOCKED_QUOTE_MIN_CHARS:
            continue
        if quote not in folded_task:
            continue
        rule = guardrail_for(rule_match.group("rule"))
        if rule is None:
            continue
        key = (quote, rule.rule_id)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            BlockedInstruction(
                quote=quote,
                rule_id=rule.rule_id,
                rule_text=_folded(rule_match.group("rule")),
            )
        )
    return tuple(found)


def gate_veto(question: str, answer: str = "", reason: str = "") -> str:
    """Why this CONTINUE must become ASK_USER, or "" to let it through.

    Reads the question **and** Codex's own answer, because the answer is the
    instruction about to be handed to Claude: a gate that only inspected the
    question would miss 「問：要怎麼清掉舊分支？」 answered with a force-push.

    Returns the label named by `GATE_VETO_PATTERNS`, or "". Never raises - a broken
    veto must not take down a run it was only supposed to watch.
    """
    import re

    haystack = "\n".join(part for part in (question, answer, reason) if part)
    if not haystack.strip():
        return ""

    # 那張表存的是 key（模組層存字會在 import 的當下凍住）。
    # **回的是取出來的那一句**——這個值會寫進決策紀錄給人看。
    for label_key, pattern in GATE_VETO_PATTERNS:
        try:
            if re.search(pattern, haystack, re.IGNORECASE):
                return t(label_key)
        except re.error:
            continue

    # **掃描的結果先算完，再進 try。**2026-08-24 之前 `t(...)` 那一行在
    # try 裡面，於是「掃到機密了、只是標籤取不到」會跟「掃描壞掉」一起
    # 被吞成放行——一個**已經驗出機密**的結果被 except 吃掉。
    try:
        from app.services.security_scan import SecurityScanner

        found_secret = SecurityScanner().scan_text(haystack).has_blocking_findings
    except Exception:
        # **失敗關閉。**前面九條規則遇到壞掉的樣式是 `continue`（那一條不算，
        # 其他八條照跑）；這一條沒有別人接手，所以它壞掉的意思是
        # **這一輪沒有人在看機密**——那不能靜靜地變成放行。
        #
        # 這句話**寫死，不走 `t()`**：如果壞掉的正好是 i18n 本身，
        # 走 `t()` 會在報告故障的路上再掉進同一個洞。
        return SECRET_CHECK_BROKE
    if found_secret:
        return t(GATE_VETO_SECRET_LABEL)
    return ""


def should_skip_memory_maintenance(changed_files: list[str]) -> bool:
    normalized = [item.replace("\\", "/") for item in changed_files if item]
    if not normalized:
        return False

    low_impact_prefixes = (
        "tests/",
        "docs/",
        "_update_backup/",
        ".tmp.driveupload/",
    )
    low_impact_files = {
        "ROADMAP.md",
        "pytest.ini",
    }
    low_impact_suffixes = (
        ".md",
        ".txt",
        ".rst",
    )
    return all(
        item.startswith(low_impact_prefixes)
        or item in low_impact_files
        or item.endswith(low_impact_suffixes)
        for item in normalized
    )


def normalize_claude_result(result: CliResult) -> CliResult:
    result.raw_stdout = result.stdout
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return result

    if not isinstance(payload, dict):
        return result

    result.events = [payload]
    result.session_id = str(
        payload.get("session_id") or payload.get("sessionId") or ""
    ).strip()

    for key in ("result", "text", "output", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            result.stdout = value.strip()
            break
        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str) and text.strip():
                result.stdout = text.strip()
                break
    return result


def normalize_codex_result(result: CliResult) -> CliResult:
    result.raw_stdout = result.stdout
    events: list[dict] = []
    final_messages: list[str] = []
    session_id = ""

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        if event.get("type") == "thread.started":
            session_id = str(event.get("thread_id", "")).strip()
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
        ):
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                final_messages.append(text.strip())

    if events:
        result.events = events
        result.session_id = session_id
        if final_messages:
            result.stdout = final_messages[-1]
    return result


# Money the CLI reported itself. Transcribing a vendor's own figure is allowed;
# multiplying tokens by a price list we maintain is not - see ROADMAP P12
# 「用量與花費怎麼呈現」. Looked for at the event's top level as well as inside
# `usage`, because it is not yet verified where each CLI puts it.
COST_KEYS = ("total_cost_usd",)


def extract_usage_readings(
    result: CliResult,
    *,
    provider: str,
    event_type: str,
    session_id: str,
) -> list[dict]:
    """What the CLI itself said this call used - transcribed, never interpreted.

    Every scalar under `usage` is copied out under the name the CLI gave it.
    Nothing is added to anything else, and that is deliberate: two things about
    these numbers are unverified, and they need opposite arithmetic.

    1. Is `cached_input_tokens` part of `input_tokens` or extra? Our one real
       sample reads input=16931 / cached=5504 - 25% apart depending on which.
    2. Is `usage` this turn's, or the session's running total? openai/codex#17539
       reports the latter ("cumulative session token totals", 6.9M input against
       a 272K window). If it is cumulative, summing our calls double-counts; if
       it is per-turn, taking the last one under-counts.

    So no total is computed here or anywhere downstream. Each reading is stored
    with the call and session it came from, which is correct under either
    reading, and the aggregate can be written once one real run with a reused
    session settles it (see ROADMAP P12 for the recipe).
    """
    readings: list[dict] = []

    for event in result.events:
        if not isinstance(event, dict):
            continue

        numbers: dict[str, float] = {}
        unread: list[str] = []

        usage = event.get("usage")
        if isinstance(usage, dict):
            for key, value in usage.items():
                # bool is an int subclass, and a flag is not a token count.
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    unread.append(str(key))
                else:
                    numbers[str(key)] = value

        for key in COST_KEYS:
            value = event.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            numbers.setdefault(key, value)

        if not numbers:
            continue

        readings.append(
            {
                "provider": provider,
                "event_type": event_type,
                "event": str(event.get("type") or ""),
                "session_id": session_id,
                "numbers": numbers,
                "unread_keys": sorted(unread),
            }
        )

    return readings


def parse_claude_question(output: str) -> str:
    marker = "ASK_USER:"
    if marker not in output:
        return ""

    question = output.split(marker, 1)[1]
    if "END_ASK_USER" in question:
        question = question.split("END_ASK_USER", 1)[0]
    return question.strip()


def parse_review(codex_output: str) -> dict:
    try:
        data = json.loads(codex_output)

        if not isinstance(data, dict):
            raise ValueError("Codex output is not a JSON object.")

        status = str(data.get("status", "")).upper()

        if status not in {"PASS", "FIX", "ASK_USER"}:
            raise ValueError("Codex returned an invalid status.")

        issues = data.get("issues", [])
        instructions = data.get("instructions", [])

        if not isinstance(issues, list):
            issues = [str(issues)]

        if not isinstance(instructions, list):
            instructions = [str(instructions)]

        graded = [_grade_issue(item) for item in issues]

        return {
            "status": status,
            "summary": str(data.get("summary", "")),
            # 純文字，給既有的呼叫端（提示詞、畫面、紀錄）照舊用。
            "issues": [item["what"] for item in graded],
            # 帶等級的那一份。**只有須處理級會讓這一輪重做。**
            "graded_issues": graded,
            "blocking_issues": [
                item["what"] for item in graded if item["level"] == MUST_FIX
            ],
            "note_issues": [
                item["what"] for item in graded if item["level"] == FOR_INFO
            ],
            "instructions": [str(item) for item in instructions],
            # 【駁回直接送給該負責的人】：駁回一定要指名一個 node。
            # 空字串代表沒指名——呼叫端要把那當成無效回覆，**不准自己挑
            # 一格代打**，那就變回「退回上一關」了。
            "target": str(data.get("target", "")).strip(),
            "question": str(data.get("question", "")),
        }

    except (json.JSONDecodeError, ValueError, TypeError):
        return {
            "status": "UNKNOWN",
            "summary": t("parse.review_unreadable"),
            "issues": [],
            "graded_issues": [],
            "blocking_issues": [],
            "note_issues": [],
            "instructions": [],
            "target": "",
            "question": "",
        }


def parse_review_status(codex_output: str) -> str:
    return parse_review(codex_output)["status"]


def parse_decision_gate(output: str) -> dict:
    """The gate's verdict, plus `parsed` saying whether it was really read.

    `question` on an unparsed result is the raw CLI output, kept because
    showing it beats showing nothing. But it is *raw machine output*, and the
    caller has to be able to tell it apart from a question somebody wrote -
    the decision banner puts this string in front of the user as
    「需要你的判斷：…」, and a stack trace presented that way is worse than the
    clean question Claude actually asked.

    That is what `parsed` is for. Without it the only way to tell was to match
    on the Chinese text of `reason`, which is not something a caller should be
    doing.
    """
    try:
        data = json.loads(_unfenced(output))
    except (json.JSONDecodeError, TypeError):
        return {
            "decision": "ASK_USER",
            "reason": t("parse.gate_unreadable"),
            "answer": "",
            # **Not** the raw output. When the gate's reply cannot be parsed we
            # do not know what it wanted to ask - and the caller still holds the
            # real question, which `decision.get("question") or question` then
            # uses. Putting the raw text here made it win that fallback, so a
            # machine marker went into the decision log and out to the audit
            # page: 「ASK_USER: 要覆寫 demo.py 嗎？」 with the marker still on it.
            # Same failure as the decision banner showing raw CLI output, one
            # layer further from anyone who would notice.
            "question": "",
            "parsed": False,
        }

    if not isinstance(data, dict):
        return {
            "decision": "ASK_USER",
            "reason": t("parse.gate_not_object"),
            "answer": "",
            "question": str(data),
            "parsed": False,
        }

    decision = str(data.get("decision", "")).upper()
    if decision not in {"APPROVED", "REJECTED", "ASK_USER"}:
        decision = "ASK_USER"

    return {
        "decision": decision,
        "reason": str(data.get("reason", "")),
        "answer": str(data.get("answer", "")),
        "question": str(data.get("question", "")),
        "parsed": True,
    }


_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.S)


def _unfenced(output: str) -> str:
    """剝掉 ```json 圍籬。**那才是最常見的輸出形狀。**

    不剝的話，一個內容完全正確的 JSON 會走進「讀不懂」那一支——
    而在 2026-08-24 之前，那一支的意思是「當成通過」。
    """
    if not isinstance(output, str):
        return output
    match = _FENCE.match(output.strip())
    return match.group(1) if match else output


def parse_memory_review(output: str) -> dict:
    """複查那一格對「記憶要不要這樣改」的回覆。

    ## `parsed` 那一格是 2026-08-24 補的，理由很具體

    在那之前，讀不懂就回 `status="PASS"`——**跟一個真的通過審查的回覆
    形狀一模一樣**。呼叫端看不出差別，於是把 Codex 那份**沒有人審過**的
    草稿整檔寫進八份共同記憶（含 `SECURITY_MODEL.md` 與 `OPEN_QUESTIONS.md`），
    畫面上收 COMPLETED。

    最刺的是**同一支檔案的 `parse_decision_gate` 對完全相同的情況回
    `ASK_USER`**——兩個 parser 並排寫著，一個 fail-closed 一個 fail-open。

    `parsed=False` 不直接翻成 `ASK_USER`，是因為那兩件事要分得開：
    「AI 說要問人」跟「我讀不懂 AI 說什麼」在畫面上要講不同的話。
    """
    try:
        data = json.loads(_unfenced(output))
    except (json.JSONDecodeError, TypeError):
        return {
            "status": "PASS",
            "summary": t("parse.memory_no_json"),
            "memory_update": "",
            "question": "",
            "parsed": False,
        }

    if not isinstance(data, dict):
        return {
            "status": "PASS",
            "summary": t("parse.memory_not_object"),
            "memory_update": "",
            "question": "",
            "parsed": False,
        }

    status = str(data.get("status", "PASS")).upper()
    if status not in {"PASS", "ASK_USER"}:
        status = "PASS"

    return {
        "status": status,
        "summary": str(data.get("summary", "")),
        "memory_update": str(data.get("memory_update", "")),
        "question": str(data.get("question", "")),
        "parsed": True,
    }


# 需求確認那一步的兩個開頭。從 `prompt_builder` 進來——**格式的定義只有一個，
# 就在要求它的那句話旁邊**。抄一份到這裡的話，改了提示詞而忘記改這裡，引擎
# 會把一個問題讀成一份定案的驗收標準，然後照著它去派工。
_INTAKE_ASK_RE = re.compile(
    r"^[ \t]*" + re.escape(INTAKE_ASK.rstrip("：")) + r"[：:]",
    re.MULTILINE,
)
_INTAKE_SETTLED_RE = re.compile(
    r"^[ \t]*" + re.escape(INTAKE_SETTLED.rstrip("：")) + r"[：:]",
    re.MULTILINE,
)


def parse_intake(output: str) -> dict:
    """需求確認那一格這一輪講了什麼。

    只有三種結果，而且**第三種不准被猜成前兩種**：

    * 還要問 → `{"valid": True, "settled": False, "question": ...}`
    * 問清楚了 → `{"valid": True, "settled": True, "criteria": ...}`
    * 兩個開頭都寫了、或者都沒寫 → `{"valid": False, ...}`

    為什麼不猜：猜成「還要問」會把一坨命令列輸出丟到使用者臉上；猜成「定案」
    會拿一個問句當驗收標準，而後面每一格都照它做。兩種都是**看起來很確定的
    錯誤**，而使用者當下分不出來。

    開頭必須自成一行（`^`）。整份提示詞裡本來就寫著這兩個詞（那是在教它格式），
    模型如果順手引用了一句，用「有沒有出現過」判斷就會誤判。
    """
    text = output or ""
    ask = _INTAKE_ASK_RE.search(text)
    settled = _INTAKE_SETTLED_RE.search(text)

    if bool(ask) == bool(settled):
        both = t("parse.both_prefixes") if ask else t("parse.neither_prefix")
        return {
            "valid": False,
            "settled": False,
            "question": "",
            "criteria": "",
            "problem": (
                f"需求確認那一格{both}——引擎分不出它是在問問題，"
                f"還是在交出驗收標準。要嘛用「{INTAKE_ASK}」開頭問一題，"
                f"要嘛用「{INTAKE_SETTLED}」開頭把標準寫完。"
            ),
        }

    if ask is not None:
        return {
            "valid": True,
            "settled": False,
            "question": text[ask.end():].strip(),
            "criteria": "",
            "problem": "",
        }

    return {
        "valid": True,
        "settled": True,
        "question": "",
        "criteria": text[settled.end():].strip(),
        "problem": "",
    }


_REJECT_CRITERIA_RE = re.compile(
    r"^[ \t]*" + re.escape(REJECT_CRITERIA.rstrip("：")) + r"[：:]",
    re.MULTILINE,
)


def parse_criteria_rejection(output: str) -> str:
    """執行的那一格有沒有退回驗收標準；有的話，理由是什麼。

    老闆 2026-08-22 問的：「如果當成絕對的驗收標準的話**可能會因為執行上的
    問題而任務失敗**　有其他方法嗎?」定案是**駁回給訂標準的那一格**——不是
    讓執行的自己改標準（它會改成自己剛好做得到的），也不是讓整件事失敗。

    開頭必須自成一行。提示詞裡本來就寫著這個詞（那是在教它怎麼退回），用
    「有沒有出現過」判斷的話，模型順手引用一句就會被讀成駁回，而整條線會
    回頭重訂一份根本沒有人反對的標準。

    回傳理由字串；沒有駁回就是空字串。**理由是空的也算沒有駁回**——一個
    沒有理由的退回，訂標準的那一格拿到之後只能猜，而猜錯要再退一次。
    """
    text = output or ""
    hit = _REJECT_CRITERIA_RE.search(text)
    if hit is None:
        return ""
    return text[hit.end():].strip()


_CONSULT_RE = re.compile(
    r"^[ \t]*" + re.escape(CONSULT.rstrip("：")) + r"[：:][ \t]*(?P<who>[^\r\n]*)",
    re.MULTILINE,
)


def _seat_named(who: str, seats: tuple[str, ...] | list[str]) -> str:
    """`who` 指的是哪一格，**用存檔的那個寫法回**。找不到回空字串。

    ## 為什麼要有這一支

    座位名在 `AgentSlot.__post_init__` 會被壓成小寫（`lineup.py`），而
    **模型回的字是原樣的**——使用者把一格取名 NODE2、提示詞裡也印著他打的
    那個字，模型回「找【NODE2】討論」，而 `seats` 裡是 `node2`。

    2026-08-25 之前這裡是 `who not in seats`，於是那一句被判成「它指名的
    人不在這條工作線上」。**畫面上會說模型指了一個不存在的人，而模型指對了。**

    回傳存檔的寫法（不是使用者打的寫法）是刻意的：下游要拿它去派工、蓋章、
    查對話，那幾張表的鍵都是小寫的。
    """
    target = who.strip().lower()
    for seat in seats:
        if seat.strip().lower() == target:
            return seat
    return ""


def parse_consult_request(output: str, seats: tuple[str, ...] = ()) -> dict:
    """這一格有沒有要找別人討論；有的話，找誰、問什麼。

    `seats` 是這個專案的名冊。指名一格不存在的名字**不是「沒有要討論」，是
    一個無效回覆**——兩者差很多：前者會安靜地繼續往下走，等於那一格的疑問
    被丟掉了，而它接下來寫的東西建立在一個沒有人回答的假設上。

    回傳 `{"asking": bool, "who": str, "question": str, "problem": str}`。
    `problem` 非空就是無效回覆，引擎要停下來說清楚，**不准替它挑一個對象**
    ——挑了就變回「問上一關」，那正是【駁回直接送給該負責的人】否決的形狀。
    """
    text = output or ""
    hit = _CONSULT_RE.search(text)
    if hit is None:
        return {"asking": False, "who": "", "question": "", "problem": ""}

    who = hit.group("who").strip().strip("【】[]").strip()
    question = text[hit.end():].strip()

    if not who:
        return {
            "asking": True,
            "who": "",
            "question": question,
            "problem": (
                t("parse.consult_without_target")
            ),
        }
    if not question:
        return {
            "asking": True,
            "who": who,
            "question": "",
            "problem": (
                f"它說要找【{who}】討論，但沒有寫要問什麼。"
                "被問的那一格拿到一個空問題，只能猜。"
            ),
        }
    matched = _seat_named(who, seats) if seats else who
    if seats and not matched:
        return {
            "asking": True,
            "who": who,
            "question": question,
            "problem": (
                f"它指名的【{who}】不在這個專案的工作線上。"
                f"線上有的是：{'、'.join(seats)}。"
            ),
        }
    # **回存檔的寫法，不是他打的寫法。**下游要拿它去派工、蓋章、查對話，
    # 那幾張表的鍵都是小寫的；回原樣的話比對過了、派工那一步照樣找不到人。
    return {"asking": True, "who": matched, "question": question, "problem": ""}


# 這兩個開頭**不是**以冒號結尾（是「拍板：可以」），所以不能照抄上面那幾個
# 的寫法「去掉尾巴的冒號再補一個回來」——那會變成要求「可以」後面還要有一個
# 冒號，於是每一種回覆都被判成「兩種都沒寫」，而那個判定看起來完全合理。
# 實際踩過。
_APPROVE_OK_RE = re.compile(
    r"^[ \t]*" + re.escape(APPROVE_OK).replace("：", "[：:]"), re.MULTILINE
)
_APPROVE_NO_RE = re.compile(
    r"^[ \t]*" + re.escape(APPROVE_NO).replace("：", "[：:]"), re.MULTILINE
)
_SEND_BACK_RE = re.compile(prompt_markers.REJECT_TARGET, re.MULTILINE)
_REASON_RE = re.compile(prompt_markers.REJECT_REASON, re.MULTILINE | re.DOTALL)


def parse_approval(output: str, seats: tuple[str, ...] = ()) -> dict:
    """最後那一格看完所有的章之後說什麼。

    三種結果，第三種**不准被猜成前兩種**：

    * 可以拍板 → `{"decided": True, "approved": True, "reasoning": ...}`
    * 不可以，退給某一格 → `{"decided": True, "approved": False, "who": ...}`
    * 看不懂 → `{"decided": False, "problem": ...}`

    「可以」**一定要有理由**。一個沒有理由的「我同意」，事後回溯的時候回答
    不了最重要的那一題：憑什麼。這裡把它當成無效回覆而不是通過——蓋不出章
    的話，鏈子上會少一個，而那比一個空洞的章誠實。

    `APPROVE_NO` 先比：「拍板：不可以」裡面**包含**「拍板：可以」不是子字串
    （中間隔著「不」），所以其實不會互相誤判；先比它只是讓意圖看得出來。
    """
    text = output or ""
    no = _APPROVE_NO_RE.search(text)
    ok = _APPROVE_OK_RE.search(text)

    if bool(no) == bool(ok):
        both = t("parse.both_kinds") if no else t("parse.neither_kind")
        return {
            "decided": False,
            "approved": False,
            "who": "",
            "reasoning": "",
            "problem": (
                f"核准那一格{both}——引擎分不出它是同意還是不同意。"
                f"要嘛用「{APPROVE_OK}」開頭，要嘛用「{APPROVE_NO}」開頭。"
            ),
        }

    reason_hit = _REASON_RE.search(text)
    reasoning = reason_hit.group("why").strip() if reason_hit else ""

    if ok is not None:
        if not reasoning:
            return {
                "decided": False,
                "approved": False,
                "who": "",
                "reasoning": "",
                "problem": (
                    t("parse.approve_without_reason")
                ),
            }
        return {
            "decided": True,
            "approved": True,
            "who": "",
            "reasoning": reasoning,
            "problem": "",
        }

    back = _SEND_BACK_RE.search(text)
    who = back.group("who").strip().strip("【】[]").strip() if back else ""
    if not who:
        return {
            "decided": False,
            "approved": False,
            "who": "",
            "reasoning": reasoning,
            "problem": (
                t("parse.reject_without_target")
            ),
        }
    matched = _seat_named(who, seats) if seats else who
    if seats and not matched:
        return {
            "decided": False,
            "approved": False,
            "who": who,
            "reasoning": reasoning,
            "problem": (
                f"核准那一格要退給【{who}】，但這條工作線上沒有那一格。"
                f"線上有的是：{'、'.join(seats)}。"
            ),
        }
    return {
        "decided": True,
        "approved": False,
        # 同上：退回去那一格要用存檔的寫法，不然「退給它」找不到人。
        "who": matched,
        "reasoning": reasoning or t("parse.no_reason_given"),
        "problem": "",
    }


# 異議兩級（2026-08-21 定案）。
#
# 老闆的原話：「如果這項決議沒有重要到去中斷的話　這樣不合邏輯」。
#
# 只有一種異議的話，任何一句「順帶一提」都會讓整件事重跑一輪；而如果為了
# 避免那件事就把它們全部當成不重要，那審查等於沒有牙齒。
# 這兩個字**是提示詞叫 Codex 寫的**，所以它們住在 `prompt_markers`
# ——引擎照它回的字分級，翻掉就分不出來了。
MUST_FIX = prompt_markers.MUST_FIX
FOR_INFO = prompt_markers.FOR_INFO

_LEVEL_WORDS = {
    MUST_FIX: MUST_FIX,
    "must_fix": MUST_FIX,
    "blocking": MUST_FIX,
    FOR_INFO: FOR_INFO,
    "for_info": FOR_INFO,
    "note": FOR_INFO,
    "info": FOR_INFO,
}


def _grade_issue(raw) -> dict:  # noqa: ANN001
    """把一條意見讀成 `{"level": ..., "what": ...}`。

    **沒有標級的一律算須處理。**分不出來的時候，停下來問人比默默放行便宜——
    而且反過來的預設值會讓「忘記標級」跟「這件事不重要」變成同一件事，
    那正是這兩級要拆開的原因。

    舊格式（一串純文字）照樣讀得進來：那些都是還沒有等級的年代寫的，全部
    當成須處理，跟它們當時的行為一致。
    """
    if isinstance(raw, dict):
        what = str(raw.get("what") or raw.get("issue") or raw.get("text") or "")
        level = _LEVEL_WORDS.get(str(raw.get("level", "")).strip().lower())
        if level is None:
            level = _LEVEL_WORDS.get(str(raw.get("level", "")).strip())
        return {"level": level or MUST_FIX, "what": what or str(raw)}
    return {"level": MUST_FIX, "what": str(raw)}
