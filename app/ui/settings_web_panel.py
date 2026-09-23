"""Settings panel — renders settings_v1.html.

The page merges what Python sends over its own defaults (`{...S, ...d}`), which
means **a key that is not pushed shows the page's demo value**. So every control
with a real backing store is pushed explicitly, and the three that have none are
called out rather than filled in:

* `export_scan` - the secret scan before a workspace export is unconditional;
  `workspace_portability` has no skip flag. Still not pushed, and the design
  session turned that control into a statement of fact rather than a toggle. A
  toggle that changes nothing is worse than no toggle: the user will believe
  they turned something off.

`gate_on` and `long_task` were in that list until ROADMAP P3 gave them real
backing stores, and the way they got one is the point: both are settings
*because* something now reads them. `gate_on` is the list the Decision Gate
prompt's ASK_USER sentence is built from, and `long_task_mode` decides whether
a run writes a milestone plan at all - which it previously did unconditionally,
which is exactly why the toggle could not exist before. Adding the field
without changing what the orchestrator does would have produced the same lie in
a new place.
"""
from __future__ import annotations

from app.i18n import t

from datetime import datetime
from pathlib import Path

from app.services.ai_settings import (
    AISettingsStore,
    gate_categories,
    gate_floor_clauses,
)
from app.services.project_dashboard import DashboardQueueStore
from app.services.project_registry import FAIL_CLOSED_SENSITIVITY, ProjectRegistry
from app.ui.web_panel import WebViewPanel

# The page's vocabulary on the left, the store's on the right.
EFFORT = {"low": "low", "medium": "medium", "high": "high"}
# 這一張表 2026-08-24 之前**兩邊都寫錯**，而且錯法跟底下 `PERMISSION` 那一段
# 記的是同一個病——只是沒有人回頭看隔壁那一格：
#
#   1. 鍵寫成 `concise`／`balanced`／`detailed`，但頁面送過來的是
#      `low`／`medium`／`high`（`seg('verbosity',[['low',…],['medium',…],
#      ['high',…]])`），所以查表一定落空，直接吃 `.get()` 的預設值；
#   2. 值寫成 `low`／`medium`／`high`，而 `claude_verbosity` 的合法集合是
#      `VALID_VERBOSITY = {"concise","standard","detailed"}`，所以就算查得到，
#      `normalized()` 也會把它換掉。
#
# 兩個錯疊起來的結果實測是：**三個選項全部存成 `concise`**，而推回畫面的值是
# `balanced`——那不是頁面上任何一個選項的值，所以「回覆詳細度」那三格
# **從來沒有一格是亮的**。使用者按哪一個都一樣，而且看不出來自己按過。
#
# 頁面自己有偵測這件事的機制（`checkEcho` 比對「我剛送出的」與「推回來的」），
# 但它只寫進 console，而 App 的 QWebEnginePage 沒有覆寫
# `javaScriptConsoleMessage`——那句話連 stderr 都到不了。
VERBOSITY = {"low": "concise", "medium": "standard", "high": "detailed"}
# 對不上就落在中間那一檔。跟 `PERMISSION_FALLBACK` 不同的是這裡沒有寬鬆／嚴格
# 之分（詳細度不影響安全），所以取的是「不會替使用者放大也不會縮小」的那一個。
VERBOSITY_FALLBACK = "standard"
# 頁面的字 → 全 app 通用的字（`AIExecutionSettings.VALID_CLAUDE_PERMISSION_MODES`，
# 也就是原生設定面板一直在用的那一組，最後原封不動送進 `--permission-mode`）。
#
# 這張表原本是 `{"ask": "ask", "acceptEdits": "edit", "auto": "auto"}`，兩處都錯：
#
#   1. `"ask"` 與 `"edit"` 根本不在合法集合裡，`normalized()` 會把它們改掉；
#   2. 鍵寫成 `"acceptEdits"`，但頁面送的是 `"edits"`，所以連查都查不到，
#      直接吃 `.get()` 的預設值。
#
# 兩個錯疊起來的結果是**三個選項全部存成 `auto`**——最寬鬆的那一個。使用者
# 在客戶專案上選「每次問」，拿到的是全自動，而同一頁還印著「客戶或 KYC 專案
# 請維持『每次問』」。一個做不到自己承諾的權限開關，比沒有開關更糟。
PERMISSION = {"ask": "default", "edits": "acceptEdits", "auto": "auto"}
# 對不上就往嚴格的那邊倒，不是往寬鬆的那邊。跟 `import_files(allow_flagged=False)`
# 同一條原則：沒想過這件事的呼叫端拿到的是安全的那一邊。
PERMISSION_FALLBACK = "default"


