"""介面文字住的地方。**這一輪不翻譯，只做結構與守衛。**

定案（2026-08-22）：

> 【先做結構與守衛，翻譯之後做】介面文字全部挖到一個地方，並且加一支
> 「新寫死的中文就變紅」的檢查；**這一輪不翻譯**。
>
> 否決：現在整套翻成英文——**介面文字每天在改，翻完就過期**。
> 也否決「先不管」——今天約 1,500 段中文字串寫死在 35 個檔案裡，
> 每多做一週就多幾百段，這件事只會越來越貴。
>
> 不變式：**翻譯會過期，結構不會。**

2026-08-24 實際量到的是 **1,724 段、64 個檔案**（用語法樹數，註解與
docstring 不算——這個專案刻意用大量中文寫說明，那些不是介面文字）。

## 所以這一輪做的是止血，不是搬家

一次搬 1,724 段是一次巨大的機械改動，而它每一段都可能改錯一個字。
真正要緊的是**它不會變成 2,000 段**：

* 這個檔案是新程式碼放字串的地方；
* `tests/test_no_new_hardcoded_chinese.py` 是那支棘輪——
  每個檔案的既有段數記在 `tools/i18n_baseline.json`，**只准少不准多**。

搬家可以任何一天做，而且做一半也不會壞。**止血只有現在做才有用。**

## 為什麼查不到的 key 是丟例外，不是回 key 本身

回 key 的話，畫面上會出現 `credential.claude.why` 這種東西——它看起來像
一個字串、排版正常、沒有人會發現。那是【有預設值的分類等於沒有分類】
的同一個病。

丟例外安全，是因為 `test_every_key_used_anywhere_exists` 會把所有
`t("...")` 的 key 掃出來比對——**沒有一個 key 進得了正式版而 STRINGS
裡沒有它**。
"""
from __future__ import annotations

