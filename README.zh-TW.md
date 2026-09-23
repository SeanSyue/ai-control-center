# AI Control Center（中文說明）

> 這是專案原本的中文說明。英文版與目前的限制清單在 [README.md](README.md)。
> **介面目前只有繁體中文。**

AI Control Center 是一個本機優先的桌面工具，用來把 Claude Code 與 Codex 的本機對話、專案資料、共同記憶、測試與通知流程整合在一起。

它的主要目標不是當雲端電腦，也不是文件管理器，而是讓使用者可以：

1. 找到既有 Claude / Codex 對話。
2. 將兩邊對話配對到同一個專案。
3. 讓 AICC 協調規劃、開發、測試、Codex Review、Decision Gate 與記憶更新。
4. 只有遇到真正需要使用者決策的時候才停下來。

## 目前版本

目前開發版本以 `app/ui/main_window.py` 顯示的版本為準。

近期重點：

- Conversation-first 入口。
- Claude / Codex 左右雙欄配對。
- 專案文件索引與 context budget。
- 共同記憶維護。
- 長任務 Decision Gate。
- 多專案 Dashboard 與佇列控制。
- Workspace 匯出前安全掃描。
- 舊 ChatGPT / Claude 對話 context-only 匯入。

## 啟動

建議使用專案虛擬環境。以下指令都在專案根目錄執行：

```powershell
.\.venv\Scripts\python.exe -m app.main
```

或使用：

```text
啟動 AI Control Center.cmd
```

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`pytest.ini` 已限制測試只收集 `tests/`，避免 `_update_backup`、`.venv`、sandbox runtime、快取資料造成 import mismatch。

## 打包成安裝程式

給不會用 CLI 的使用者，可以打包成一個雙擊安裝的 `Setup.exe`。分兩步，順序不能反：

```powershell
# 1) PyInstaller：把 app/main.py 連同 PySide6 打包成 dist\AI Control Center\（onedir 模式）
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist --workpath build "packaging\AI Control Center.spec"

# 2) Inno Setup：把上一步的產出包成安裝精靈
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "packaging\AI Control Center.iss"
```

輸出在 `packaging\Output\AI Control Center Setup.exe`。**採用 QWebEngineView 之後，安裝檔從 37 MB 變成約 152 MB**——多出來的是 QtWebEngineProcess.exe 與 Chromium 資源，這是嵌入設計稿 HTML 的已知代價。spec 檔必須把 `app/ui/web/*` 列進 `datas`，否則裝起來的版本面板會是空白的（程式碼找得到、資料檔找不到）。這個安裝程式是每人（per-user）安裝、`PrivilegesRequired=lowest`，正常執行不會跳 UAC——因為 app 的設定/專案清單本來就寫在 `%APPDATA%`，不需要系統管理員權限。已驗證過完整的安裝→啟動→解除安裝流程（含模擬全新使用者、`%APPDATA%`/`%USERPROFILE%` 都是空的情境）都正常，登錄檔的解除安裝項目、開始選單捷徑、桌面捷徑（可選）都會正確建立與移除。

`packaging/AI Control Center.spec`、`packaging/AI Control Center.iss` 兩個檔案都進版本控制；`dist/`、`build/`、`packaging/Output/` 都是建置產物，`.gitignore` 已排除。

## 重要資料夾

- `app/`：正式程式碼。
- `tests/`：自動測試。
- `tools/`：輔助 CLI 工具。
- `.aicc/knowledge/`：AICC 自己的共同記憶與產品文件（PROJECT_BRIEF、ARCHITECTURE、DECISIONS、SECURITY_MODEL、PRODUCT_REVIEW、BUSINESS_MODEL、NEXT_STEPS、OPEN_QUESTIONS、PITCH）。自 2026-08-05 起**不再版本控制**（見下方「不應提交的資料」），本機一定要有這份資料夾內容，開發時才讀得到這些共同記憶——新機器/新 clone 下來會是空的，屬於預期行為。
- `sandbox/demo_project/`、`sandbox/demo_project2/`：測試用 demo 專案，各自有自己獨立的 `.git`，用來手動驗證多專案並行等行為。

## 網頁面板（feat/dashboard-webengine 分支）

儀表板等六個面板的畫面是嵌在 `QWebEngineView` 裡的 HTML，設計稿在上一層的
`AICC-視覺概念\`。設計稿更新後：

```powershell
.\.venv\Scripts\python.exe app\ui\web\build_pages.py    # 複製進 app/ui/web/，比對 CSS 雜湊
.\.venv\Scripts\python.exe tools\verify_web_pages.py    # 用 offscreen QtWebEngine 實際載入每一頁
```

`verify_web_pages.py` 刻意不是 pytest 測試——它會啟動 QtWebEngine，而殘留的
view 會讓測試在直譯器結束時 SIGSEGV。

## 不應提交的資料

- `.venv/`
- `_update_backup/`（若存在——舊更新流程留下的備份資料夾，目前這個工作目錄裡沒有）
- `.tmp.driveupload/`
- `logs/`
- `runtime/`
- sandbox 各專案的 `.aicc/` 執行資料（`runs/`、`timeline/`、`sessions.json` 等，`.gitignore` 已排除，但仍會佔用本機磁碟空間，可安全刪除）
- token、密碼、`.env`、LINE access token、Claude/Codex 登入資料
- `.aicc/knowledge/`（AICC 自己的產品/商業共同記憶——內容本來就存在於這個路徑，`orchestrator.py`/`cloud_sync.py` 對它的讀取邏輯完全不用改，只是從 2026-08-05 起不再進版本控制，避免這個 repo 未來不管是私有還是公開推上 GitHub 時，商業策略/定價相關內容進到 commit 歷史裡）

## Roadmap 與交接

- 開發方向與各階段狀態：`ROADMAP.md`。
- 接手一個新視窗/新對話前，先讀 `NEXT_WINDOW_HANDOFF.md`。