def _reverse(mapping: dict[str, str], value: str, fallback: str) -> str:
    for key, mapped in mapping.items():
        if mapped == value:
            return key
    return fallback


class SettingsWebPanel(WebViewPanel):
    PAGE = "settings_v1.html"

    def __init__(self, project_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.store: AISettingsStore | None = None
        self.queue_store = DashboardQueueStore()
        self._project: Path | None = None
        self._saved_at: datetime | None = None
        if project_path is not None:
            self.set_project(project_path)

    def set_project(self, project_path: Path) -> None:
        self._project = Path(project_path)
        if self.store is None:
            self.store = AISettingsStore(self._project)
        else:
            self.store.set_project(self._project)
        # 2026-08-24 之後沒有「未儲存的編輯」這回事——每一次改動當場落地，
        # 所以切專案不需要丟掉任何東西。
        self.push_state()

    # ---- data -----------------------------------------------------------
    def collect_state(self) -> dict:
        if self.store is None:
            return {"mode": self._mode}
        # **直接讀檔案。**
        #
        # 這裡本來要先看有沒有「還沒存的編輯」，理由寫得很長：`_apply` 記完
        # 就推狀態，而推的是檔案裡的舊值，於是每一個開關按下去都會彈回來。
        #
        # 那整個問題 2026-08-24 消失了——**改動當場就寫進檔案**，所以推的
        # 就是他剛剛改的那個值。少一個「畫面上的值」與「檔案裡的值」可能
        # 不一致的狀態，就少一整類 bug。
        try:
            settings = self.store.load_project()
        except Exception:
            settings = self.store.load_global()

        try:
            concurrency = self.queue_store.load().concurrency_limit
        except Exception:
            concurrency = 1

        # **答不出來的時候倒向最嚴的那一級，不是最寬的。**
        #
        # 這兩個 `except` 本來都寫死 `"personal"`——三級裡最寬鬆的那一個，
        # 而它決定的是這個專案的逐字紀錄准不准往雲端同步、准不准匯出。
        # `ProjectRegistry.get_sensitivity()` 自己的契約寫得很清楚（讀不到、
        # 解析不出來、認不得的值一律回 `sensitive_kyc`），這一側卻在它之外
        # 又補了一個往反方向倒的預設——**兩份判斷遲早分岔，而分岔的那一次
        # 是往鬆的那一邊**。
        #
        # 而且它不會被看出來：畫面上那個下拉選單會停在「個人」，跟他親手
        # 選「個人」長得一模一樣（同 `sensitivity_source` 那一則在講的病）。
        profile = FAIL_CLOSED_SENSITIVITY
        if self._project is not None:
            try:
                profile = ProjectRegistry.get_sensitivity(self._project)
            except Exception:
                profile = FAIL_CLOSED_SENSITIVITY

        state = {
            "mode": self._mode,
            "claude_model": settings.claude_model,
            "codex_model": settings.codex_model,
            "effort": settings.claude_effort,
            # 這兩個回合數**各自只有一條序列讀得到**：計畫序列讀 planning_rounds，
            # 開發序列讀 max_fix_attempts。所以要連 workflow_mode 一起推——畫面靠它
            # 決定畫哪一列。Qt 面板那邊是 setEnabled(planning) 兩行（ai_settings_panel.py:630），
            # 網頁這邊 2026-08-27 之前**只畫規劃那一列而且無條件畫**：出廠序列
            # 換成開發之後，他看到的是一個沒有選項被選中、而且這條序列從頭到尾
            # 不會讀的欄位，同時看不到真正會生效的那一個。
            "workflow_mode": settings.workflow_mode,
            "planning_rounds": settings.planning_rounds,
            "max_fix_attempts": settings.max_fix_attempts,
            "verbosity": _reverse(VERBOSITY, settings.claude_verbosity, "medium"),
            "permission": _reverse(
                PERMISSION, settings.claude_permission_mode, "ask"
            ),
            "concurrency": concurrency,
            "profile": profile,
            # Both have real backing stores now (ROADMAP P3): gate_on is the
            # ASK_USER category list the Decision Gate prompt is built from,
            # long_task decides whether a run writes a milestone plan at all.
            # Sent as a list so the page can render one toggle per category.
            "gate_on": list(settings.gate_on),
            "gate_options": [
                {"key": key, "label": label}
                for key, label in gate_categories().items()
            ],
            # The clauses that always apply and cannot be switched off. Sent
            # rather than left for the page to hard-code, so what the user is
            # told always matches what the Decision Gate prompt says - a screen
            # claiming "these always ask" while the prompt disagreed would be
            # the more convincing of the two, and wrong.
            #
            # That was true as an intention and false as a description until
            # 2026-08-11: this key was pushed and the page never read it, so
            # both copies existed and only one was authoritative. Shaped like
            # `gate_options` (label + note) because the page already renders
            # that shape, and because a label-only list would have left the
            # notes hard-coded - half the drift, still silent.
            "gate_floor": [
                {"label": label, "note": note}
                for label, note in gate_floor_clauses()
            ],
            "long_task": settings.long_task_mode,
            "drive_sync": self._drive_sync_enabled(),
            # **這一格不是開關，是事實陳述**，頁面照這個值印「會推播／不會推播」。
            #
            # LINE 那一側沒有「開／關」這個東西可以存：`LineNotifier` 只有憑證，
            # 有 token 就會發、沒有就不會發。頁面本來把它畫成開關，而 `_apply`
            # 沒有它的分支——按下去亮起來、什麼都沒存、下一次 push_state 彈回去。
            # 補一個假的旗標會更糟（那是一個關不掉的開關），所以照
            # `export_scan` 的既有做法：**沒有開關的事情就不要畫成開關。**
            "line_notify": self._line_token_set(),
            "line_token_set": self._line_token_set(),
            # **這一格是時間戳，不是一句話。**
            #
            # 本來在還沒存過的時候塞 `t("settings.never_changed")`（「尚未變更」），
            # 而頁面把這一格包進「已儲存 · ${S.saved_at}」的模板裡——於是每次
            # 重開 App，設定頁底部那一行都寫著**「已儲存 · 尚未變更」**：
            # 兩句話互相否定，而使用者的設定其實已經在磁碟上生效。
            #
            # 頁面那一側本來就寫對了（`S.saved_at ? … : ''`，沒有時間就整行不印），
            # 是這一側送了一個非空字串把那個守衛繞過去。**「沒有值」要用 None 講，
            # 不要用一句散文講**——散文在下游會被當成資料用。
            "saved_at": (
                self._saved_at.strftime("%H:%M") if self._saved_at else None
            ),
        }
        return state

    def _drive_sync_enabled(self) -> bool:
        """Google Drive 同步現在是不是開著。

        本來寫的是 `CloudSyncSettings.load()`——而 `CloudSyncSettings` 是一個
        dataclass，`load()` 在 `CloudSyncConfigStore` 身上。`AttributeError`
        被底下那個 `except Exception` 吃掉，**於是這一格永遠回「關著」**，
        即使同步正在跑、檔案正在上傳。

        跟隔壁 `_line_token_set` 修過的那個 bug 是同一個形狀：一個對不上的
        型別，加上一個什麼都吞的 `except`，合起來變成一句看起來很合理、
        而且永遠不會變的話。所以 `except` 也一起收窄——`CloudSyncConfigStore()`
        建構時會 `mkdir`，那是真的答不出來（OSError），不是「沒有啟用」。
        """
        try:
            from app.services.cloud_sync import CloudSyncConfigStore

            return bool(CloudSyncConfigStore().load().enabled)
        except (RuntimeError, OSError):
            return False

    def _set_drive_sync(self, enabled: bool) -> None:
        """把「Google Drive 同步」那個開關真的寫進機器層設定。

        這一格本來是**頁面上按得動、引擎這一側沒有分支**的開關：`_apply` 的
        elif 鏈一路走到 `else: return`，不寫檔、也不推狀態。使用者看到開關
        亮起來，檔案裡什麼都沒變，下一次任何 `push_state` 把舊值推回來時它
        自己彈回去——`app/ui/web/settings_v1.html` 的 `checkEcho()` 正是在
        偵測這件事，但它只寫進 console，而 App 的 QWebEnginePage 沒有覆寫
        `javaScriptConsoleMessage`，那句話連 stderr 都到不了。

        **開不起來的時候要說一句話，不是靜靜地不開。**沒有選資料夾就把
        `enabled` 寫成 True，`is_configured()` 仍然是 False，於是同步一次都
        不會發生而開關是亮的——那是同一個謊換一個地方講。`reject()` 會跳出
        原因並把畫面推回真正的值，兩件事一起做。
        """
        from app.services.cloud_sync import CloudSyncConfigStore

        store = CloudSyncConfigStore()
        settings = store.load()
        if enabled and not settings.root_path:
            self.reject(t("cloud.settings.folder_required"))
            return
        settings.enabled = enabled
        store.save(settings)
        self._saved_at = datetime.now()
        self.push_state()

    def _line_token_set(self) -> bool:
        """設定頁的「LINE 已設定」指示燈。

        原本是 `LineNotifier().load_credentials().get("channel_access_token")`
        ——而 `load_credentials()` 回的是 `tuple[str, str]`，不是 dict。
        `.get()` 丟 `AttributeError`，被底下那個 `except Exception` 吃掉，
        **於是這一格永遠是「沒設定」**，即使 LINE 正常在發通知。

        兩件事同時出錯才會變成這樣：一個對不上的型別，加上一個什麼都吞的
        `except`。而錯的方向讓它更難發現——它不是壞掉，它是安靜地一直說同一句
        看起來很合理的話。

        改成問 `is_configured()`：那是同一個問題的既有答案，兩份判斷遲早會
        分岔。`except` 留著但收窄理由——`LineNotifier()` 在沒有 `APPDATA` 時
        會拋，那是真的答不出來，不是設定不存在。
        """
        try:
            from app.services.line_notifier import LineNotifier

            return LineNotifier().is_configured()
        except (RuntimeError, OSError):
            return False

    # ---- actions --------------------------------------------------------
    def handle_action(self, kind: str, arg: str) -> None:
        if kind.startswith("set:"):
            self._apply(kind.split(":", 1)[1], arg)
        elif kind in ("save", "revert"):
            # **這兩個動作 2026-08-24 之後沒有意義了：每一次改動當場就存。**
            #
            # 不是把它們拿掉不理，是**明著收下再什麼都不做**：舊版頁面還會
            # 送這兩個過來，而一個沒有人收的動作在畫面上跟「收了但沒反應」
            # 長得一模一樣。收下再推一次狀態，畫面至少會跟硬碟同步。
            #
            # （`revert` 更是不能假裝還原：已經寫進檔案了。畫面上那顆按鈕
            # 要拿掉——見交接給頁面設計 2026-08-24-G。）
            self.push_state()

    def _apply(self, key: str, value: str) -> None:
        if self.store is None:
            return
        settings = self.store.load_project()
        changed = True
        if key == "claude_model":
            settings = settings.__class__(**{**settings.to_dict(), "claude_model": value})
        elif key == "codex_model":
            settings = settings.__class__(**{**settings.to_dict(), "codex_model": value})
        elif key == "effort":
            settings = settings.__class__(**{**settings.to_dict(), "claude_effort": value})
        elif key == "planning_rounds":
            settings = settings.__class__(
                **{**settings.to_dict(), "planning_rounds": int(value or 0)}
            )
        elif key == "max_fix_attempts":
            settings = settings.__class__(
                **{**settings.to_dict(), "max_fix_attempts": int(value or 0)}
            )
        elif key == "verbosity":
            settings = settings.__class__(
                **{
                    **settings.to_dict(),
                    "claude_verbosity": VERBOSITY.get(value, VERBOSITY_FALLBACK),
                }
            )
        elif key == "permission":
            settings = settings.__class__(
                **{
                    **settings.to_dict(),
                    "claude_permission_mode": PERMISSION.get(
                        value, PERMISSION_FALLBACK
                    ),
                }
            )
        elif key == "concurrency":
            self.queue_store.set_concurrency_limit(int(value or 1))
            changed = False
        elif key == "profile" and self._project is not None:
            ProjectRegistry.set_sensitivity(self._project, value)
            changed = False
        elif key == "gate_on":
            # A comma-joined list of category keys, per the page's handoff.
            # normalized() drops unknown ones and fixes the order, so a stale
            # page sending a category this build no longer has is harmless.
            chosen = tuple(part for part in (value or "").split(",") if part)
            settings = settings.__class__(**{**settings.to_dict(), "gate_on": chosen})
        elif key == "long_task":
            settings = settings.__class__(
                **{**settings.to_dict(), "long_task_mode": value not in ("", "false", "0")}
            )
        elif key == "drive_sync":
            # 機器層的設定，不在專案的 AISettings 裡，所以自己寫自己推。
            self._set_drive_sync(value not in ("", "false", "0"))
            return
        else:
            # export_scan still has no backing store - the export path always
            # scans and there is nothing to turn off, so a toggle would be
            # decorative. The design session has been told.
            #
            # **走到這裡就是「按了沒事」**，而畫面上它跟「按了、存好了」長得
            # 一模一樣——`drive_sync` 與 `line_notify` 就是這樣掉進來的。
            # 守著這條的是 `tests/test_a_setting_you_picked_comes_back_the_same.py`：
            # 頁面畫得出來的每一個控制項，送過來時都不准落到這一支。
            return

        # **當場寫進去，不排隊。**
        #
        # 老闆 2026-08-24 拍板：拿掉「儲存變更」那顆按鈕，全部立刻落地。
        #
        # 在這之前這一頁有**兩種存檔模型**，而且是在同一頁上：`concurrency`
        # 與敏感度那兩格本來就是按了就存（上面那兩個 `changed = False`），
        # 其餘的排隊等按鈕。那顆按鈕因此對一半的控制項說謊——他改完敏感度，
        # 按鈕亮起「有未儲存的變更」，而那一格早就寫進檔案了。
        #
        # 頁面設計建議的是「按鈕對卡片灰掉＋寫一句說明」，老闆否決：
        # **一顆對一半控制項灰掉的按鈕，本身就是那個問題的第二個版本**
        # ——他還是要先看那顆按鈕是不是灰的，才知道剛才那一下有沒有生效。
        #
        # 不需要 undo：設定看得見，改回去就是再點一下。
        if changed and self.store is not None:
            self.store.save_project(settings)
        self._saved_at = datetime.now()
        self.push_state()