# key 用英文、分段用點。值是**今天的中文**——之後加語言就是多一份對照表，
# 不是改這裡的 key。
#
# 加新的字串進來的規矩：
#   1. key 要講「這句話在哪裡、做什麼」，不是講它今天寫什麼
#      （`wizard.depth.custom` ✅ ／ `wizard.text_custom` ❌）
#   2. 有變數的用 `{名字}`，呼叫的時候 `t(key, 名字=值)`
#   3. **不要在這裡放 prompt**。給 AI 看的字不是介面文字，它改動的理由
#      跟介面完全不同，而混在一起的那天翻譯會把 prompt 一起翻掉。
STRINGS: dict[str, str] = {
    # --- 憑證入口 --------------------------------------------------------
    "credential.claude.label": "Claude Code",
    "credential.codex.label": "Codex CLI",
    "credential.why.opens_browser": (
        "它會開瀏覽器讓你用自己的帳號同意。"
        "AICC 不代跑——一個能替你登入的程式，就是一個握得住你帳號的程式。"
    ),
    "credential.why.same_as_above": "同上。它會開瀏覽器讓你用自己的帳號同意。",
    "credential.button.take_me_there": "帶我去登入",
    "credential.error.not_found": (
        "找不到 {tool}。請先安裝它，或到「系統檢查」裡用「選擇位置…」指給 AICC 看。"
    ),
    "credential.error.launch_failed": "{tool} 啟動失敗：{reason}",
    # 憑證入口網頁版（`app/ui/credentials_web_panel.py`）自己的幾句。
    # 原生對話框那一份還在 `diag.*`，兩邊分工不同、字面沒有重複——
    # 重複的那幾句（「已登入」「掃描完成」「沒有其他副本」）一律重用既有 key。
    "credential.error.unsupported": "AICC 不認得 {provider} 這一支，沒辦法替你處理。",
    "credential.install.do_it_yourself": (
        "AICC 不替你下載或安裝東西。把上面那一行安裝指令複製到終端機自己跑一次，"
        "裝完回到這裡按「重新檢查」。"
    ),
    "credential.pick.title": "選擇 {name} 的執行檔",
    "credential.scan.pick_one": "找到 {count} 個不在 PATH 上的副本。要讓 AICC 用哪一個？",
    # --- 偵測卡 ----------------------------------------------------------
    "detect.confidence.sure": "查到了",
    "detect.confidence.likely": "看起來是",
    "detect.confidence.guessed": "猜的",
    "detect.confidence.unknown": "查不到",
    "detect.cell.name": "專案名稱",
    "detect.cell.version_control": "版本控制",
    "detect.cell.language": "用什麼寫的",
    "detect.cell.verify": "怎麼確認做完了",
    "detect.cell.used_before": "以前用過 AICC 嗎",
    "detect.because.folder_is_called": "資料夾就叫這個：{path}",
    "detect.because.has_git": "資料夾裡有 .git",
    "detect.because.no_git": "這個資料夾裡沒有 .git（可能在上一層，或者用的是別的工具）",
    "detect.because.saw_marker": "看到 {marker}",
    "detect.because.no_known_marker": "沒有看到認得的標記檔",
    "detect.because.no_test_config": (
        "沒有看到認得的測試設定檔——**這一格請你自己填**，"
        "AICC 靠它判斷每一次執行成功還是失敗"
    ),
    "detect.because.has_aicc": "資料夾裡有 .aicc——**精靈不會蓋掉既有設定**",
    "detect.because.no_aicc": "資料夾裡沒有 .aicc",
    "detect.value.used_before": "用過",
    "detect.value.not_used_before": "沒有",
    # --- 兩個精靈 --------------------------------------------------------
    "wizard.look.night": "暗",
    "wizard.look.day": "亮",
    "wizard.error.unknown_choice": "不認得的選項：{value}",
    "wizard.error.unknown_depth": "不認得的深淺：{value}",
    "wizard.error.unknown_look": "不認得的畫面：{value}",
    "wizard.error.unknown_sensitivity": "不認得的敏感度：{value}",
    "wizard.error.unknown_node_count": "不認得的 NODE 數：{value}",
    "wizard.error.presets_missing": "出廠表格少了，這是 AICC 自己的問題，不是你的設定。",
    "wizard.problem.sensitivity": "敏感度沒存成：{reason}",
    "wizard.problem.lineup": "陣容沒存成：{reason}",
    "wizard.problem.rules": "專案規矩沒存成：{reason}",
    "wizard.problem.ai_settings": "AI 設定沒存成：{reason}",
    "wizard.problem.look": "畫面偏好沒存成：{reason}",
    # --- 質化驗收標準 ----------------------------------------------------
    "rubric.problem.no_name": "有一個維度沒有名字",
    "rubric.problem.too_few_examples": (
        "「{name}」只有 {count} 個案例。至少要 {minimum} 個"
        "——**只給一個，產出會全部長成那一個樣子**。"
    ),
    "rubric.problem.no_counter_example": (
        "「{name}」沒有反例。反例才是有用的那一半："
        "「不准用破折號連接兩個短句」比「避免 AI 味」有用一百倍。"
    ),
    "rubric.problem.too_abstract": (
        "「{name}」裡的「{text}」太短，那是抽象描述不是案例。"
        "寫「開頭那句沒有勾子」，不要寫「不好」。"
    ),
    "rubric.problem.no_dimensions": "這份驗收標準一個維度都沒有。",
    "rubric.problem.not_an_object": "{key} 要是一個物件，讀到的是 {kind}。",
    "rubric.problem.not_a_list": "{key} 要是一個陣列。",
    "rubric.problem.dimension_not_an_object": "第 {index} 個維度不是物件。",
    "rubric.problem.examples_not_a_list": "「{name}」的 examples 要是一個陣列。",
    "rubric.problem.example_not_an_object": "「{name}」裡有一個案例不是物件。",
    "verify.no_machine_check": "這個專案用的是質化驗收標準，沒有可執行的檢查",
    "verify.by_rubric_reason": "這個專案用質化驗收標準（{count} 個維度）",
    "rubric.starter.hook.name": "開頭有沒有勾子",
    "rubric.starter.hook.bad": "開頭那句是「在這個快速變遷的時代」這種誰都能寫的話",
    "rubric.starter.hook.good": "開頭直接給一個具體的數字、日期或場景",
    "rubric.starter.complete.name": "有沒有把話講完",
    "rubric.starter.complete.bad": "寫「已優化相關流程」但沒有說優化了哪一段",
    "rubric.starter.complete.good": "每一個結論後面都接得上「憑什麼這樣說」",
    # --- 對話的來源（短名給表格，長名給提示）------------------------
    "source.claude-local.long": "Claude Code 本機紀錄",
    "source.claude-session.short": "Claude Code Session",
    "source.claude-session.long": "Claude Desktop / Claude Code 工作階段",
    "source.claude-task.short": "Claude Task",
    "source.claude-task.long": "Claude 任務紀錄",
    "source.claude-desktop.short": "Claude Desktop",
    "source.claude-desktop.long": "Claude Desktop 本機紀錄",
    "source.codex-app-server.short": "Codex Desktop",
    "source.codex-app-server.long": "Codex 本機紀錄",
    "source.codex-local-fallback.long": "Codex 本機備援紀錄",
    "source.aicc.short": "AICC-created",
    "source.aicc.long": "AI Control Center 建立",
    "source.imported-transcript.short": "Imported cloud transcript",
    "source.imported-transcript.long": "從雲端匯入的對話紀錄",
    "source.unknown.long": "本機紀錄",
    "verify.rubric_is_broken": "這個專案的驗收標準有問題：{problems}",
    # --- 收場（`app/core/outcomes.py` 那張表）--------------------------
    "outcome.APPROVAL_BLOCKED.label": "有紅章擋著",
    "outcome.APPROVAL_BLOCKED.stage": "有一格蓋了紅章，在那件事處理掉之前不派人拍板",
    "outcome.APPROVAL_ERROR.label": "拍板沒跑起來",
    "outcome.APPROVAL_ERROR.stage": "拍板那一格沒有交出東西",
    "outcome.APPROVAL_INVALID.label": "拍板的回覆看不懂",
    "outcome.APPROVAL_INVALID.stage": "拍板那一格的回覆格式不對，再跑一次",
    "outcome.APPROVAL_REJECTED.label": "拍板沒有通過",
    "outcome.APPROVAL_REJECTED.stage": "拍板那一格看完之後不同意，理由在下面",
    "outcome.ASK_USER.stage": "需要使用者決策",
    "outcome.CLAUDE_ERROR.label": "Claude 執行失敗",
    "outcome.CLAUDE_ERROR.stage": "Claude 暫時無法完成任務",
    "outcome.CODEX_ERROR.label": "Codex 執行失敗",
    "outcome.CODEX_ERROR.stage": "Codex 未完成審查",
    "outcome.CODEX_INVALID_RESPONSE.label": "Codex 回覆無效",
    "outcome.CODEX_INVALID_RESPONSE.stage": "無法解析 Codex 審查結果",
    "outcome.COMPLETED.label": "PASS",
    "outcome.COMPLETED.stage": "工作流程已完成",
    "outcome.CONSULT_INVALID.label": "討論的回覆看不懂",
    "outcome.CONSULT_INVALID.stage": "被問的那一格回覆格式不對，再跑一次",
    "outcome.CONSULT_STUCK.label": "兩格談不攏",
    "outcome.CONSULT_STUCK.stage": "同一題問了兩次還是沒有進展，要你決定",
    "outcome.CRITERIA_DEADLOCK.label": "驗收標準談不攏",
    "outcome.CRITERIA_DEADLOCK.stage": "同一個異議提了兩次還是沒有進展，要你決定",
    "outcome.CRITERIA_NO_OWNER.label": "沒有人訂驗收標準",
    "outcome.CRITERIA_NO_OWNER.stage": "這條線上只有做事的那一格，沒有人可以訂標準",
    "outcome.FIX_LIMIT_REACHED.label": "已達修正上限",
    "outcome.FIX_LIMIT_REACHED.stage": "自動修正做完了，仍未通過",
    "outcome.INTAKE_ASKING.stage": "在動手之前要先跟你確認一件事",
    "outcome.INTAKE_ERROR.label": "需求確認沒跑起來",
    "outcome.INTAKE_ERROR.stage": "需求確認那一格沒有交出東西",
    "outcome.INTAKE_INVALID.label": "需求確認的回覆看不懂",
    "outcome.INTAKE_INVALID.stage": "需求確認那一格的回覆格式不對，再跑一次",
    "outcome.MEMORY_ERROR.label": "共同記憶沒有維護到",
    "outcome.MEMORY_ERROR.stage": "程式做完了，但維護共同記憶那一次呼叫沒有成功——再跑一次就好",
    "outcome.MEMORY_TRUNCATED.label": "共同記憶不完整",
    "outcome.MEMORY_TRUNCATED.stage": "共同記憶沒有完整送出，請跑 tools/memory_budget.py 查",
    "outcome.NO_REVIEWING_SEAT.label": "沒有人複查",
    "outcome.NO_REVIEWING_SEAT.stage": "這條線上沒有一格會複查",
    "outcome.NO_WORKING_SEAT.label": "沒有人做事",
    "outcome.NO_WORKING_SEAT.stage": "這條線上沒有一格會寫檔案",
    # **不是「沒有人複查」。**那一句的意思是「東西做出來了但沒人審」，
    # 而這一種是「連第一步都派不出去」——一次派工都沒發生。
    "outcome.NO_SEAT_FOR_DUTY.label": "這一輪沒有開始",
    "outcome.NO_SEAT_FOR_DUTY.stage": "這條序列要派工的那一種職責，陣容裡一格都沒有",
    # **不是「共同記憶沒有維護到」**（那一句的結尾是「再跑一次就好」，
    # 而同一份陣容再跑一次會停在同一個地方）。這一句要先講產出還在。
    "outcome.MEMORY_NO_SEAT_FOR_DUTY.label": "共同記憶沒有人維護",
    "outcome.MEMORY_NO_SEAT_FOR_DUTY.stage": "程式做完了，但維護共同記憶那一步要派的職責，陣容裡一格都沒有",
    "outcome.NOTHING_TO_APPROVE.label": "沒有東西可以拍板",
    "outcome.NOTHING_TO_APPROVE.stage": "跑完了，但這一輪一個檔案都沒有改到",
    "outcome.PLANNER_ERROR.label": "規劃者執行失敗",
    "outcome.PLANNER_ERROR.stage": "規劃者沒有交出做法，尚未開始實作",
    "outcome.PLANNING_COMPLETED.label": "計畫完成",
    "outcome.PLANNING_COMPLETED.stage": "Codex 已完成最終計畫，可在整合時間軸查看",
    "outcome.PLAN_REJECTS_CRITERIA.label": "驗收標準被退回",
    "outcome.PLAN_REJECTS_CRITERIA.stage": "有一格認為這份驗收標準做不到，要你決定怎麼改",
    "outcome.VERIFY_NOT_CONFIGURED.label": "尚未設定驗證",
    "outcome.VERIFY_NOT_CONFIGURED.stage": "這個專案沒有宣告驗證指令，請先設定",
    "outcome.VERIFY_TIMED_OUT.label": "驗證逾時",
    "outcome.VERIFY_TIMED_OUT.stage": "驗證指令在宣告的時限內沒有結束",
    "outcome.group.ask": "等待決策",
    "outcome.group.decide": "要你決定怎麼做",
    "outcome.group.done": "跑完了",
    "outcome.group.fix": "有一項要先修",
    "outcome.group.rerun": "要再跑一次",
    "outcome.group.setting": "少一項設定",
    "undecidable.FAILED.label": "有檢查是紅的",
    "undecidable.FAILED.stage": "有一項檢查是紅的，那件事處理掉之前拍不了板",
    "undecidable.GATED.label": "還沒說怎麼確認結果",
    "undecidable.GATED.stage": "有東西還沒告訴我怎麼確認結果",
    "undecidable.MISSING.label": "還不能拍板",
    "undecidable.MISSING.stage": "跑完了、測試也過了，只是這一版還缺章。再跑一次會補上",
    "undecidable.READY.stage": "跑完了，但拍板的判定自己算不出來",
    "undecidable.SELF_REVIEW.label": "同一格自己出考卷",
    "undecidable.SELF_REVIEW.stage": "有一格同時訂了驗收標準又做了事，陣容要改",
    "undecidable.SPLIT_VERSION.label": "章蓋在不同版本上",
    "undecidable.SPLIT_VERSION.stage": "內容中途改過，現有的章不是蓋在同一版上。要你決定用哪一版",
    "diag.bundled": "安裝版內建，不需另外安裝",
    "diag.cli.and_not_signed_in": "（而且尚未登入）",
    "diag.cli.command_not_found": "找不到指令",
    "diag.cli.stale_shim": "指令可能是舊安裝殘留的捷徑，重新安裝一次即可。",
    "diag.cloud.folder_moved": "資料夾可能被移動或改名，到「設定與同步」重新選一次。",
    "diag.cloud.name": "雲端同步資料夾",
    "diag.cloud.repick_folder": "到「設定與同步」重新選一次資料夾。",
    "diag.configured": "已設定",
    "diag.git.not_installed": "未安裝",
    "diag.git.only_for_repos": "只有在版本控制的專案裡才需要，可先略過。",
    "diag.line.name": "LINE 通知",
    "diag.line.refill_token": "到「設定與同步」重新填一次 token。",
    "diag.not_configured_optional": "未設定（選用）",
    "diag.sessions.name": "本機對話格式",
    "diag.sessions.none_yet": "這台機器上還沒有本機對話紀錄",
    "cloud.settings.failed": "Google Drive 同步設定失敗",
    "cloud.settings.folder_required": "啟用同步時必須選擇 Google Drive 資料夾。",
    "cloud.settings.title": "Google Drive 同步設定",
    "cloud.status.disabled": "Google Drive：未啟用",
    "cloud.status.folder_missing": "Google Drive：資料夾不存在",
    "cloud.status.no_folder": "Google Drive：尚未選擇資料夾",
    "cloud.status.not_configured": "Google Drive：未設定",
    "cloud.warn.sync_in_flight": "有專案正在同步，這時候改雲端設定會讓進行中的同步寫到別的位置",
    "export.done": "匯出完成",
    "export.failed": "匯出失敗",
    "export.preview.title": "匯出 Workspace 預覽",
    "export.preview_failed": "匯出預覽失敗",
    "export.title": "匯出 AI Workspace",
    "import.done": "匯入完成",
    "import.failed": "匯入失敗",
    "import.outside_destination": "這個壓縮檔的專案會落在選定資料夾外面（{path}），已停止匯入。",
    "import.pick_destination": "選擇匯入目的資料夾",
    "import.pick_package": "選擇 AI Workspace 匯出包",
    "import.still_missing": "這幾件還沒好：",
    "index.missing.title": "文件索引沒有跟過來",
    "index.rebuild_failed": "重建索引失敗",
    "path.missing.title": "路徑不存在",
    "project.busy.running": "這個專案正在執行",
    "project.busy.syncing": "這個專案正在同步，同步完才能開始執行",
    "project.initialise.failed": "初始化失敗",
    "project.initialise.title": "初始化專案",
    "project.new.done": "建立完成",
    "project.new.failed": "建立失敗",
    "project.new.name_label": "專案名稱：",
    "project.new.name_required": "專案名稱不能空白。",
    "project.new.pick_parent": "選擇新專案要建立在哪個資料夾",
    "project.new.title": "建立配對專案",
    "project.path.label": "專案路徑",
    "project.path_missing.title": "專案路徑不存在",
    "project.pick_folder": "選擇專案資料夾",
    "project.remove.need_one": "專案清單至少需要保留一個專案。",
    "project.remove.refused_title": "無法移除",
    "project.remove.title": "移除專案",
    "run.status.not_started": "尚未執行",
    "run.status.running": "執行中",
    "run.status.waiting_to_start": "等待開始",
    "action.failed.title": "操作沒有完成",
    "cloud.blocked.title": "Google Drive 同步擋下可疑檔案",
    "cloud.conflict.title": "Google Drive 同步發現衝突",
    "cloud.link.confirm": "確認連結",
    "cloud.link.failed": "連結失敗",
    "cloud.link.none_available": "雲端目前沒有可連結的 AI Control Center 專案。",
    "cloud.link.pick_label": "選擇雲端專案：",
    "cloud.link.pick_local": "選擇對應的本機 Git 專案資料夾",
    "cloud.link.title": "連結雲端專案",
    "cloud.settings_first": "請先完成 Google Drive 同步設定。",
    "cloud.bad_project_id": "這個專案的識別碼格式不對（{project_id}），為了不寫到同步資料夾外面，已停止同步。",
    "cloud.status.sync_failed": "Google Drive：同步失敗",
    "cloud.status.syncing": "Google Drive：同步中……",
    "cloud.sync.enable_first": "請先按「雲端設定」啟用 Google Drive 同步。",
    "cloud.sync.failed_title": "Google Drive 同步失敗",
    "cloud.sync.title": "Google Drive 同步",
    "error.unknown": "未知錯誤",
    "file.created.title": "已產生檔案",
    "gate.dismissed_note": "已將決策橫幅收起；此專案仍在等待你的決定。",
    "handoff.created.title": "接手任務已建立",
    "path.missing.inline": "（路徑不存在）",
    "run.already_running.title": "專案目前正在執行",
    "run.answer_received": "已收到使用者答案，工作流程繼續。",
    "run.heavy.confirm_title": "確認高用量執行",
    "run.initialising": "正在初始化",
    "run.live_log.placeholder": "執行結果將顯示在這裡。",
    "run.live_log.title": "即時執行紀錄",
    "run.needs_decision": "此任務需要使用者決策。",
    "run.stale_lock_cleared": "偵測到過期的工作流程鎖定，已自動清除。",
    "run.started": "工作流程開始。",
    "run.stop.nothing_found": "沒有找到可停止的子行程；本次執行可能正在收尾。",
    "run.stop.nothing_running": "目前沒有正在執行的子行程可以停止，請稍候再試。",
    "run.stop.signal_sent": "已送出停止訊號，等待本次執行收尾。",
    "run.stop.stopping": "正在停止",
    "run.stop.title": "停止執行",
    "close.busy.body": "Claude、Codex 或 Google Drive 同步目前仍在執行。\n現在關閉視窗可能不會立即停止背景程序。\n\n仍要關閉嗎？",
    "close.busy.title": "背景工作仍在執行",
    "cloud.blocked.body": "部分檔案內容疑似包含 API key、token 或密碼，已不上傳到共用的 Google Drive 資料夾。\n請先清除檔案中的機密內容，再重新同步。",
    "cloud.blocked.files_label": "已擋下上傳（疑似含密鑰）：",
    "cloud.conflict.body": "本機與雲端同時修改了部分檔案。\n系統已保留雙方版本，並以本機版本作為目前版本。\n請檢查專案 .aicc\\conflicts 資料夾。",
    "cloud.conflict.files_label": "衝突檔案：",
    "export.confirm_continue": "\n\n是否繼續匯出？",
    "handoff.mode_switched": "已切換為 Codex 先行的 1 回合計畫模式。\n先確認任務內容，再按上方「開始執行」。",
    "index.rebuild_prompt": "這個專案的文件索引不完整，在重建之前，執行時不會帶到專案資料。\n\n現在重建嗎？（只讀本機檔案，不會呼叫 AI，也不會花錢）",
    "line.already_configured": "這台機器已經設定過 LINE 通知。\n\n要改成新的憑證，還是把現有的移除？",
    "line.remove.done_body": "這台機器上的 LINE 憑證已經刪除，等待決策時不會再發通知。\n\n這只刪掉本機這一份——如果那把 token 不該再被使用，要到 LINE Developers 後台自己撤銷。",
    "line.remove.done_title": "已移除",
    "line.remove.failed": "移除失敗",
    "line.setup.done_body": "金鑰已使用 Windows DPAPI 加密儲存，\n測試訊息已送出。",
    "line.setup.done_title": "LINE OA 設定完成",
    "line.setup.failed": "LINE OA 設定失敗",
    "line.setup.title": "LINE OA 設定",
    "line.test_failed": "LINE OA 測試失敗",
    "line.test_message": "【AI Control Center】LINE OA 通知測試成功。",
    "line.token_required": "Channel Access Token 不可為空。",
    "line.user_id_label": "接收通知的 LINE User ID：",
    "line.user_id_required": "LINE User ID 不可為空。",
    "notify.line_failed_but_waiting": "等待決策；LINE 通知失敗",
    "notify.redacted_question": "問題：（高敏感度專案，內容不外送，請回到 AICC 查看）",
    "outcome.unrecognised.body": "這一輪跑完了，但畫面認不出它是哪一種收場——這是一個瑕疵，請回報",
    "outcome.unrecognised.title": "沒認出來的結局",
    "project.imported.next_steps": "既有專案已導入 AI Control Center。\n\n建議下一步：\n1. 在「專案資料」按「掃描專案」。\n2. 匯入需要參考的文件或舊對話摘要。\n3. 按「建立記憶檔」確認共同記憶存在。\n4. 按「建立接手任務」，讓 Codex 和 Claude 產生接手摘要。\n",
    "result.none_yet.body": "目前尚未產生工作流程結果檔。",
    "result.none_yet.title": "尚無結果",
    "run.already_running.body": "偵測到這個專案已經有工作流程在執行中（可能來自另一個 AI Control Center 視窗或處理程序）。\n請先等待該工作流程完成，或確認沒有其他視窗在執行後再試一次。",
    "run.answer_prompt": "\n\n請輸入 A／B、具體指示或其他答案：",
    "run.can_rerun": "可重新執行任務",
    "run.end_wait.failed": "無法結束等待",
    "run.end_wait.title": "結束等待",
    "run.error.title": "工作流程發生錯誤",
    "run.failed": "執行失敗",
    "run.failed.body": "執行過程發生錯誤，請查看執行紀錄。",
    "run.failed.title": "工作流程失敗",
    "run.fix_limit.body": "Claude 已完成兩次自動修正，但任務仍未通過。\n請查看執行紀錄後人工處理。",
    "run.fix_limit.title": "已達自動修正上限",
    "run.start_failed": "工作流程啟動失敗，請查看錯誤訊息後重試。",
    "run.wait_ended.body": "使用者已結束等待；未繼續執行工作流程。",
    "run.wait_ended.title": "等待已結束",
    "run.waiting_five_minutes": "等待使用者回答已達 5 分鐘。",
    "run.waiting_for_answer": "等待使用者回答",
    "common.no_content": "（無內容）",
    "common.refresh": "重新整理",
    "common.time_unknown": "時間不明",
    "sessions.all_sessions": "全部工作階段",
    "sessions.first_request": "最初需求",
    "sessions.help": "先選你在 Claude Code 或 Codex 裡熟悉的對話名稱。AICC 會優先顯示官方名稱與手動命名；看不懂時可先看預覽，再按「重新命名」。若原本對話不屬於目前專案，請按「掃描全部本機對話」。",
    "sessions.latest_exchange": "最近互動",
    "sessions.name_label": "對話名稱：",
    "sessions.new": "新對話",
    "sessions.new_on_next_run": "新對話（下一次執行時建立）",
    "sessions.none.body": "請先按「掃描目前專案」或「掃描全部本機對話」。",
    "sessions.none.title": "尚無可選對話",
    "sessions.not_picked.body": "請先選擇 Claude 或 Codex 對話，或按「選擇最新一組」。",
    "sessions.not_picked.title": "尚未選擇對話",
    "sessions.pick_latest": "選擇最新一組",
    "sessions.preview.claude": "Claude 預覽",
    "sessions.preview.claude_empty": "選擇 Claude 對話後顯示內容預覽。",
    "sessions.preview.codex": "Codex 預覽",
    "sessions.preview.codex_empty": "選擇 Codex 對話後顯示內容預覽。",
    "sessions.preview.title": "所選對話預覽",
    "sessions.preview_help": "選取對話後，下方會自動顯示最初需求與最近互動。按「顯示所選紀錄」才會把完整內容載入整合時間軸。",
    "sessions.rename": "重新命名",
    "sessions.scan_all": "掃描全部本機對話",
    "sessions.scan_done": "本機對話掃描完成",
    "sessions.scan_project": "掃描目前專案",
    "sessions.scope.all_local": "全部本機對話",
    "sessions.scope.this_project": "目前專案",
    "sessions.show_selected": "顯示所選紀錄",
    "sessions.title": "對話配對",
    "sessions.unreadable_reason": "可能是舊工作階段、格式已變更，或該對話尚未產生訊息。",
    "timeline.all_sources": "全部來源",
    "timeline.empty_help": "工作流程執行後，Claude、Codex、測試與使用者回答會顯示在這裡。",
    "timeline.none_yet": "目前尚無整合時間軸紀錄。",
    "timeline.source.user": "使用者",
    "timeline.title": "整合時間軸",
    "ai.auto_bootstrap": "新對話自動帶入啟動提示（較好接手，但會增加本次 token）",
    "ai.auto_memory": "任務 PASS 後自動維護共同記憶（多用 Codex + Claude 各一次）",
    "ai.claude.effort": "Claude 思考",
    "ai.claude.max_turns": "Claude 最大回合",
    "ai.claude.model": "Claude 模型",
    "ai.claude.permission": "Claude 權限",
    "ai.claude.speed": "Claude 速度",
    "ai.claude.speed_note": "Standard（Sonnet 5 無獨立加速選項）",
    "ai.codex.effort": "Codex 思考",
    "ai.codex.model": "Codex 模型",
    "ai.codex.speed": "Codex 速度",
    "ai.codex.speed_note": "由模型與思考程度決定",
    "ai.context.balanced": "Balanced｜一般任務",
    "ai.context.minimal": "Minimal｜最省 token",
    "ai.context.strict": "Strict｜複雜/高風險",
    "ai.context_profile": "上下文模式",
    # 模型與強度的顯示名。**它們是介面文字**——今天剛好全是 ASCII，
    # 而「今天不用翻」跟「不是介面文字」是兩件事。放這裡的另一個理由是
    # `app/services/run_settings_options.py` 那份宣告要全部走鍵：
    # 一張表裡兩種形狀（有些存鍵、有些存字）遲早會有人挑錯一種。
    # 「這一次要怎麼跑」那一段擋下來時說的話（2026-08-27 搬進網頁那一輪）。
    # 那一行「本次：⋯」。**整句一個鍵**——拆成七小段的話，
    # 翻譯的人看不出它們會被接成一句話，而接出來的順序在別的語言裡是錯的。
    "run.summary": ("本次：{mode}｜Claude {claude_model} / {claude_effort}"
                    "｜權限 {permission}｜最長 {timeout}／最大回合 {turns}"
                    "｜Codex {codex_model} / {codex_effort}｜{detail}"
                    "｜{context}｜{session}｜{bootstrap}｜{memory}"),
    "run.summary.detail.planning": "{rounds} 回合，預計 {calls} 次模型呼叫",
    "run.summary.detail.development": ("最多自動 FIX {fixes} 次；"
                                       "最壞情況約 {calls} 次模型呼叫"),
    "run.summary.timeout_minutes": "{minutes} 分鐘",
    "answer.nobody_waiting": ("現在沒有人在等你回話。你打的字還留著，但**今天送不出去**——"
                              "一段話要進到一個還沒開跑的格子裡，得先有地方存著等它，而那個地方還沒做。"),
    "answer.not_the_waiting_seat": "在等你的是【{waiting}】。這一格今天收不下——引擎只認在等的那一格。",
    "answer.empty": "回覆是空的——送出去的話，那一格拿到一份沒有內容的回覆，而它會照樣往下跑。",
    "run.unknown_setting": "認不得的設定：{key}",
    "run.no_such_choice": "「{key}」沒有「{value}」這個選項——畫面上那份清單跟引擎對不上了",
    "run.not_a_number": "「{key}」要一個數字，收到的是「{value}」",
    "run.out_of_range": "「{key}」只收 {lo}～{hi}，收到的是 {got}",
    "run.unknown_target": "不知道要存去哪裡：{where}",
    "run.unknown_profile": "沒有這一張出廠表格：{value}",
    "run.unknown_provider": "沒有這一家：{value}",
    "task.empty.web": "任務是空的——沒有題目的話，這一輪跑出來的東西沒有人驗得了",
    "task.write_failed": "寫不進 {name}：{error}",
    "ai.model.sonnet": "Claude Sonnet 5",
    "ai.model.opus": "Claude Opus 5",
    "ai.model.haiku": "Claude Haiku",
    "ai.effort.auto": "Auto",
    "ai.effort.low": "Low",
    "ai.effort.medium": "Medium",
    "ai.effort.high": "High",
    "ai.effort.xhigh": "Extra High",
    "ai.effort.max": "Max",
    "ai.effort.ultracode": "Ultracode",
    "ai.codex.model.gpt55": "GPT-5.5",
    "ai.codex.model.sol": "GPT-5.6 Sol",
    "ai.codex.model.terra": "GPT-5.6 Terra",
    "ai.codex.model.luna": "GPT-5.6 Luna",
    "ai.codex.model.mini": "GPT-5.4 Mini",
    "ai.follow_cli_default": "跟隨 Claude Code 預設",
    "ai.force_new_sessions": "每次依目前模型設定建立新對話（確保模型／思考程度生效）",
    "ai.hint.bootstrap_off": "新對話不自動帶入啟動提示",
    "ai.hint.bootstrap_on": "新對話會帶入啟動提示",
    "ai.hint.context_balanced": "上下文 Balanced",
    "ai.hint.context_minimal": "上下文 Minimal",
    "ai.hint.context_strict": "上下文 Strict",
    "ai.hint.memory_off": "記憶維護關",
    "ai.hint.memory_on": "記憶維護開",
    "ai.hint.new_session": "建立新對話，設定可確實套用",
    "ai.hint.resume_session": "續接所選對話；既有 session 可能保留原模型設定",
    "ai.max_fix_attempts": "自動 FIX",
    # 這裡本來有四個 key：`ai.mode.planning`「計畫發想：Codex 先行」、
    # `ai.mode.development`「正式開發：Claude 執行」，加上兩個 `.short`。
    # **2026-08-26 全部刪掉，改用 `sequence.planning`／`sequence.development`。**
    # 理由寫在那兩個 key 上面。
    "ai.permission.auto": "安全自動（推薦）",
    "ai.permission.edits_only": "自動編輯、指令保守",
    "ai.permission.manual": "手動授權（非互動可能停止）",
    "ai.permission.read_only": "唯讀規劃",
    "ai.planning_rounds": "討論回合",
    "ai.save_global_default": "設為全域預設",
    "ai.save_project_default": "儲存專案預設",
    "ai.saved_as_project_default": "目前設定已儲存為此專案的預設值。",
    "ai.timeout": "單次最長時間",
    "ai.timeout.10m": "10 分鐘",
    "ai.timeout.1h": "1 小時",
    "ai.timeout.30m": "30 分鐘",
    "ai.timeout.3h": "3 小時",
    "ai.timeout.6h": "6 小時",
    "ai.title": "AI 執行設定",
    "ai.verbosity": "輸出長度",
    "ai.workflow_mode": "工作模式",
    "common.save_failed": "儲存失敗",
    "common.saved": "已儲存",
    "task.empty.body": "請先輸入計畫內容或開發任務。",
    "task.empty.title": "任務不可為空",
    # 這兩句以前自己抄了一份「計畫發想／正式開發」，於是同一件事在同一個
    # 面板上有兩種講法。**現在名字從 `sequence.*` 帶進來**——那是唯一的
    # 一份，改一個地方兩個畫面一起改。
    "task.help": "「{planning}」不修改程式碼；「{development}」才會進入 Claude 修改、測試與 Codex 審查。安全自動會讓 Claude Code 自動處理低風險操作；高風險、權限受阻或需求不明時，Claude 會結束本段並由 AICC 顯示 ASK_USER，回答後續接同一個 Session。目前是分段續接，不是執行中的即時終端對話。",
    "task.placeholder": "在這裡輸入「{planning}」要討論的計畫內容，或「{development}」要交辦的任務。",
    "task.save": "儲存到 task.md",
    "task.save_plan": "儲存計畫需求",
    "task.title": "工作對話／任務輸入",
    "crash.body": "剛才那個動作沒有完成。",
    "crash.hide_details": "隱藏技術細節",
    "crash.reason.disk_full": "磁碟空間不足。",
    "crash.reason.encoding": "某個檔案的編碼不是 UTF-8，讀不出來。",
    "crash.reason.permission": "沒有權限讀寫某個檔案。它可能正被其他程式開著，或位於受保護的資料夾。",
    "crash.show_details": "顯示技術細節",
    "crash.title": "AI Control Center 遇到問題",
    "diag.all_ready": "需要的工具都就緒了。",
    "diag.executable_filter": "執行檔 (*.exe *.cmd *.bat);;所有檔案 (*)",
    "diag.forget_location": "忘記這個位置",
    "diag.has_blockers": "有項目需要處理才能執行工作流程。",
    "diag.pick_location": "選擇位置…",
    "diag.recheck": "重新檢查",
    "diag.recheck.tip": "重跑一次上面這些檢查（比「掃描這台機器…」快得多）。",
    "diag.scan.nothing_else": "除了 PATH 上已經有的之外，沒有在這台機器上找到其他副本。\n\n如果你知道它裝在哪裡，用那一列的「選擇位置…」直接指給 AICC。",
    "diag.scan_done_title": "掃描完成",
    "diag.scan_machine": "掃描這台機器…",
    "diag.scan_machine.tip": "找出這台機器上所有的 Claude Code / Codex CLI 副本，包括不在 PATH 上的。需要幾秒鐘。",
    "diag.sign_in_unknown": "登入狀態不明",
    "diag.signed_in": "已登入",
    "diag.signed_out": "尚未登入",
    "diag.skip_when_fine": "之後一切正常時不要再顯示",
    "diag.title": "啟動檢查",
    "library.bind_selected": "綁定選取對話",
    "library.bound.body": "已將選取的 Claude / Codex 對話綁到目前專案。",
    "library.bound.title": "綁定完成",
    "library.column.name": "對話名稱",
    "library.column.updated": "最後更新",
    "library.create_from_pair": "用配對建立專案",
    "library.detail_placeholder": "選擇對話後會顯示來源、狀態與最近互動。",
    "library.help": "先從本機 Claude 與 Codex 對話中找出你認得的工作，再把兩邊配成一組。文件匯入只是補充，不應該是第一步。",
    "library.no_claude": "沒有找到 Claude 對話。",
    "library.no_codex": "沒有找到 Codex 對話。",
    "library.not_scanned": "尚未掃描",
    "library.nothing_picked.body": "請至少選擇一個 Claude 或 Codex 對話。",
    "library.nothing_picked.title": "尚未選擇",
    "library.nothing_selected": "目前選擇：Claude 未選，Codex 未選",
    "library.pick_claude_first": "請先掃描並選擇 Claude 對話。",
    "library.pick_codex_first": "請先掃描並選擇 Codex 對話。",
    "library.rescan": "重新掃描",
    "library.scan_failed": "掃描失敗",
    "library.search_placeholder": "搜尋對話名稱、專案路徑或最近訊息",
    "library.title": "連接既有 Claude / Codex 對話",
    "dash.context_carried": "帶入 context",
    "dash.done_today": "今日完成",
    "dash.failed_at": "失敗在哪一格",
    "dash.handoff_todo": "接手待辦",
    "dash.last_activity": "最後活動",
    "dash.queued": "佇列中",
    "dash.resend_saved": "省下重送",
    "dash.today.completed": "完成任務",
    "dash.today.retried": "失敗重試",
    "dash.unpaired": "未配對",
    "dash.unpaired_suffix": " · 未配對",
    "dash.waiting_for_you": "等你決定",
    "dash.why_not_moving": "為什麼沒動",
    "queue.paused_note": "佇列已暫停，不會自動遞補。",
    "common.none_bullet": "- 無",
    "knowledge.block.failed_documents": "有文件解析失敗，請修正或移除該文件。",
    "knowledge.block.memory_incomplete": "共同記憶檔不完整，請建立或補齊內容。",
    "knowledge.block.missing_sources": "有原始文件遺失，請確認來源或保留副本。",
    "knowledge.block.no_documents": "沒有掃描到可索引的專案文件；請先確認專案資料夾，或匯入文件。",
    "knowledge.block.none": "沒有阻塞項目。",
    "knowledge.block.parse_errors": "有文件解析錯誤，請查看下方掃描錯誤。",
    "knowledge.block.stale_sources": "有原始文件已更新，請重新解析。",
    "knowledge.needs_attention": "需要處理",
    "knowledge.next.check_missing": "檢查原始檔遺失的文件。",
    "knowledge.next.create_memory": "建立共同記憶檔。",
    "knowledge.next.fix_failed": "查看解析失敗的文件並修正。",
    "knowledge.next.ready": "可以建立接手任務，讓 Codex 與 Claude 開始盤點。",
    "knowledge.next.reparse_stale": "重新解析已更新的來源文件。",
    "knowledge.next.scan_or_import": "掃描專案，或匯入可供 AI 參考的文件。",
    "knowledge.no_keywords_note": "這次沒有從任務文字裡抓到可用的關鍵字，所以帶的是「最近更新的資料」，跟這個任務不一定相關。任務寫具體一點（含檔名、功能名或術語）會挑得準。",
    "knowledge.preview_truncated": "\n\n…（預覽已截斷）",
    "knowledge.project_rules_title": "這個專案的規矩（使用者自己訂的）",
    "knowledge.pypdf_missing": "PDF 解析需要 pypdf；請在 AICC 虛擬環境執行 python -m pip install pypdf",
    "knowledge.ready_for_handoff": "可開始接手",
    "knowledge.where_the_rest_is": "### 其餘的記憶在哪裡\n",
    "export.answered_by": "- 答覆者：",
    "export.answered_by_codex": "- 答覆者：Codex（未經人員）※ 由紀錄類型推得",
    "export.contains_raw": "**本檔案包含原始問答內容**，可能含專案或客戶資訊，請比照原始資料保管。",
    "export.kyc.ack_required": "必須確認你知道這份檔案包含原始問答內容（可能含客戶資料）。\nAICC 目前沒有遮蔽功能，所以這件事只能由你確認，不能由程式假設。",
    "export.kyc.gate_question": "匯出高敏感度專案的決策紀錄",
    "export.kyc.reason_required": "授權必須附理由。理由會寫進紀錄，讓日後讀到這份匯出的人知道它為什麼離開這台機器。",
    "export.kyc.refused": "這是 sensitive_kyc 專案，決策紀錄預設不得匯出。\n原因與 workspace 匯出／雲端同步相同：紀錄內含原始問答，可能有客戶資料。\n要匯出必須逐次明確授權，並說明理由——授權本身會寫進紀錄裡。",
    "export.no_records": "（這個專案還沒有任何 Decision Gate 紀錄。）",
    "export.no_timestamp": "（無時間）",
    "export.proof.can": "- **能證明**：中間任何一筆被修改、刪除或插入，驗證就不會通過。",
    "export.proof.cannot_complete": "- **也不能證明**：這是全部。從**結尾**整段砍掉的紀錄不會留下痕跡，因為沒有任何地方記著「應該有幾筆」。",
    "export.proof.cannot_history": "- **也不能證明**：這涵蓋整個專案的歷史。這條鏈從它的第一筆開始，在那之前發生的事不在裡面——換過電腦的話，舊機那一段**不會**跟著過來（決策紀錄刻意不放進 workspace 匯出包）。",
    "export.proof.cannot_honest": "- **不能證明**：紀錄本身誠實。能編輯檔案的人也能把整條鏈重算一次。",
    "export.proof.heading": "## 這份紀錄能證明什麼",
    "export.proof.how": "每一筆都帶一個指紋，由「這筆內容」加「前一筆的指紋」算出來，一環扣一環。",
    "export.tampered": "> 這份紀錄在寫入之後被更動過，不應作為稽核依據。",
    "export.unlabelled": "（未標示）",
    "dash.needs_you": "要你處理",
    "project.folder_missing": "專案資料夾不存在",
    "queue.why.already_running": "這個專案已經有一個流程在跑。",
    "queue.why.folder_missing": "專案資料夾不存在，可能被搬走或刪掉了。",
    "queue.why.no_task": "沒有 .aicc/task.md，沒有任務可以執行。",
    "queue.why.not_queued": "沒有加入佇列，排程器不會自動啟動它。",
    "queue.why.paused": "佇列整個暫停中。繼續佇列之後才會輪到它。",
    "queue.why.stale_lock": "有一個過期的執行鎖（上一次沒有正常結束）。清掉就可以啟動。",
    "chain.builds_on_missing": "這個章說它建立在還不存在的章之上：",
    "chain.decidable": "可以拍板",
    # 章的判定跟畫面上那個收場的名字是**同一句話**，所以共用同一個 key
    #（`undecidable.FAILED.label`、`undecidable.GATED.stage`）。
    # 各存一份的話，改了一邊使用者會在兩個地方看到兩種說法。
    "summary.no_question_text": "（沒有記下問題內容）",
    "summary.nothing_waiting": "沒有還在等你回答的問題。",
    "summary.warn_raw_questions": "⚠ 下面含問題原文，可能有客戶名稱。這是本機閱讀用的。",
    "summary.warn_project_names": "⚠ 這份內容含專案名稱，專案名稱常常就是客戶名稱。",
    "summary.warn_local_only": "  這是本機閱讀用的，不是可以直接寄出去的報表。",
    "summary.no_records_in_range": "這個範圍內沒有任何可採計的決策紀錄。",
    "summary.no_decided": "已定案的決策：0 件，所以沒有「多少比例有人看過」可算。",
    "summary.mostly_auto_1": "  一半以上是 Codex 自己批准的。那是 Decision Gate 的設計行為，不是故障——",
    "summary.mostly_auto_2": "  但這個比例代表這些專案目前的實際狀態比較接近「AI 做事」，而不是「AI 做事、人把關」。",
    "summary.excluded_projects": "以下專案沒有列入計算，各自的原因：",
    "summary.unverifiable": "無法驗證",
    "summary.broken_chain_note": "  鏈斷掉不代表一定有人動過手腳，但它代表這份紀錄不能拿來回答「我批准了幾件」。",
    # --- 設定的家（`app/services/settings_homes.py`）---------------------
    "home.project.what_and_how": "這個專案是什麼、怎麼驗",
    "home.project.people": "這個專案的人",
    "home.project.rules": "這個專案的規矩",
    "home.project.memory": "這個專案的記憶",
    "home.project.this_run": "這一次跑了什麼",
    "home.project.this_task": "這一次要做什麼",
    "home.project.this_result": "這一次跑完的結果",
    "home.project.runtime_lock": "現在有沒有人在跑",
    "home.project.sync_state": "雲端同步到哪了",
    "home.project.handover": "換機器要帶走什麼",
    "home.project.gitignore": "哪些東西不進版控",
    "home.project.timeline": "從頭到現在發生過什麼",
    "home.project.decisions": "拍板過什麼（一條扣一條的鏈）",
    "home.project.conflicts": "雲端跟本機打架的時候兩邊留著",
    "home.project.imported": "從別的地方搬進來的資料",
    "home.machine.ai_settings": "AI 怎麼跑",
    "home.machine.presets": "有哪些角色表格",
    "home.machine.cli_paths": "機器上的東西在哪",
    "home.machine.projects": "有哪些專案",
    "home.machine.ui": "畫面偏好",
    "home.logs.crash": "當掉的時候留下什麼",
    "layer.base": "產品底座",
    "layer.project": "專案規則",
    "layer.role": "角色積木",
    "gate.category.direction": "產品方向",
    "gate.category.delete": "資料刪除",
    "gate.category.schema": "資料庫結構",
    "gate.category.permission": "權限",
    "gate.category.security": "安全",
    "gate.category.external": "外部服務",
    "gate.category.install": "套件安裝",
    "gate.category.paid": "付費服務",
    "gate.floor.outside.label": "專案外資料",
    "gate.floor.outside.note": "要動到專案資料夾以外的檔案時",
    "gate.floor.secrets.label": "機密資訊",
    "gate.floor.secrets.note": "偵測到金鑰、密碼、Token 時",
    "gate.floor.git.label": "Git commit/push/reset/rebase",
    "gate.floor.git.note": "改寫歷史：commit / push / reset / rebase",
    "gate.floor.unsure.label": "目前資料不足以判斷",
    "gate.floor.unsure.note": "AI 自己也不確定該不該做的時候",
    # --- 出廠三張角色表格（使用者可改的預設值）--------------------------
    "preset.one.name": "一人：只有執行者",
    "preset.one.description": "小事。沒有人複查，也沒有人訂驗收標準——章會誠實地缺一個。",
    "preset.two.name": "兩人：執行者 → 複查者",
    "preset.two.description": "一般。Claude 做、Codex 看。複查那一格會在動手之前先跟你把需求問清楚。",
    "preset.three.name": "三人：執行者 → 複查者 → 核准者",
    "preset.three.description": "重要。多一格看所有的章能不能拍板。",
    # 出廠模式排出來的**座位叫什麼名字**。
    #
    # 2026-08-25 全部加上「者」。以前是「執行／複查／核准」，跟職責下拉的
    # 四個字**一模一樣**——所以三人模式排出來的那一列長這樣：
    #
    #     [執行]  [執行▾]  [會寫檔案]  [claude▾]  寫程式
    #      名字    職責
    #
    # 同一個字在同一列上出現兩次，而它們是兩件事（一個是這一格叫什麼、
    # 一個是引擎怎麼對待它）。**讀的人只會以為畫面壞了。**
    #
    # 「者」是這一組唯一的分工：**名字是人的稱呼，職責是工作的種類。**
    "role.worker": "執行者",
    "role.reviewer": "複查者",
    "role.approver": "核准者",
    "role.planner": "規劃者",
    "page.lineup": "陣容",
    "page.dashboard": "儀表板",
    "page.sessions": "執行中對話",
    "page.knowledge": "共享記憶",
    "page.documents": "文件",
    "page.settings": "設定",
    "page.log": "即時紀錄",
    "page.audit": "決策紀錄",
    "memory.brief": "定位",
    "memory.architecture": "架構",
    "memory.decisions": "決策",
    "memory.security": "安全",
    "memory.review": "檢討",
    "memory.business": "商業",
    "memory.next": "下一步",
    "memory.open": "待答",
    "memory.generic": "記憶",
    "page.roadmap": "Roadmap",
    # 一次執行走的是哪一條路。**這兩個 key 是那兩個名字的唯一一份。**
    #
    # ## 為什麼留下來的是「序列」不是「發想／開發」（2026-08-26）
    #
    # 在這之前同一件事有兩個名字：AI 執行設定面板上的下拉叫
    # 「計畫發想／正式開發」，陣容頁上的徽章叫「計畫序列／開發序列」，
    # 而使用者要在那兩個畫面之間對照——他排的那五格這一輪各自要做什麼，
    # 答案在陣容頁；而決定跑哪一條的開關在設定面板。兩個名字，
    # 他沒有辦法知道那是同一個東西。
    #
    # 留「序列」的三個理由：
    #
    # 1. **「計畫發想／正式開發」不是一組對照。**兩個詞不在同一條軸上：
    #    一個講產出（發想），一個講正式與否（正式）。而且「正式開發」
    #    暗示另一條是「非正式」——**那是假的**：計畫序列是一次真的執行，
    #    花真的額度，寫真的檔案（final-plan.md、共同記憶）。
    # 2. **引擎自己的字就是「序列」。**`Orchestrator.SEQUENCE_JOBS`、
    #    `sequence_for()`、陣容頁推過去的 `sequence` 欄位——三處都是。
    #    設定面板那兩個 key 是唯一的例外。改例外，不改三處。
    # 3. 「：Codex 先行」「：Claude 執行」那半句**現在是錯的**。派工看的是
    #    職責不是廠牌（2026-08-25 起），計畫序列的第一步派給陣容裡最後
    #    一格「複查」——那一格可能是 Claude。留著等於在畫面上寫一句
    #    引擎已經不遵守的話。
    "sequence.development": "開發序列",
    "sequence.planning": "計畫序列",
    # 「這一輪它要做什麼」。**每一句都帶著「這一輪：」，那三個字是重點。**
    #
    # ## 為什麼要有前綴
    #
    # 這一欄跟旁邊的職責下拉緊鄰，而兩欄都是中文短詞——沒有前綴的話，
    # 使用者看到的是兩個並排的名詞，讀起來像同一個欄位印了兩次。
    # 它們其實是兩種不同的句子：
    #
    # | 欄 | 回答的問題 | 誰定的 | 會不會變 |
    # |---|---|---|---|
    # | 職責 | 這一格**是**什麼 | 使用者 | 不會 |
    # | 工作 | 這一格**這一次**要做什麼 | 引擎算的 | **會** |
    #
    # 最後那一格是這一欄存在的唯一理由：**同一格「執行」職責的位子，
    # 在開發序列是「動手做」，在計畫序列是「審計畫」。**
    # 沒有這一欄，使用者看不出換了序列之後他排的線在做別的事。
    "job.plan": "這一輪：出做法",
    # 不是「寫程式」。【不是所有使用者都寫程式】——這一格可能在規劃一支
    # 影片的剪法、一份文件的章節、一張圖的構圖，而
    # `prompt_builder.py` 的人設早就不提程式了，只有這個字漏掉。
    "job.build": "這一輪：動手做",
    # 不是「審查」。同一件事在這個產品裡有兩個字（職責叫複查、工作叫審查），
    # 而【驗收標準由複查角色訂】那條定案用的是「複查」。**一個概念一個字。**
    "job.review": "這一輪：複查",
    # 核准那一種以前整張表都沒有，於是每一個排了核准的座位都被畫成
    # 「不會被執行」——而 `steps/approval.py` 真的會派工給它。
    "job.approve": "這一輪：拍板",
    "job.read_plan": "這一輪：審計畫",
    "job.write_plan": "這一輪：出計畫",
    "documents.state.indexed": "已索引",
    "documents.state.stale": "原檔已變更",
    "documents.state.missing_index": "索引遺失",
    "documents.state.missing_source": "原檔不存在",
    "documents.why.hit": "命中",
    "documents.why.contains": "文字包含",
    "documents.why.fallback": "沒有比對到關鍵字，帶的是最近更新的",
    "documents.import_files": "匯入檔案",
    "documents.filter.documents": "文件 (*.pdf *.docx *.txt *.md *.csv);;所有檔案 (*)",
    "documents.import_transcripts": "匯入舊對話",
    "documents.filter.transcripts": "對話紀錄 (*.md *.txt *.json *.jsonl);;所有檔案 (*)",
    "scope.project": "這個專案的",
    "scope.machine": "這台機器的",
    "scope.logs": "當掉的時候留下的",
    "written_by.project_json": "專案精靈（敏感度）、設定頁（驗證指令）",
    "written_by.sessions": "專案精靈（選幾個 NODE）、陣容頁",
    "written_by.rules": "專案精靈（第③題）、使用者自己改",
    "written_by.ai_settings": "系統精靈、AI 設定頁",
    "written_by.presets": "系統精靈、陣容頁的「存成模式」",
    "written_by.cli_paths": "啟動檢查的「選擇位置…」",
    "written_by.projects": "開新專案、開啟舊專案",
    "written_by.ui": "使用者按的每一個收合、亮暗切換",
    "audit.kind.needs_human": "需要人員決策",
    "audit.kind.human_answered": "人員答覆",
    "audit.kind.codex_approved": "Codex 自行批准",
    "audit.answered_by.user": "使用者",
    "audit.answered_by.codex": "Codex（未經人員）",
    "export.kyc.authorised": "使用者授權匯出（高敏感度專案）",
    "veto.destructive_delete": "破壞性刪除指令",
    "veto.database_drop": "資料庫刪除",
    "veto.database_wipe": "資料庫全表刪除",
    "veto.rewrite_history": "改寫 Git 歷史",
    "veto.force_push": "強制推送",
    "veto.system_settings": "修改系統設定",
    "veto.permissions": "修改權限",
    "veto.secrets": "疑似機密資訊",
    # 出廠三個角色在畫面上的名字（`app/core/lineup.py`）。使用者改過名字的
    # 不走這裡——那是他自己輸入的字，翻譯不准碰。

    "common.none": "無",
    # --- 那份驗收標準算不算數 ---------------------------------------------
    # 三種身分各一句話。**不准合併成一句「未核准」**——
    # 「我改了一個字」跟「我從來沒看過」要的是完全不同的下一步。
    "rubric.never_looked_at": (
        "這份驗收標準還沒有人過目，所以它還不算一把尺。"
        "請你自己看一遍那幾個維度，確認它們量的是你在意的事，再按「這份可以」。"
    ),
    "rubric.changed_since_approval": (
        "這份驗收標準在 {at} 被過目過，但**之後被改動了**。"
        "改過的那一版還沒有人看過，所以它暫時不算數——請再看一遍再按一次。"
    ),
    "rubric.problem.nothing_to_approve": "這個專案還沒有驗收標準，沒有東西可以核准。",
    "memory.review_unreadable": (
        "讀不懂複查那一格對記憶更新的回覆，所以**沒有動任何一份共同記憶**。"
        "草案已經存進這一次執行的紀錄（memory-update-proposal.md），"
        "請你自己看一遍再決定要不要套用。"
    ),
    # --- 2026-08-24 搬家最後一批 ---------------------------------------
    "cloud.at_line": "第 {n} 行",
    "cloud.in_filename": "檔名",
    "discover.place.npm_unknown": "npm 的全域安裝位置（問不到 npm）",
    "library.column.project": "專案",
    "library.column.source": "來源",
    "library.nothing_picked": "未選",
    "project.not_a_folder": "所選路徑不是有效資料夾。",
    "project.not_initialised": "此資料夾缺少 .aicc\\\\task.md，尚未初始化為 AI Control Center 專案。",
    "common.content": "內容",
    "common.sync": "同步",
    "log.workflow_result_banner": "===== 工作流程結果 =====",
    "start.no_task_yet": "還沒有任務內容（.aicc\\\\task.md）",
    "session.unnamed": "未命名的 {provider} 對話",
    "session.unnamed_prefix": "未命名的 ",
    "chain.broken_link": "與前一筆接不上——中間有紀錄被刪除或插入",
    "chain.digest_mismatch": "內容與指紋不符——這一筆被改過",
    "discover.place.system_path": "系統 PATH",
    "skip.aicc_not_portable": ".aicc 裡不可攜的部分",
    "skip.client_local_only": "客戶專案的逐字紀錄，不離開這台機器",
    "skip.runtime": "執行期產物（runtime、conflicts、快取）",
    "skip.secret_or_local": "本機專屬或含機密（.env、鎖檔、資料庫暫存）",
    "skip.vcs_or_venv": "版本控制與虛擬環境（.git、.venv）",
    "verify.join.and": " 且 ",
    "verify.join.or": " 或 ",
    # 大括號要寫兩次：`t()` 一律跑 `.format()`，而這句話裡的 `{` 是
    # **JSON 的一部分**，不是變數。寫一次的話它會被當成 `{"exit_codes"}`
    # 這個變數名去找，然後丟 MissingText。
    "verify.success_example": (
        '{{"exit_codes": [0]}}（離開碼 0 算過）或 '
        '{{"output_pattern": "(?m)^\\d+ passed"}}（輸出出現這個樣式算過）'
    ),
    "common.browse": "瀏覽",
    "common.close": "關閉",
    "common.run": "執行",
    "import.unrecognised_format": (
        "認不出這個對話的格式，沒有匯入。"
        "目前支援 ChatGPT 的 mapping／messages 與 Claude 的 chat_messages。"
    ),
    # 第四個入口（另外三個是 `lineup.*` 那三句）。同一條規則、不同的下一步：
    # 這裡壞掉的不是使用者剛才按的那一格，是**一整張存起來的表格**，所以要
    # 先講清楚是表格裡的哪幾格。
    "preset.empty_lineup": "空的陣容存不成模式。",
    "preset.needs_a_name": "模式要有名字。",
    "queue.nothing_to_start": "佇列排程：這一輪沒有可以自動開始的專案。",
    "queue.stale_lock_hint": "佇列排程：上面那些鎖定如果沒有其他視窗正在跑，就是殘留的鎖定，儀表板會標出來。",
    "search.background_only": "只能當背景資料",
    "search.matching_part": "相關片段",
    "search.nothing_to_preview": "沒有可預覽的訊息。",
    "search.resumable": "可續接",
    "search.speaker.user": "使用者",
    "stamp.cannot_tell": "（算不出來。）",
    "stamp.kind.review": "審查章",
    "stamp.no_evidence": "（沒有留下證據）",
    "stamp.no_verdict_at_all": "規則層回了 None：這一次沒有算出任何判定",
    "stamp.none_at_all": "（一個章都沒有。）",
    "vendor.folder_missing": "找不到 Claude 桌面版的對話清單資料夾。請先用桌面版開過一次，它才會把資料夾建出來。",
    "vendor.no_session_id": "沒有對話編號",
    "vendor.not_a_record": "寫進去的不是一筆紀錄",
    "vendor.readback_mismatch": "讀回來的內容跟寫進去的對不上：",
    "vendor.registered": "已登記到 Claude 的對話清單",
    "wizard.look.day": "亮",
    "wizard.look.night": "暗",
    "project.task_placeholder": "請在此描述要交給 Claude 執行的任務。",
    "dash.action.decide": "決定",
    "dash.action.view": "檢視",
    "dash.action.resume": "接續",
    # 卡片背面那張明細的欄位名。**它們是 key 送去頁面當標題用的**，
    # 所以要翻——頁面照收到的字畫，不認得任何一個代號。
    "dash.detail.state": "狀態",
    "dash.detail.pairing": "配對",
    "dash.state.idle": "待命",
    "dash.state.done": "完成",
    "dash.state.error": "錯誤",
    # 上一次執行**宣稱自己還在跑，而沒有任何東西替它背書**。
    # 它跟「執行中」長得不一樣，是這一整件事的重點——見
    # `ProjectDashboardService.is_really_running`。
    "dash.state.interrupted": "中斷未完成",
    # 卡片背面那一列的欄位名，以及兩種中斷各自的下一步。
    "dash.interrupted_at": "上一次執行",
    # 護欄擋下了他寫的哪一句（❓15，2026-08-27 拍板）。
    # **要有他寫的原話**——一句「有指示被忽略」等於叫他自己去猜是哪一句。
    "dash.blocked_by_rule": "被護欄擋下的指示",
    "dash.blocked_pair": "你寫的「{quote}」──被「{rule}」擋下",
    "dash.interrupted.never_started": "還沒開始就中斷了。直接重跑，不會少掉什麼。",
    "dash.interrupted.stopped_midway": "跑到一半中斷了。紀錄留在 .aicc/runs 裡，重跑前可以先看一眼。",
    "stamp.required.criteria": "驗收標準",
    "stamp.required.build": "實作",
    "stamp.required.verify": "驗證",
    "stamp.result.pass": "通過",
    "stamp.result.fail": "失敗",
    "stamp.result.gated": "沒得驗",
    "sensitivity.personal": "個人專案",
    "sensitivity.client": "客戶專案",
    "sensitivity.kyc": "高敏感度／KYC",
    "chain.split_version": "這些章蓋在不同版本的內容上",
    "stamp.machine.needs_evidence": "機器章要有證據（跑了什麼、輸出怎麼判定的）。沒有證據的機器章就是一個穿著機器外衣的審查章",
    "stamp.machine.needs_result": "機器章要有結果",
    "stamp.machine.no_reasoning": "機器章不准帶理由——它記的是量出來的東西，不是誰的判斷。要寫判斷的話蓋的是審查章",
    "stamp.needs_digest": "章一定要綁在某一版內容上，否則它永遠不會失效",
    "stamp.needs_index": "章要有編號",
    "stamp.needs_role": "章要說是誰蓋的",
    "stamp.review.needs_reasoning": "審查章要寫為什麼同意。只記「誰、何時、哪一版」的章，事後回溯時回答不了最重要的那一題：憑什麼",
    "stamp.review.no_evidence": "審查章不准帶證據；要附證據的話蓋的是機器章",
    "stamp.review.no_result": "審查章不准帶結果——「我同意」不是一個量出來的東西",
    "gate.too_many_auto": "Codex Decision Gate 已連續自動處理 3 個 Claude 問題。為避免無限來回，請確認下一步。",
    # 決策關卡沒有人可以派。**這句話講的是「為什麼直接問你」，不是一個錯誤**
    # ——那一題本來就要有人回答，只是這份陣容裡沒有一格能先替他判斷。
    # 念出他排的那幾格，因為他要對照的正是「我排的這幾格裡哪一格該改」。
    "gate.no_seat_to_judge": (
        "這一題直接問你：決策關卡要請職責是「複查」的那一格先判斷要不要打擾你，"
        "而這份陣容的 {count} 格（{seats}）裡一格都不是"
        "——AICC 不替你挑一格代打，那一格會替你回答一個你沒有授權的問題。"
        "想讓它以後先自動判斷，到陣容頁把其中一格的職責改成「複查」。"
    ),
    "handoff.first_import": "首次導入 AI Control Center",
    "handoff.saved_to_memory": "已將最終接手摘要保存至共同記憶：",
    "handoff.summary_title": "專案接手摘要",
    "lineup.no_reviewer_after_work": "東西做出來了，但陣容裡沒有任何一格的職責是「複查」，所以沒有人審過它。這不是失敗，是少一個章——請到陣容頁加一格複查。",
    "lineup.no_reviewer_note": "這條線上沒有任何一格的職責是「複查」，所以這一輪沒有人複查過產出。這不是失敗——最終判定會照實反映少了哪些章。",
    # 上面那一句在**陣容裡有讀不出來的位子**時是假的：那一格是他排的，
    # 只是這一版讀不懂它某一欄。照上面那句講，他會以為是自己沒排複查。
    "lineup.no_reviewer_but_some_unreadable": "這一輪沒有人複查過產出，而**原因可能不是你沒排**：這份陣容裡有 {count} 格這一版讀不出來（{names}），它們沒有被派工。設定還在檔案裡，沒有被刪掉。先去陣容頁看那幾格，再決定要不要重跑。",
    "lineup.no_worker": "這個專案的陣容裡沒有任何一格的職責是「執行」，所以沒有人會產出東西。請到陣容頁把其中一格的職責改成執行。",
    # --- 「這條序列要派給某一種職責，而陣容裡一格都沒有」------------------
    #
    # 四句，一種職責一句。**不是一句帶 `{duty}` 變數的通稿**：每一種缺法
    # 的出路不一樣（缺複查可以換序列，缺執行可以把討論回合設成 0），
    # 而一句填空的通稿只講得出「去陣容頁改職責」——那是四句話裡最沒用的
    # 那一半。挑哪一句由 `workflow_worker.why_this_sequence_cannot_run()`
    # 決定，那支有自己的測試。
    #
    # 序列的名字用 `{sequence}` 代，**不寫死**：這個產品裡同一件事一度有
    # 兩個名字（設定面板叫「計畫發想」、陣容頁徽章叫「計畫序列」），
    # 而這句話的用途正是讓他在那兩個畫面之間對照。在這裡再抄一次那個詞，
    # 等於替第三個名字先開好一扇門。
    #
    # 每一句都要塞得進 `outcomes.STAGE_MAX_CHARS`（240 字）。超過的話
    # `_last_words()` 切的是**前面**，而前面那半句正是「為什麼沒跑」。
    # 所以「不替你挑一格代打」在這裡只留一句話，完整的理由住在
    # `orchestrator.NoSeatWithDuty` 與 `outcomes.py` 的註解裡。
    "lineup.no_seat_with_duty.review": (
        "這一輪跑的是「{sequence}」，而它每一步都派給職責是「複查」的那一格。"
        "這份陣容的 {count} 格（{seats}）裡一格都不是，所以一次派工都沒有發生"
        "——AICC 不替你挑一格代打。"
        "兩條路：到陣容頁把其中一格的職責改成「複查」，"
        "或到「AI 執行設定」把序列換成「{other}」。"
    ),
    "lineup.no_seat_with_duty.work": (
        "「{sequence}」要有人審計畫，而那一步派給職責是「執行」的那一格。"
        "這份陣容的 {count} 格（{seats}）裡一格都不是"
        "——AICC 不替你挑一格代打。"
        "兩條路：到陣容頁把其中一格的職責改成「執行」，"
        "或把「討論回合」設成 0，0 回合只出一份計畫、不需要有人審。"
    ),
    "lineup.no_seat_with_duty.plan": (
        "這一步派給職責是「擬做法」的那一格，"
        "而這份陣容的 {count} 格（{seats}）裡一格都不是"
        "——AICC 不替你挑一格代打。"
        "請到陣容頁把其中一格的職責改成「擬做法」。"
    ),
    "lineup.no_seat_with_duty.approve": (
        "這一步派給職責是「核准」的那一格，"
        "而這份陣容的 {count} 格（{seats}）裡一格都不是"
        "——AICC 不替你挑一格代打。"
        "請到陣容頁把其中一格的職責改成「核准」。"
    ),
    # 維護共同記憶那兩步派不出去。**跟上面那四句是兩件事**：上面講的是
    # 「這一輪沒有開始」，這兩句講的是「跑完了，只有最後那一步沒有人做」
    # ——所以每一句的**第一件事**都是先說產出還在，他才不會整輪重跑。
    #
    # 兩句不是一句：缺複查是沒有人擬草稿，缺執行是草稿擬得出來但沒有人審，
    # 而**這個產品不把一份沒有人看過的東西寫進八份共同記憶**。
    # 挑哪一句由 `workflow_worker.why_memory_was_not_maintained()` 決定。
    #
    # 一樣要塞得進 `outcomes.STAGE_MAX_CHARS`（240 字）：切的是前面，
    # 而前面那半句正是「你的程式沒事」。
    "memory.no_seat_with_duty.review": (
        "程式做完了、驗證過了，產出都在。"
        "只有最後一步沒做：維護共同記憶要先請職責是「複查」的那一格擬草稿，"
        "而這份陣容的 {count} 格（{seats}）裡一格都不是"
        "——AICC 不替你挑一格代打。"
        "到陣容頁把其中一格的職責改成「複查」，或到「AI 執行設定」"
        "把「自動維護共同記憶」關掉。"
    ),
    "memory.no_seat_with_duty.work": (
        "程式做完了、驗證過了，產出都在。"
        "只有最後一步沒做：共同記憶的草稿要請職責是「執行」的那一格審過才算數，"
        "而這份陣容的 {count} 格（{seats}）裡一格都不是"
        "——沒有人看過的東西，AICC 不寫進共同記憶。"
        "到陣容頁把其中一格的職責改成「執行」，或到「AI 執行設定」"
        "把「自動維護共同記憶」關掉。"
    ),
    "step.claude_resuming_after_answer": "Claude 正在依照使用者答案繼續",
    "step.claude_resuming_after_gate": "Claude 正在依 Codex 判斷繼續",
    "step.codex_drafting_plan": "Codex 正在建立計畫初稿",
    "step.codex_judging_question": "Codex 正在判斷 Claude 問題",
    "step.maintaining_memory": "正在維護共同記憶",
    "verify.fix_instruction": "修正造成測試失敗的問題，並保持原始任務範圍。",
    "verify.tests_failed": "測試未通過。",
    "parse.approve_without_reason": "核准那一格說可以拍板，但沒有寫理由。一個沒有理由的「我同意」，事後回溯時回答不了最重要的那一題：憑什麼。",
    "parse.both_kinds": "兩種都寫了",
    "parse.both_prefixes": "兩個開頭都寫了",
    "parse.consult_without_target": "它說要討論，但沒有指名要問哪一格。引擎不會替它挑一個——挑了就變回「問上一關」，而那正是被否決的做法。",
    "parse.gate_not_object": "Codex Decision Gate 輸出不是 JSON 物件。",
    "parse.gate_unreadable": "Codex Decision Gate 輸出格式無法解析。",
    "parse.memory_no_json": "Claude 未輸出 JSON，沿用 Codex 記憶草案。",
    "parse.memory_not_object": "Claude 記憶審查格式不是物件，沿用 Codex 記憶草案。",
    "parse.neither_kind": "兩種都沒寫",
    "parse.neither_prefix": "兩個開頭都沒寫",
    "parse.no_reason_given": "（沒有寫理由）",
    "parse.reject_without_target": "核准那一格不同意拍板，但沒有指名要退給誰。引擎不會替它挑一個——挑了就變回「退回上一關」。",
    "parse.review_unreadable": "無法解析 Codex 審查結果。",
    "brief.heading": "## 這個專案的計畫書（跟使用者確認過的）\n\n",
    "git.changed_files": "變更檔案：\n",
    "git.diff_empty": "Git diff 為空。",
    # 新建的檔案 `git diff` 看不到。這幾句是它們在「這一輪做出來的東西」
    # 裡的樣子——**每一句都要標明是「新增」**，不然下游分不出「改了一行」
    # 跟「整個檔案是這一輪寫出來的」。
    "git.new_files.header": (
        "=== 新增檔案（git diff 看不到未追蹤的檔案，以下是它們的內容）："
        "共 {count} 個 ==="
    ),
    "git.new_file.header": "--- 新增檔案 {path}（{lines} 行 / {chars} 字）",
    "git.new_file.cut_lines": "；只放了前 {shown} 行，其餘 {dropped} 行沒有放進來",
    "git.new_file.cut_chars": "；只放了前 {shown} 字，其餘 {dropped} 字沒有放進來",
    "git.new_file.no_room": (
        "--- 新增檔案 {path}（{chars} 字）：內容沒有放進來，"
        "前面的新增檔案已經用完這一段的字數上限 ---"
    ),
    "git.new_file.binary": (
        "--- 新增檔案 {path}（{size} bytes，不是文字檔）：內容沒有放進來 ---"
    ),
    "git.new_file.unreadable": "--- 新增檔案 {path}：讀不到內容（{error}）---",
    # 檔名留著、內容不送。**兩件事都要**：內容送出去違反「憑證永不外送」，
    # 而整條刪掉的話，那一輪真的產生的檔案在最後一關等於不存在。
    "git.new_file.secret": (
        "--- 新增檔案 {path}：內容沒有放進來（這種檔名裝的通常是憑證，"
        "而憑證不送給廠商）---"
    ),
    "git.new_file.runtime": (
        "--- 新增檔案 {path}：內容沒有放進來（相依套件／執行期狀態，"
        "不是這一輪做出來的東西）---"
    ),
    "step.gate_answered": "Codex 已代為回答 Claude 問題，Claude 繼續同一個工作流。",
    "step.intake": "需求確認",
    "step.intake_running": "第一格正在跟使用者確認需求，問清楚之後才會有人動手。",
    "step.memory_drafting": "Codex 正在整理本次任務後的共同記憶更新草案。",
    "step.memory_updated": "已更新共同記憶：",
    # 對話清單上那一列的開頭，講「這一輪是哪一段」。
    # `step.planner` 已經併進 `role.planner`——它講的就是那一格的名字，
    # 而同一件事兩個 key 的下場是：改了一個，另一個留在舊講法。
    #（2026-08-25 `seat.*` 整組併進 `role.*`，這一句跟著改。）
    "step.consult": "討論",
    "step.approve": "核准",
    "step.approach": "做法",
    # 不寫死「開發工程師」與「實作」：使用者的座位可能叫別的名字，
    #【不是所有使用者都寫程式】那條也否決了「實作」這個預設。
    "step.planning_running": "規劃那一格正在擬定做法，動手的那一格會照它做。",
    "step.question_to_gate": "Claude 提出問題，已交給 Codex 判斷是否需要詢問使用者。",
    "step.sent_for_review": "已將 Claude 輸出、測試結果與 Git diff 送交 Codex 審查。",
    "handover.decisions_stay": "決策紀錄留在舊機",
    "handover.git_done": "已經有 git 了。",
    "handover.git_note": "舊機這個專案有 git，但 `.git` 不會被打包（太大，而且該由遠端來）。從遠端 clone 之後把檔案放回去，或在這裡重新 `git init`。",
    "handover.line_credentials": "重新輸入 LINE 通知的憑證",
    "handover.line_done": "已經設定好了。",
    "handover.line_note": "舊機有設定 LINE，但憑證存在 %APPDATA% 而且是用 Windows DPAPI 加密的——綁這台機器跟這個帳號，**複製過來也解不開**，只能重新輸入。在那之前，等待回答超過十分鐘不會有任何通知。",
    "handover.new_sessions": "第一次執行會開新的對話",
    "handover.rebuild_index": "重建文件索引",
    "handover.reconnect_git": "接回版本歷史",
    "handover.sessions_done": "已經在這台機器上跑過了。",
    "handover.sessions_note": "舊機的對話紀錄留在舊機的 CLI 裡，接不過來，所以那些 id 在匯入時清掉了。模型要靠共同記憶與文件重新進入狀況，不是靠上一段對話。",
    "cloud.auto.on_finish": "工作流程完成後自動同步",
    "cloud.auto.on_start": "程式啟動後自動同步",
    "cloud.auto.on_switch": "切換專案後自動同步",
    "cloud.auto.title": "自動同步",
    "cloud.enable": "啟用 Google Drive 同步",
    "cloud.folder_label": "Google Drive 資料夾",
    "cloud.pick_folder": "選擇 Google Drive 資料夾",
    "cloud.pick_folder_hint": "請選擇 Google Drive for desktop 的本機資料夾",
    "cloud.scope_note": "只同步 .aicc 任務、政策、handoff、notes、prompts 與執行結果。不會同步原始碼、.env、LINE 金鑰、資料庫或客戶文件。",
    "export.project_root": "（專案根目錄）",
    "export.scan_not_run": "尚未執行安全掃描。",
    "export.stopped.index": "匯出已停止：無法把文件索引寫回單一檔案，打包出去的索引會是空的。",
    "export.stopped.kyc": "此專案已標記為 sensitive-KYC，目前版本不支援匯出成可攜 Workspace。",
    "export.stopped.secrets": "匯出已停止：預計打包的檔案中包含疑似 token、密碼或 secret。",
    "export.will_not_take": "不會帶走的：",
    "export.will_take": "會帶走的：",
    "cloud.blocked_uploads": "\n已擋下不上傳：",
    "cloud.kyc_disabled": "此專案已標記為 sensitive-KYC，Google Drive 同步已停用。",
    "cloud.link.no_project_json": "找不到所選雲端專案的 project.json。",
    "cloud.link.pick_existing_git": "請選擇已存在的本機 Git 專案資料夾。",
    "cloud.not_enabled": "Google Drive 同步尚未啟用。",
    "cloud.security_scan": "安全掃描",
    "cloud.sync_done": "Google Drive 同步完成：",
    "cloud.withheld_by_sensitivity": "\n依敏感度留在本機：",
    "audit.export_filename": "決策紀錄.jsonl",
    "audit.export_refused": "無法匯出",
    "audit.exported": "已匯出",
    "audit.exported_but_unverified": "已匯出，但完整性檢查沒有通過",
    "audit.for_machines": "給程式驗的",
    "audit.for_people": "給人看的",
    "audit.kyc_warning": "這是高敏感度（sensitive_kyc）專案。紀錄內含原始問答，可能有客戶資料，AICC 沒有遮蔽功能。\n匯出必須逐次授權並寫明理由——理由本身會寫進紀錄裡，而且這次授權不會被記住。",
    "audit.pick_destination": "選擇匯出位置",
    # --- 時間怎麼講成人話（`app/services/relative_time.py`）-----------------
    # 這一組是英文最會出事的地方：中文的「3 分鐘前」不分單複數，英文的
    # `minutes ago` 要。**翻的時候一個都不准漏**——漏掉的那一句會印出
    # `1 minutes ago`，而它排版正常、不會報錯、沒有人會回報。
    "time.just_now": "剛剛",
    "time.minutes_ago": "{n} 分鐘前",
    "time.hours_ago": "{n} 小時前",
    "time.days_ago": "{n} 天前",
    "time.under_a_minute": "不到 1 分鐘",
    "time.minutes": "{n} 分鐘",
    "time.hours": "{n} 小時",
    "time.hours_minutes": "{h} 小時 {m} 分",
    "time.days": "{d} 天",
    "time.days_hours": "{d} 天 {h} 小時",
    "time.minutes_seconds": "{m} 分 {s} 秒",
    "sessions.cloud_note": "\n\n掃描不到的雲端網頁對話仍需先由 Claude Code / Codex 在本機建立或匯出摘要；AICC 不會直接讀取 Claude.ai 或 ChatGPT 網頁聊天室。",
    "sessions.unreadable": "\n\n找不到可讀取的對話內容。\n",
    "title_source.claude_task": "Claude 任務名稱",
    "title_source.guessed_from_content": "內容推測",
    "title_source.manual": "手動命名",
    "title_source.official": "官方名稱",
    "title_source.project_folder": "專案資料夾",
    # 這一句跟 `library.column.name` 是**同一串字、不同的東西**：
    # 一個是欄位標題，一個是「這個名字從哪裡來」。
    # 2026-08-24 我為了讓「不准兩個 key 同一句話」那條檢查過，把這裡改成
    # 「對話自己的名稱」——**老闆退了，而他是對的**：為了讓檢查過得去而改
    # 使用者看得到的字，順序反了。改回來，改的是那條檢查。
    "title_source.session_name": "對話名稱",
    "title_source.summary": "對話摘要",
    "title_source.system_id": "系統代號",
    "lineup.role_required": "角色不可為空",
    "lineup.slot_needs_name": "位子要有名字",
    "lineup.slot_needs_name_why": "位子要有名字。沒有名字的節點，駁回的時候指不到。",
    # --- （2026-09-01：這裡本來有三句「會寫檔案的位子只能是 Claude」。
#      那條規則沒有人問過老闆，已經整條拿掉，三句話跟著刪。
#      見 `app/core/lineup.py` 那一段。）------------------------------
    #
    # 三句而不是一句：**擋下來要講得出下一步**，而三條路的下一步不一樣。
    # 一句共用的「不行」會讓使用者在三個地方各卡一次，而他不知道要去改哪裡。
    # 共同的那半句（「會寫檔案的位置只能是 {allowed}」）刻意每一句都寫一次
    # ——拆成一句共用的開頭再接三個尾巴，翻譯的時候會拼出不成句的東西。
    "lineup.parent_is_itself": (
        "「{role}」這一格接到它自己身上了。一條接自己的線畫不完，"
        "而這一格也永遠等不到它自己的東西。留空代表接前面那一格。"
    ),
    "lineup.parent_not_seated": (
        "沒有接：這條線上沒有一格叫「{parent}」，"
        "所以「{role}」接不到它。先確認那一格的名字（改過名字的話用新的），"
        "或者留空讓它接回前面那一格。"
    ),
    "lineup.parent_runs_later": (
        "沒有接：「{parent}」排在「{role}」後面，"
        "而一格收不到還沒跑的那一格產出的東西。"
        "先把「{parent}」往前移，再接一次。"
    ),
    "lineup.move_would_break_a_link": (
        "沒有移動：這樣移完之後「{role}」會排到「{parent}」前面，"
        "而它是接在「{parent}」後面的——一格收不到還沒跑的那一格產出的東西。"
        "先把「{role}」那條線改接到別格，再移一次。"
    ),
    "lineup.removing_would_orphan": (
        "沒有移除：「{orphans}」是接在「{role}」後面的，"
        "而「{role}」是這條線的第一格，移掉之後那幾格接不到任何一格。"
        "先把它們改接到別格，再移除「{role}」。"
    ),
    # --- 「插在這兩格中間」那顆 ⊕ 送回來的位置讀不出來，兩句話（2026-08-27）--
    #
    # 兩句而不是一句，理由同上面那幾組：**擋下來要講得出下一步**，而兩條路的
    # 下一步不一樣（一個是頁面壞了或有人手改，一個是畫面停在上一份陣容）。
    #
    # 兩句都明講「陣容一個字都沒有動」。這裡的拒絕跟別處不同的地方是：
    # 他按的是一顆**加東西**的按鈕，而加東西失敗最容易被讀成「加了，只是我
    # 沒看到」——他接下來會去找那一格，或者再按一次。
    "lineup.insert_position_unreadable": (
        "沒有加：這顆加號送回來的位置讀不出來（{value}）。"
        "位置要是一個格號（0 代表插在最前面），留空代表加在最後。"
        "陣容一個字都沒有動，畫面已經退回引擎裡真正的那一份——"
        "請在上面重新挑一次要插在哪兩格中間。"
    ),
    "lineup.insert_position_off_the_line": (
        "沒有加：這顆加號要插在第 {value} 格前面，而這條線上只有 {seated} 格。"
        "畫面可能停在上一份陣容（別的地方剛動過這條線）。"
        "陣容一個字都沒有動，畫面已經退回引擎裡真正的那一份——"
        "請在上面重新挑一次要插在哪兩格中間。"
    ),
    "lineup.unknown_writes_switch": (
        "不認得的寫檔開關：{value}（目前只有「on」與「off」，"
        "留空代表照這一格的職責決定）。這一格先不會被派工，"
        "原本的設定一個字都沒有動——請到陣容頁重新選一次。"
    ),
    "review.pass": "複查回報 PASS。",
    "review.pass_without_reason": "複查回報 PASS，但沒有寫理由。",
    "step.collecting_diff": "正在取得 Git 審查資料",
    "approval.missing_stamp_note": "\n\n少一個章不是卡在那一關，是整件事還沒有可以回溯的憑據。",
    # **一行，不准換行。**這一句會原樣變成 `outcome_detail`，而
    # `outcomes._last_words()` 只取最後一行——換行的話，畫面上只剩尾巴那一句。
    "approval.nothing_was_produced": (
        "這一輪跑完了，但一個檔案都沒有改到——沒有產出就沒有東西可以拍板，"
        "所以沒有派核准那一格。先看看交辦的內容是不是漏了講要改什麼，"
        "或者到這一輪的執行紀錄看它實際做了什麼。"
    ),
    "approval.not_decidable_yet": "這一輪跑完了，測試也過了，但**還不能拍板**：\n\n",
    "approval.skipped_red_stamp": "有機器章是紅的，所以沒有派核准那一格——它的意見蓋不掉一次真的跑出來的失敗。紅的是：",
    "step.running_tests": "正在執行測試",
    "intake.clear_enough": "交辦內容本身已經寫得夠清楚，第一輪就訂得出驗收標準，沒有需要回問使用者的地方。",
    "consult.self_target": "它指名要跟自己討論。一格自己問自己，答案一定跟它已經想的一樣，而這一輪要多跑兩次。",
    "step.claude_working": "Claude 正在執行任務",
    "rules.read_before_starting": "**你這一格開工之前要先讀：",
    "diag.searched_here": "找過這些地方：\n",
    "settings.never_changed": "尚未變更",
    "documents.import_folder": "匯入資料夾",
    "documents.purge_missing": "清掉找不到檔案的索引紀錄",
    "documents.secrets_found": "這幾個檔案裡有疑似機密的內容",
    "common.no_content_paren": "（沒有內容）",
    "file.empty": "檔案是空的",
    "file.missing": "檔案不存在",
    "file.unreadable": "讀不到檔案",
    "line.message_required": "LINE 訊息內容不可為空。",
    "line.not_configured": "LINE 尚未設定 Channel Access Token 與 User ID。",
    "line.sent": "LINE 通知已送出。",
    "line.timed_out": "LINE 請求逾時。",
    "line.token_required_full": "LINE Channel Access Token 不可為空。",
    "ai.saved_as_global_default": "目前設定已儲存為 AICC 全域預設值。",
    "common.collapse": "收合",
    "common.expand": "展開",
    "common.unlimited": "不限",
    "profile.custom": "自訂",
    "profile.deep": "深入",
    "profile.standard": "標準",
    "profile.thrifty": "省額度",
    "unit.rounds": " 回合",
    "unit.times": " 次",
    "verbosity.concise": "精簡",
    "verbosity.detailed": "詳細",
}


class MissingText(KeyError):
    """`STRINGS` 裡沒有這個 key。

    **不回 key 本身。**回 key 的話畫面上會出現 `credential.claude.why`
    這種東西——看起來像一個字串、排版正常、沒有人會發現。
    """


def t(key: str, /, **values: object) -> str:
    """那一句話。

    第一個參數**只能用位置傳**（那個 `/`）。不然模板裡不能有叫 `key` 的
    變數——而 `"{key} 要是一個物件"` 是一句非常自然的錯誤訊息，寫的人
    完全不會想到它撞到函式自己的參數名。
    （那個 TypeError 2026-08-24 真的發生過一次。）

    有 `{名字}` 的用 `t(key, 名字=值)` 填。**填不到的變數丟例外**，
    不是留一個 `{名字}` 在畫面上——那跟查不到 key 是同一個病。
    """
    try:
        template = STRINGS[key]
    except KeyError as exc:
        raise MissingText(
            f"介面文字裡沒有 {key!r}。"
            "新的字串要先加進 `app/i18n.py`，不要寫死在程式裡。"
        ) from exc
    # **不能因為呼叫端沒給變數就跳過 format。**跳過的話，一句
    # 「找不到 {tool}。」會**原封不動**印到畫面上——那個 `{tool}` 看起來
    # 像是程式壞了，而且沒有任何東西會報錯。（這個洞是這個檔案寫完
    # 第一次跑就掉進去的。）
    try:
        return template.format(**values)
    except (KeyError, IndexError) as exc:
        raise MissingText(
            f"{key!r} 這句話要 {exc} 這個變數，但呼叫的時候沒有給。"
        ) from exc


def keys() -> tuple[str, ...]:
    """所有的 key。守衛用它比對。"""
    return tuple(STRINGS)
