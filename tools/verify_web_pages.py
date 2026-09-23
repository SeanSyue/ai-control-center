"""Verify the embedded web panels load, wire up and render.

From the design folder verify_pages.py, pointed at this repo copies.
Not a pytest test: it starts QtWebEngine and leftover views made the suite exit 139.

Every page is measured inside a shown QWebEngineView, never a bare
QWebEnginePage. A page with no view has a 0x0 viewport, and that is not a
harmless inaccuracy: at zero width *every* `max-width` media query matches, so
the whole run silently checks the phone layout instead of the desktop one. It
also made one check pass for the wrong reason - `?embed=1` hid `.side`, but so
does `@media(max-width:860px)`, so that assertion held even when `body.embed`
did nothing. VIEWPORT_W/H below are asserted on every page so nobody can drop
the view for speed and quietly go back to measuring nothing.

`?embed=1` itself is gone: the content pages no longer draw a sidebar to hide.
The shell is its own page loaded twice, so the query string is now per-page
(`part=side` / `part=top`) rather than one constant appended to everything.

None of the above asks whether anything opens the page. It cannot: every check
here is a measurement of one file in isolation, and a retired page loads,
renders and passes exactly like a live one. conversation_library_v2.html did,
inside "全部 9 頁通過", for the whole of ROADMAP P10 after the 陣容 page took
its slot. So the report ends with where each page is mounted - read out of
main_window by tools/page_wiring.py - and a page nothing opens is a failure
rather than a ninth pass.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "app" / "ui" / "web"

# Run as a script, sys.path[0] is tools/. The mounting report reads app.ui and
# tools.page_wiring, and imports them by the same names the test suite uses.
sys.path.insert(0, str(ROOT))

# Desktop width, above every breakpoint the pages define (1180 is the largest).
# QT_QPA_PLATFORM=offscreen means showing the view opens no actual window.
#
# **這台機器上的預設，不是唯一要驗的一檔。**頁面設計 2026-08-24 查到的：
#
# > 現在鎖死 1400×900，上面那兩個真問題一條都量不到。
# > 我建議跑兩檔，**出廠那一檔失敗算失敗**。
#
# 他們說的兩個問題是：`page_stack` 有 760 的最小高度，而出廠視窗是
# 980×720——出廠大小的使用者從來沒看過設定頁那顆「儲存變更」，而決策橫幅
# 一捲頁面就被捲出畫面上緣。**在 1400×900 底下那兩件事一條都量不到**，
# 於是新的幾何檢查上線第一天就是綠的。
#
# 出廠大小從 `main_window` 那一行 `resize(...)` 抄過來——**抄一份就會漂開**，
# 所以是 import 進來的。
VIEWPORT_W, VIEWPORT_H = 1400, 900

# `--viewport 980x720` 換一檔。跑兩檔的方式是跑兩次。
if "--viewport" in sys.argv:
    _index = sys.argv.index("--viewport")
    _w, _, _h = sys.argv[_index + 1].partition("x")
    VIEWPORT_W, VIEWPORT_H = int(_w), int(_h)

# How many sidebar entries there should be, asked of the side that decides.
# A literal here is a third copy of the page list, and it would be the copy
# nobody updates - `.shell-nv: 7` sat here while the list had 9.
from app.ui.shell_panel import PAGES as _SIDEBAR_PAGES  # noqa: E402
from app.ui.shell_panel import PAGE_GROUPS as _SIDEBAR_GROUPS  # noqa: E402

# 這支工具會印非 ASCII，而 Windows 的預設輸出編碼是 cp950：編不出來的字元會讓
# 整支變成 traceback。危險的地方是**它只在有話要說的時候才炸**——`✗` 只在失敗
# 時才印，所以一切正常的時候完全看不出來。
# 這一行不是為了好看：一支工具的輸出就是它唯一的產物。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# The shell is checked as two loads of one file. Both halves are listed because
# they render from different branches of the same render() - the sidebar was
# fine while the execution bar was still blank, and one entry would have covered
# for the other.
#
# `part=top` is the half that must never be clipped: 13 actions is the execution
# bar's 11 plus the decision banner's 「查看並回答」/「稍後」, so a count below
# that means the banner did not draw - and the banner is how the user learns a
# project is waiting for them.
SHELL_CHECKS = (
    # `.shell-nv` 原本寫 7，而清單有 9 項——比對是「至少 N 個」，所以那個數字
    # 少兩項也照樣通過，包含少掉的正好是側邊欄漏掉一頁的那種情況。改成問
    # Python 自己有幾頁。
    #
    # `.group-start` 是決策紀錄上面那條分隔線。它不是裝飾：上面八項是「做事」，
    # 這一項是「證明做過的事」。自用驗收清單一度把它列成「還沒接」，而它其實
    # 一直都在——**沒有東西在驗它，所以也沒有東西能反駁那句話**。
    (
        "part=side",
        {
            ".shell-side": 1,
            ".shell-nv": len(_SIDEBAR_PAGES),
            ".shell-nv.group-start": len(_SIDEBAR_GROUPS),
            ".brand": 1,
        },
    ),
    (
        "part=top",
        {
            ".shell-exec": 1,
            ".shell-gate": 1,
            "[data-shell-act]": 13,
            "[data-shell-set]": 2,
            # The read-only line-up summary, beside the button that starts
            # spending money.
            ".shell-exec .lineup": 1,
        },
    ),
)

# 每頁載入後要檢查的選擇器：至少要有這麼多個元素，否則代表 render() 沒跑或畫錯
CHECKS = {
    "dashboard_v5.html": {
        ".lane": 3, ".star": 1, ".grp": 2, ".row": 1,
        "#ccSlots .slot": 1, "#queue .qi": 1, "#today .stat": 1,
    },
    # The composer. `.note.bad` is the independence reading (2026-08-27: it used
    # to sit on the connector between two seats as `.gap.bad`; the connector now
    # says what is handed over, and independence lives in the hint bar below).
    # The demo data is deliberately same-vendor and
    # same-source so the red state has to be drawn - if someone later makes the
    # defaults look tidy, this stops being exercised.
    #
    # `.addslot.off` used to be asserted here, on the reasoning that the seat
    # cap is whatever the engine dispatches - two executed roles, so the add
    # button must be disabled. That check passed for two seats, three seats
    # and every other number, because the cap it compared against was the
    # *sequence's* dispatch count and the development sequence gives every
    # seat a duty. `slots >= executed` was a tautology, so the button was
    # disabled always and this line called that correct.
    #
    # Now the ceiling comes from the engine (`lineup.max_slots`) and the demo
    # sits two seats under a ceiling of three, so the button must be *live*.
    # `.addslot[data-add]` is the click target, not just the paint.
    "lineup_v1.html": {
        # `[data-add]`（不是 `.addslot[data-add]`）：「＋ 加一格」2026-08-25
        # 從那一列的尾端搬到標題列的按鈕——留在裡面的話四格時它自己會捲出
        # 畫面，也就是使用者按了加一格之後，他剛按的那顆鈕不見了。
        # **這一條要跟著改，不是刪掉**：它守的是「那顆鈕在」。
        # ⚠️ 頁面設計 2026-08-27 動了這一行（老闆當天拍板的改動連帶）。
        # `.gap.bad` → `.note.bad`：兩張卡片中間那道縫改成講「這裡傳什麼過去」，
        # 獨立性整個移到下面那條提示條——一個位置回答一個問題。
        # **斷言是搬家不是拿掉**：太像的那一對還是必須看得見，
        # 只是看得見的地方換了。設計端 `verify_pages.py` 同一條也一起搬了。
        # 覺得這條做壞了直接改回去，那是你們的檔案。
        # ⚠️ 頁面設計 2026-08-27 又動了這一行（老闆當天拍板「都幫我做」）。
        # `.gap` 拿掉：框變成二維之後（左右＝階層、同一層往下堆），
        # **兩張卡片中間那道縫不存在了**——線要從上一格的右邊拉到這一格的
        # 左邊，而那兩格可能不在同一列。
        # **斷言是搬家不是拿掉**：換成 `.line svg.links .lk`（框裡那幾條線）
        # 與 `.line .cell`（每一格在框裡的位置）。
        # 覺得這條做壞了直接改回去，那是你們的檔案。
        ".slot": 2, ".line .cell": 2, ".note.bad": 1, "[data-add]": 1, ".col": 2,
    },
    # 決策紀錄。`.ent.human` 對 `.ent.ai` 是這一頁的全部意義：「人看過」與
    # 「AI 自己批准」必須是兩種東西，混在一起這份紀錄就沒有用了。
    # `.integrity .limits` 守的是那段「證明不了什麼」的說明——它在三種狀態下
    # 都必須存在，包含全部通過的時候，因為那正是最容易被簡化成一個綠勾勾、
    # 也最可能被當成保證拿去給別人看的時候。
    "audit_v1.html": {
        ".ent.human": 3,
        ".ent.ai": 2,
        ".ent.pending": 1,
        ".ent.export": 1,
        ".integrity": 1,
        ".integrity .limits": 1,
    },
    # ctxBar 的分段代表「誰吃掉了 context 預算」；.it.trunc 是「只送前 N 字」那一份，
    # 少了它就代表又退回「送／不送」二元狀態，那會讓人以為 AI 讀過整份檔案。
    "knowledge_v1.html": {".mem": 3, "#ctxList .it": 6, "#ctxBar i": 2, "#ctxList .it.truncated": 1},
    "documents_v1.html": {".doc": 3, "#chips .chip": 3},
    # 4 個區段（Decision Gate 與長任務模式已移除，因為沒有後端）
    "settings_v1.html": {".grp": 4, ".r": 10, ".toc a": 4},
    # .rail .seg 是逐段上色的軌道。如果有人改回「單一長度的進度條」，
    # 這兩條會直接失敗 —— 那個寫法對亂序完成的階段會算出 0%。
    "roadmap_v1.html": {".pt": 5, ".ph": 5, ".it": 5, ".rail .seg": 4, ".rail .seg.on": 1},
    # 登入與安裝（憑證入口）。數量只守外殼，真正在守的是下面的
    # CREDENTIALS_PROBE —— 這一頁的每一種失敗都是「畫了一個看起來很正常
    # 的東西」，數量檢查一條都抓不到。
    #
    # **沒有這一筆的下場**：`report_mounting()` 會把它列成「有掛載卻不在
    # CHECKS 裡」，而在那條檢查出現之前，它會是「看起來全綠、其實沒驗到
    # 新頁」——十一頁通過裡有一頁根本沒被看過。
    "credentials_v1.html": {
        ".pv": 2,                    # 一行一個 provider
        ".pv .dot": 2,               # 每一列都要有狀態點
        "#verdict": 1,               # 「能不能開始」那一句結論
        "[data-act='recheck']": 1,   # 重新檢查（要有進行中狀態）
    },
}

# 在任何頁面腳本之前注入：把錯誤攔下來存進 window.__err
COLLECTOR = """
window.__err = [];
window.addEventListener('error', function(e){
  window.__err.push('error: ' + (e.message || e) + ' @' + (e.filename||'') + ':' + (e.lineno||''));
});
window.addEventListener('unhandledrejection', function(e){
  window.__err.push('rejection: ' + e.reason);
});
window.__ready = false;
"""

PROBE = """
(function(){
  var sel = %s, out = {};
  for (var k in sel) out[k] = document.querySelectorAll(k).length;
  return JSON.stringify({
    counts: out,
    errors: window.__err || [],
    ready: !!window.__ready,
    hasQWebChannel: typeof QWebChannel !== 'undefined',
    hasTransport: !!(window.qt && qt.webChannelTransport),
    drawsShell: !!document.querySelector('.shell-side, .shell-top, .shell-nv'),
    viewportW: window.innerWidth,
    viewportH: window.innerHeight,
    bodyChildren: document.body.children.length
  });
})()
"""


# knowledge_v1 專用：注入 Markdown 轉換器會吐出的每一種標籤，
# 確認每一個都有樣式。沒有這道檢查，樣式缺口只會在真實記憶檔載入時才現形。
TYPO_PROBE = r"""
(function(){
  var doc = document.getElementById('doc');
  if (!doc) return JSON.stringify({fail:['找不到 #doc']});
  var sample = '<h2>H2</h2><h3>H3</h3><h4>H4</h4>'
    + '<p>p <strong>b</strong> <em>i</em> <code>c</code> <a href="#">L</a></p>'
    + '<ul><li>u<ul><li>u2</li></ul></li></ul><ol><li>o</li></ol>'
    + '<pre><code>x</code></pre>'
    + '<table><thead><tr><th>H</th></tr></thead><tbody><tr><td>D</td></tr></tbody></table>'
    + '<hr><blockquote>q</blockquote>';
  var keep = doc.innerHTML;
  doc.innerHTML = sample;
  if (typeof wrapTables === 'function') wrapTables(doc);

  function cs(sel, prop){
    var el = doc.querySelector(sel);
    return el ? getComputedStyle(el).getPropertyValue(prop).trim() : null;
  }
  function transparent(v){ return !v || v === 'transparent' || /rgba\(0, 0, 0, 0\)/.test(v); }

  var fail = [];
  var h2 = parseFloat(cs('h2','font-size'));
  if (!(h2 > 0 && h2 < 20)) fail.push('h2 字級 ' + h2 + 'px —— 沒套用樣式，會撐爆版面');
  if (cs('ul','list-style-type') !== 'none') fail.push('ul 沒有自訂項目符號');
  if (parseFloat(cs('ol','padding-left')) < 8) fail.push('ol 沒有縮排');
  if (transparent(cs('code','background-color'))) fail.push('行內 code 沒有底色');
  if (transparent(cs('pre','background-color'))) fail.push('pre 程式區塊沒有底色');
  if (cs('table','border-collapse') !== 'collapse') fail.push('table 沒有樣式');
  if (transparent(cs('th','background-color'))) fail.push('th 沒有底色');
  if (cs('hr','border-top-width') === '0px') fail.push('hr 沒有樣式');
  if (parseInt(cs('strong','font-weight'), 10) < 700) fail.push('strong 沒有加粗');
  if (cs('em','font-style') !== 'normal') fail.push('em 仍是斜體 —— 中文斜體不好看，應改用顏色');
  if (cs('blockquote','border-left-width') === '0px') fail.push('blockquote 沒有樣式');
  if (!doc.querySelector('.tablewrap > table')) fail.push('table 沒被 wrapTables 包起來 —— 寬表會撐爆面板');

  doc.innerHTML = keep;
  if (typeof wrapTables === 'function') wrapTables(doc);
  return JSON.stringify({fail: fail});
})()
"""

# settings_v1 專用：把狀態清空，確認沒收到值時「不畫」而不是「畫預設值」。
# 這條直接測防編造行為 —— 原本 {...S, ...d} 的合併會讓沒推的 key 顯示頁面自己的預設值。
EMPTY_STATE_PROBE = r"""
(function(){
  if (typeof S === 'undefined' || typeof paint !== 'function')
    return JSON.stringify({fail:['找不到 S 或 paint()']});
  var keep = JSON.stringify(S);
  var warn = console.warn; console.warn = function(){};   // 清空是刻意的，別讓它的警告污染輸出
  S = { mode: S.mode, dirty: false, saved_at: null };   // 什麼設定值都沒有
  paint();
  console.warn = warn;
  var rows  = document.querySelectorAll('.r').length;
  var facts = document.querySelectorAll('.r .bdg').length;  // 「一律執行」是事實陳述，本來就該留著
  var sections = document.querySelectorAll('.grp').length;
  S = JSON.parse(keep); paint();                        // 還原

  var fail = [];
  if (rows > facts)
    fail.push('沒收到任何設定值時仍畫出 ' + (rows - facts) + ' 個控制項 —— 那些是編造的預設值');
  if (sections > 1)
    fail.push('沒收到任何設定值時仍有 ' + sections + ' 個區段沒被隱藏');

  // Decision Gate 的地板必須是 Python 推什麼就畫什麼。
  //
  // 這一頁曾經硬寫四筆自己的。它跟 Python 那份**當時內容一致**，所以沒有人
  // 發現——直到有人數了一下：兩行的字面早就跟 prompt 裡的說法不一樣了。
  // 真正的風險是條數：地板多一條，畫面會照樣說「下面四項」，等於向使用者
  // 承諾一件引擎不再做的事。
  //
  // 名稱層的檢查（pytest 那條「有沒有人讀這個欄位」）抓不到這個：頁面別處
  // 還是提到 gate_floor，名字在、讀法錯。所以這裡推一組**跟出廠完全不同**
  // 的地板進去，看畫出來的是不是它。
  S = JSON.parse(keep);
  S.gate_floor = [{label:'探針地板甲', note:'n1'},
                  {label:'探針地板乙', note:'n2'},
                  {label:'探針地板丙', note:'n3'}];
  paint();
  var head = document.querySelector('.floor-head');
  var drawn = [].slice.call(document.querySelectorAll('#s-gate .r'))
    .map(function(r){ return r.textContent; })
    .filter(function(t){ return t.indexOf('探針地板') >= 0; });
  if (drawn.length !== 3)
    fail.push('推了 3 條地板，畫出來 ' + drawn.length + ' 條 —— 這一頁還留著自己那份');
  if (!head || head.textContent.indexOf('3') < 0)
    fail.push('地板的條數寫死了：推 3 條，標題說「' + (head ? head.textContent : '不見了') + '」');

  S = JSON.parse(keep); paint();                        // 再還原一次
  return JSON.stringify({fail: fail});
})()
"""

# The three things the design session changed on 2026-08-08 after this side
# reported them. Ported from the design folder's own verify_pages.py, because
# that one runs against the authored files and this one runs against the built
# copies - and the built copies are what ship. A regression arriving through
# build_pages.py would be invisible to the other tool.
SHELL_TOP_PROBE = r"""
(function(){
  var fail = [];

  // 1. Both selects must be painted the panel's own surface colour.
  //
  //    Checked against --surface rather than against "is it transparent",
  //    which is what the design folder's copy of this probe asks. That version
  //    does not catch the bug it was written for: drop just the `background`
  //    declaration and Chromium paints an `appearance:none` select WHITE, not
  //    transparent, so the probe passes while the bar has a white box on it.
  //    Verified here by doing exactly that. It only reddened for them because
  //    their negative control removed the whole rule, taking `appearance:none`
  //    with it and handing the select back to the native theme - which does
  //    compute as transparent. A breakage coarser than the bug proves the
  //    check catches the breakage, not the bug.
  var swatch = document.createElement('div');
  swatch.style.backgroundColor =
    getComputedStyle(document.documentElement).getPropertyValue('--surface').trim();
  document.body.appendChild(swatch);
  var want = getComputedStyle(swatch).backgroundColor;
  swatch.remove();

  var sels = document.querySelectorAll('.shell-exec select');
  if (sels.length !== 2) fail.push('執行列應有 2 顆 select，實際 ' + sels.length);
  for (var i = 0; i < sels.length; i++) {
    var cs = getComputedStyle(sels[i]);
    if (cs.backgroundColor !== want)
      fail.push('第 ' + (i+1) + ' 顆 select 的底色是 ' + cs.backgroundColor
                + '，應為 --surface 的 ' + want);
    if (cs.borderTopWidth === '0px')
      fail.push('第 ' + (i+1) + ' 顆 select 沒有邊框');
  }

  // 2. Python pushes state once a second while a run is going. The partial
  //    update has to actually update, and must not close an open menu -
  //    a full innerHTML rebuild took the ⋯ menu down within a second of the
  //    user opening it.
  var more = document.getElementById('shellMore');
  if (!more) fail.push('找不到 ⋯ 選單');
  else {
    more.classList.add('open');
    var d = JSON.parse(JSON.stringify(DEMO));
    d.shell.run.elapsed = '09:59';
    Shell.render(d);
    if (!document.getElementById('shellMore').classList.contains('open'))
      fail.push('秒數更新把打開中的 ⋯ 選單關掉了');
    var t = document.querySelector('.status .t');
    if (!t || t.textContent !== '09:59')
      fail.push('秒數沒有更新到畫面上 —— 局部更新失效');
    document.getElementById('shellMore').classList.remove('open');
  }

  // 3. A finished run and a run that never started must not look the same.
  var d2 = JSON.parse(JSON.stringify(DEMO));
  d2.shell.run = { state:'done', label:'計畫完成' };
  Shell.render(d2);
  var dot = document.querySelector('.shell-exec .status.done .k');
  if (!dot) fail.push('state:"done" 沒有對應的樣式');
  else if (!/rgb\(95, 240, 200\)/.test(getComputedStyle(dot).backgroundColor))
    fail.push('完成狀態不是薄荷色，實際 ' + getComputedStyle(dot).backgroundColor);

  // 3.5 交接文件「還沒用眼睛看過的東西」那一節的三項（2026-08-11 補）。
  //
  //     那一節說它們「需要一次真實執行才看得到（會花錢）」，而三項全部
  //     只是「Python 推某個狀態時畫面長什麼樣」——把那個狀態推進來就量得到，
  //     一毛錢都不用花。它們原本的處境是那一節自己寫的那句話：
  //     **都有自動測試守著行為，沒有任何測試守著它長什麼樣。**
  var AQUA = /rgb\(79, 209, 255\)/, GOLD = /rgb\(255, 217, 142\)/;

  // (a) 正在跑的那一格要亮起來。三種壞法各自不同：都不亮（訊號沒接上）、
  //     亮錯格（映射錯）、跑完還亮著（清除漏了）。第三種最容易漏，
  //     因為它在畫面上看起來只是「還在跑」。
  var d3 = JSON.parse(JSON.stringify(DEMO));
  d3.shell.run = { state:'running', label:'執行中', elapsed:'01:00', active_slot:1 };
  Shell.render(d3);
  var seats = document.querySelectorAll('.shell-exec .lineup b');
  var lit = [].slice.call(seats).map(function (b, i) {
    return b.classList.contains('on') ? i : -1;
  }).filter(function (i) { return i >= 0; });
  if (lit.join(',') !== '1')
    fail.push('active_slot=1 時亮的是第 [' + lit + '] 格');
  else if (!AQUA.test(getComputedStyle(seats[1]).color))
    fail.push('正在跑的那一格不是水藍色，實際 ' + getComputedStyle(seats[1]).color);

  var d4 = JSON.parse(JSON.stringify(DEMO));
  d4.shell.run = { state:'done', label:'完成' };      // 沒有 active_slot
  Shell.render(d4);
  if (document.querySelector('.shell-exec .lineup b.on'))
    fail.push('跑完了還有一格亮著 —— 那看起來就只是「還在跑」，不會有人察覺');

  // (b) 「需要修正」是金色而且**不脈動**。脈動代表有人在等你，
  //     而這個狀態沒有人在等——它跟 gated 用同一個顏色，靠會不會動分辨。
  var d5 = JSON.parse(JSON.stringify(DEMO));
  d5.shell.run = { state:'partial', label:'需要修正',
                   stage:'Codex 要求 Claude 修正' };
  d5.shell.usage = { state:'developing' };
  Shell.render(d5);
  var pk = document.querySelector('.shell-exec .status.partial .k');
  if (!pk) {
    fail.push('state:"partial" 沒有對應的樣式 —— 它會跟失敗長得一樣');
  } else {
    if (!GOLD.test(getComputedStyle(pk).backgroundColor))
      fail.push('「需要修正」不是金色，實際 ' + getComputedStyle(pk).backgroundColor);
    if (getComputedStyle(pk).animationName !== 'none')
      fail.push('「需要修正」在脈動 —— 脈動代表有人在等你回答，這個狀態沒有');
  }

  // (c) 終結狀態時「原因」與「用量」並列，而且都沒有被裁掉。執行列在
  //     1400px 下本來就吃緊，這一格是最後才擠進去的。
  var stageEl = document.querySelector('.shell-exec .stage');
  var usageEl = document.querySelector('.shell-exec .usage');
  if (!stageEl || !usageEl) {
    fail.push('終結狀態沒有把「原因」與「用量」並列（找到 ' +
              (stageEl ? 'stage' : '') + (usageEl ? ' usage' : '') + '）');
  } else {
    // 原因不截斷、用量不夠寬才讓步 —— 所以只有原因這一段要求完整顯示。
    if (stageEl.scrollWidth > stageEl.clientWidth + 1)
      fail.push('失敗原因被裁掉了：' + stageEl.textContent);
    if (usageEl.getBoundingClientRect().width < 1)
      fail.push('用量那一段寬度是 0 —— 並列沒生效');
  }
  Shell.render(DEMO);

  // 4. 執行中橫幅（ROADMAP P4）。資料層 2026-08-10 就做完了，而畫面到
  //    2026-08-11 之前一個字都沒讀 —— 整個功能在螢幕上不存在。
  Shell.render(DEMO);
  var strip = document.querySelector('.shell-act');
  var pushed = DEMO.shell.activity || [];
  if (!strip) {
    fail.push('推了 ' + pushed.length + ' 個有事的專案，橫幅沒有畫出來');
  } else {
    var items = [].slice.call(strip.querySelectorAll('.it'));
    // 順序是 Python 排好的，依據是「漏看的代價」（waiting→error→running）。
    // 頁面再排一次就會變成照名字或照時間，那是另一個問題的答案。
    var drawn = items.map(function(i){ return i.dataset.shellGo; }).join(',');
    var want  = pushed.map(function(a){ return a.id; }).join(',');
    if (drawn !== want)
      fail.push('橫幅的順序不是 Python 給的：畫 [' + drawn + ']，推 [' + want + ']');

    // 聚焦中的那個要留在裡面。拿掉的話使用者正看著的專案會憑空消失、
    // 數字少一個，看起來像剛剛處理完了。
    var here = strip.querySelector('.it.here');
    if (!here) fail.push('聚焦中的專案被從橫幅裡拿掉了');
    else if (parseFloat(getComputedStyle(here).opacity) >= 0.95)
      fail.push('聚焦中的那一項沒有畫淡，跟其他還沒看過的長得一樣');

    // 顏色與脈動沿用執行列同一組語意。金色有兩種，靠「會不會動」分辨：
    // waiting 在等人 → 脈動；error 已經結束 → 不動。
    var w = strip.querySelector('.it.waiting .k');
    var e = strip.querySelector('.it.error .k');
    if (!w || getComputedStyle(w).animationName === 'none')
      fail.push('等人的那一項不脈動 —— 它跟「已經壞掉了」會分不出來');
    if (!e || getComputedStyle(e).animationName !== 'none')
      fail.push('失敗的那一項在脈動 —— 脈動代表有人在等，那一項沒有人在等');
    if (e && !/rgb\(255, 123, 166\)/.test(getComputedStyle(e).backgroundColor))
      fail.push('失敗不是玫瑰色，實際 ' + getComputedStyle(e).backgroundColor);

    // 點一下要真的送出去。arg 是專案 id，重用切換專案那條路。
    // 送到沒有由 Python 端接住（見 check_page 的 set:project 檢查）。
    items[0].click();
  }

  // 5. 按了不會有事的動作要停用並講原因（2026-08-11）。
  //
  //    示範資料擋的是**選單裡**的「立即同步」，不是執行列上的按鈕：選單要
  //    打開才看得到，那正是最容易實作成「條件算了但沒套上去」的位置。
  Shell.render(DEMO);
  var target = document.querySelector('[data-shell-act="sync_now"]');
  var free   = document.querySelector('[data-shell-act="line_settings"]');
  if (!target || !free) {
    fail.push('找不到選單裡的「立即同步」或「LINE 設定」，這一段的切法要跟著改');
  } else {
    if (!target.disabled)
      fail.push('blocked 裡有 sync_now，那顆按鈕還是可以按的');
    if (target.title !== DEMO.shell.blocked.sync_now)
      fail.push('停用了但沒講原因（title 是「' + target.title + '」）——'
                + '使用者會以為功能壞了，而不是知道等一下就可以');
    if (free.disabled)
      fail.push('沒有被 blocked 的動作也被停用了');
  }

  // 反方向：blocked 清空之後要恢復。少了這個，同步一結束那顆按鈕會一直
  // 停用到剛好有別的東西改變形狀為止。
  var open = JSON.parse(JSON.stringify(DEMO));
  open.shell.blocked = {};
  Shell.render(open);
  var again = document.querySelector('[data-shell-act="sync_now"]');
  if (again && again.disabled)
    fail.push('blocked 清空了，按鈕還是停用的 —— 它不會自己恢復');

  // 沒事的時候整條不畫。一行螢幕每天報告「目前沒事」，成本比價值高。
  var quiet = JSON.parse(JSON.stringify(DEMO));
  quiet.shell.activity = [];
  Shell.render(quiet);
  if (document.querySelector('.shell-act'))
    fail.push('沒有任何專案有事的時候，橫幅還是畫出來了');

  Shell.render(DEMO);
  return JSON.stringify({fail: fail});
})()
"""

# 陣容頁專用：三格 + 計畫序列。這是**唯一**會讓「誰會跑」跟座位順序分家的組合
# ——規劃者坐第一位，而計畫序列不派工給它——所以只有在這個組合下，用位置推
# 跟用職責推才會給出不同答案。示範資料是兩格開發序列，兩種推法一致，
# 於是這個 bug 在畫面上活了下來：三格時規劃者畫成會跑、審查者畫成不會跑，
# 剛好兩個都相反。
#
# 用 render() 真的畫一次，不是讀原始碼比字串：這一頁三種推論
# （會不會跑、能不能加、能不能移除）都是模板裡的三元運算式，
# 比對字串只會告訴你「有人改了這行」，不會告訴你「畫出來是對的」。
LINEUP_SEQ_PROBE = r"""
(function(){
  var fail = [];
  // **假資料要跟後端同一條判準。**這裡以前用 `role === 'author'` 推
  // can_write，而後端推的是 `slot.duty === 'work'`——兩邊判準不同，
  // 所以這支探針畫出來的那一格跟產品畫出來的不是同一格。
  var seat = function(role, prov, canRemove, duty){
    return {role:role, provider:prov, session_id:role.charAt(0),
            title:role, updated_rel:'剛剛', in_project:true, duty:duty,
            can_write: duty === 'work', can_remove: !!canRemove};
  };
  var d = JSON.parse(JSON.stringify(DEMO));
  // 引擎送的是 0 =「沒有上限」（2026-08-22 定案）。這裡以前推 3 進去，
  // 於是探針量的是一個**引擎再也不會送出的值**——它在測一個不存在的世界，
  // 而那種綠燈跟真的一模一樣。
  d.lineup.max_slots = 0;
  d.lineup.slots = [seat('planner','codex',true,'plan'),
                    seat('author','claude',false,'work'),
                    seat('reviewer','codex',false,'review')];
  d.sequence = {label:'計畫序列',
                jobs:{'1':'這一輪：審計畫','2':'這一輪：出計畫'},
                executed_slots:2};
  render(d);

  var slots = document.querySelectorAll('.slot');
  if (slots.length !== 3) { fail.push('畫出來只有 ' + slots.length + ' 格'); }
  else {
    var dead = [];
    for (var i = 0; i < 3; i++) if (slots[i].classList.contains('dead')) dead.push(i);
    if (dead.join(',') !== '0')
      fail.push('這次不會跑的應該只有第一位（規劃者），畫出來的是 [' + dead + ']');
    var job2 = slots[2].querySelector('.job').textContent.trim();
    if (job2 !== '這一輪：出計畫')
      fail.push('第三位（審查者）在計畫序列裡是「出計畫」，畫出來是「' + job2 + '」');
    var rm = [];
    for (var j = 0; j < 3; j++) if (!slots[j].querySelector('.rm').disabled) rm.push(j);
    if (rm.join(',') !== '0')
      fail.push('後端只肯移除選配的那一格，亮著的「移除」卻是 [' + rm + ']'
                + ' —— 其餘幾顆按下去畫面不會有任何變化');
  }

  var note = document.querySelector('.notes .note.warn');
  var text = note ? note.textContent : '';
  // 這一條守的是「提示**指名了**是哪一格閒著」，不是「提示裡有序數」。
  //
  // 原本只認「第一位」。2026-08-22 定案要把序數從畫面上拿掉（位置已經被
  // 左右順序講過一次，再寫一次序數是同一件事講兩遍），所以只認序數的話，
  // **照定案做完這一行就會變紅，而且看起來像回歸不像刻意的改動**。
  //
  // 兩種都認：序數是現況，名字是定案之後的樣子。序數那一項是過渡，
  // **頁面改成印名字之後就要把它刪掉**——留著它，這條斷言會退化成
  // 「提示裡有沒有出現過『第一位』三個字」，而那件事跟指名沒有關係。
  var names = ['planner', '規劃', '第一位'];
  var named = false;
  for (var n = 0; n < names.length; n++)
    if (text.indexOf(names[n]) >= 0) named = true;
  if (!named)
    fail.push('提示沒有指名是哪一格閒著；「最後 N 格」在規劃者坐最前面時指到的是審查者');
  if (document.querySelector('.notes .note .do[data-remove]'))
    fail.push('提示又附上移除按鈕了 —— 這次不派工不等於該刪，換一種序列它就會跑');

  // 「加一格」永遠可按。2026-08-22 定案：引擎沒有格數上限，而**畫面不准
  // 發明一個**。原本這裡斷言「三格已滿要關」，那是上限還在的時候寫的；
  // 上限拿掉之後，它斷言的正好是定案的反面。
  // 「＋ 加一格」2026-08-25 從那一列的尾端（`.addslot`）搬到標題列的按鈕
  // ——留在裡面的話它自己會捲出畫面。**這支探針要跟著改，不是刪掉**：
  // 它守的那件事（加號永遠可按）一個字都沒變。
  //
  // 找不到的時候直接 fail，不要讓 `null.hasAttribute` 丟例外——
  // 探針自己炸掉的話這一輪**什麼都沒量到**，而下游每一條斷言都會拿到
  // undefined，看起來像四個獨立的 bug。2026-08-25 真的發生過一次。
  var add = document.querySelector('[data-add]');
  if (!add)
    fail.push('找不到「加一格」那顆按鈕 —— 它可能改寫法了，這支探針要跟著改');
  d.lineup.slots = d.lineup.slots.slice(1);
  d.sequence = {label:'開發序列',
                jobs:{'0':'這一輪：動手做','1':'這一輪：複查'},
                executed_slots:2};
  render(d);
  if (!document.querySelector('[data-add]'))
    fail.push('少了一格之後「加一格」不見了 —— 那一格從這一頁加不回來');

  // ---- 他自己取的名字（五節點測試的形狀）------------------------------
  //
  // 這一段 2026-08-25 加。以前這支探針**手餵一份已經有內容的 jobs**，
  // 所以「jobs 是空的」那個狀態一次都沒量過——而那正是座位改名之後
  // 引擎真正會送出來的東西（那張表以前用出廠名字當鍵）。
  //
  // 症狀：整排虛線紅框加「不會被執行」，**而引擎照跑照付錢**。
  d.lineup.slots = [seat('node1','claude',true,'work'),
                    seat('node2','codex',true,'review'),
                    seat('node3','claude',true,'approve')];
  d.sequence = {label:'開發序列',
                jobs:{'0':'這一輪：動手做','1':'這一輪：複查','2':'這一輪：拍板'},
                executed_slots:3};
  render(d);
  var his = document.querySelectorAll('.slot');
  var greyed = [];
  for (var g = 0; g < his.length; g++)
    if (his[g].classList.contains('dead')) greyed.push(g);
  if (greyed.length)
    fail.push('他自己取的名字，第 [' + greyed + '] 格被畫成「不會被執行」'
              + ' —— 而引擎會派工給它們');
  if (document.querySelectorAll('.notes .note.warn').length)
    fail.push('他自己取的名字，畫面還說有格子閒著');

  // 反面：真的有一格閒著的時候，那句提示要在，而且**指名是哪一格**。
  d.sequence = {label:'計畫序列',
                jobs:{'1':'這一輪：出計畫','2':'這一輪：拍板'},
                executed_slots:2};
  render(d);
  var idle = document.querySelector('.notes .note.warn');
  if (!idle)
    fail.push('有一格在這次序列裡沒有工作，畫面卻一個字都沒說');
  else if (idle.textContent.indexOf('node1') < 0)
    fail.push('閒著的提示沒有指名是哪一格（用的是序數不是名字）：'
              + idle.textContent.trim());

  render(DEMO);

  // ---- 自訂身分與模式（ROADMAP P10 Stage D／E）------------------------
  //
  // 這兩組欄位從 2026-08-10 起就在推，畫面到 2026-08-11 才接。它們是
  // 「推出去沒人讀」那張待接清單上最後的八個。
  // ⚠️ 頁面設計 2026-08-27 動了這一行（老闆當天拍板「都幫我做」的連帶）。
  //
  // 原本寫死 `!== 2`，而**這條斷言自己的訊息寫著「每一格一顆」**——
  // 那個 2 是「示範資料今天有兩格」，不是這條規則要守的東西。
  // 示範資料 2026-08-27 加了第三格（一個真的分支，不然二維那段程式一行
  // 都跑不到），這條就紅了，而它紅的理由跟「身分按鈕在不在」完全無關。
  //
  // 改成跟畫出來的格數比：**釘那個行為，不是那個行為今天等於幾**。
  // （順帶：它一紅就 return，下面「清空身分要送出 0:」整段跟著沒跑——
  //   一條腐爛的斷言會把它後面的檢查一起關掉。）
  // 覺得這條做壞了直接改回去，那是你們的檔案。
  var btns = [].slice.call(document.querySelectorAll('.ops .idn'));
  var seats = document.querySelectorAll('.slot').length;
  if (btns.length !== seats) {
    fail.push('身分按鈕應該每一格一顆，' + seats + ' 格找到 ' + btns.length + ' 顆');
  } else {
    // 有自訂身分的那一格要**不展開就看得出來**。少了這個標記，日後自訂
    // prompt 造成的失敗會被誤判成程式壞了，而那種誤判很貴。
    if (btns[0].classList.contains('on'))
      fail.push('沒有自訂身分的那一格被標成自訂了');
    if (!btns[1].classList.contains('on'))
      fail.push('有自訂身分的那一格沒有標記 —— 不展開就看不出這格被改過');

    // 展開作者那一格（出廠身分有 3 種）。
    btns[0].click();
    var box = document.querySelector('.idnbox');
    if (!box) {
      fail.push('按了「身分」沒有展開輸入框');
    } else {
      var ta = box.querySelector('textarea');
      var shipped = DEMO.lineup.slots[0].shipped_identities;
      // placeholder 只放 identity 那一半。放完整開場白的話，使用者會以為
      // 後面那段安全規定也是他在編輯的。
      if (ta.placeholder !== shipped[0])
        fail.push('placeholder 不是出廠身分本身：' + ta.placeholder);
      if (/不得修改|唯讀/.test(ta.placeholder))
        fail.push('placeholder 裡出現了安全欄杆 —— 那段改不到，不能讓人以為改得到');
      // 數量要用陣列長度算。**測法刻意不是「說明裡有沒有 3」** —— 那個 3
      // 在這一格的說明裡出現兩次，凍住其中一個另一個還在，檢查照樣綠
      // （負向驗證抓到過）。改成問它會不會跟著那一格變：規劃者只跑一個
      // 步驟，它的說明裡不該出現任何「還有 N 種」。
      // 說明裡的數量要跟著那一格變。
      //
      // **問法很重要。** 第一版問「說明裡有沒有出現 3」，而 3 在那句話裡出現
      // 兩次，凍住其中一個另一個還在，檢查照樣綠。第二版改成比對數字本身，
      // 但示範資料剛好就是 3，把它凍成 3 一樣什麼都沒變。
      //
      // 所以這裡**餵幾組不同長度的出廠身分進去**：只有真的從陣列長度算出來的
      // 數字，才會三組都對。這是唯一能把「寫死」跟「剛好一樣」分開的問法。
      //
      // 座位已經是展開的，所以每一組只重畫、不要再點一次 —— 再點是收合。
      [1, 3, 4].forEach(function (n) {
        var probe = JSON.parse(JSON.stringify(DEMO));
        var list = [];
        for (var k = 0; k < n; k++) list.push('你是第 ' + k + ' 種身分。');
        probe.lineup.slots[0].shipped_identities = list;
        render(probe);
        var b = document.querySelector('.idnbox');
        if (!b) { fail.push('重畫之後展開中的身分編輯區自己關起來了'); return; }
        var text = b.querySelector('.hint').textContent;
        var more = text.match(/還有\s*(\d+)\s*種/);
        var all  = text.match(/這\s*(\d+)\s*種/);
        if (n === 1) {
          // 只跑一個步驟的座位（規劃者就是），不該被告知「還有 N 種」。
          if (more || all)
            fail.push('只有一種出廠身分的座位，說明卻講了「' +
                      (more || all)[0] + '」—— 那個數字是寫死的');
          return;
        }
        if (!more || Number(more[1]) !== n - 1)
          fail.push(n + ' 種出廠身分時，「還有 N 種」寫的是「' +
                    (more ? more[0] : '沒有這句') + '」，應該是 ' + (n - 1));
        if (!all || Number(all[1]) !== n)
          fail.push(n + ' 種出廠身分時，「這 N 種全部」寫的是「' +
                    (all ? all[0] : '沒有這句') + '」，應該是 ' + n);
      });
      render(DEMO);
      box = document.querySelector('.idnbox');
      ta = box.querySelector('textarea');
      if (!box.querySelector('.rails'))
        fail.push('沒有把「改不到的那一段」單獨列出來');
      // 清空 = 還原，走的是同一條路，所以不能有第二顆還原按鈕。
      if (box.querySelector('button'))
        fail.push('身分編輯區多了按鈕 —— 清空就是還原，按鈕會變成第二個狀態');
      ta.value = '';
      ta.dispatchEvent(new Event('change', {bubbles: true}));
    }
  }

  var sel = document.getElementById('modeSel');
  if (!sel) {
    fail.push('模式選單沒有畫出來');
  } else {
    // 目前符合某個模式時，不可以有一個「選得到、選了不做事」的空選項。
    var dead = [].slice.call(sel.options).filter(function(o){ return !o.value; });
    if (DEMO.lineup.active_preset && dead.length)
      fail.push('已經符合某個模式了，選單卻還有一個空選項 —— 選它不會有任何事發生');
    sel.value = '三格（先規劃）';
    sel.dispatchEvent(new Event('change', {bubbles: true}));
  }

  render(DEMO);
  return JSON.stringify({fail: fail});
})()
"""

# 決策紀錄的兩種卡片。這一頁的全部意義就是這兩種東西**不可以長得一樣**：
# 「人看過」是證據，「AI 自己判斷」是背景。混在一起，這份紀錄就沒有用了
# ——它會宣稱一件事被人審過，而那正是它唯一被拿出來用的場合。
#
# 交接文件把這一項列在「還沒用眼睛看過」，並說要跑一次有人審的流程才看得到。
# 那是就 sandbox 專案的日誌而言；這一頁自己的示範資料兩種卡片都有，
# 所以量得到，而且不用花錢。
#
# 用 `.ent.ai` 有沒有這個 class 來驗是不夠的——class 在、樣式沒生效，
# 兩種卡片還是長得一樣。所以比的是**算出來的樣式**。
AUDIT_CARDS_PROBE = r"""
(function(){
  var fail = [];
  var human = document.querySelector('.ent.human');
  var ai    = document.querySelector('.ent.ai');
  if (!human || !ai) return JSON.stringify({fail:['示範資料裡缺少其中一種卡片']});

  var h = getComputedStyle(human), a = getComputedStyle(ai);
  if (a.borderLeftStyle !== 'dashed')
    fail.push('「AI 自行判斷」不是虛線，實際 ' + a.borderLeftStyle);
  if (h.borderLeftStyle === 'dashed')
    fail.push('「你決定的」也是虛線 —— 兩種卡片分不出來');
  if (!(parseFloat(a.opacity) < parseFloat(h.opacity)))
    fail.push('「AI 自行判斷」沒有被壓低：opacity ' + a.opacity + ' vs ' + h.opacity);
  if (!(ai.getBoundingClientRect().height < human.getBoundingClientRect().height))
    fail.push('「AI 自行判斷」沒有比較矮 —— 它應該是壓成一行的背景資訊');

  // 身分標籤也要分得出來。卡片外框一樣、只有標籤不同的話，掃過去讀不出差別。
  var hw = getComputedStyle(human.querySelector('.who'));
  var aw = getComputedStyle(ai.querySelector('.who'));
  if (hw.backgroundImage === aw.backgroundImage &&
      hw.backgroundColor === aw.backgroundColor)
    fail.push('兩種卡片的身分標籤底色一樣');

  return JSON.stringify({fail: fail});
})()
"""

# credentials_v1 專用：這一頁的每一種失敗都長得像一個正常畫面，所以每一條
# 都是行為探針，不是數量。
#
# ① 為什麼「不准有輸入欄位」要是一條檢查而不是一句叮嚀：
#    `AICC-視覺概念/回覆給頁面設計-11.md` 最後一節到現在還寫著「API key
#    欄位要畫，這一輪就畫」，而那一節已經被 2026-08-24「會員優先」推翻。
#    下一個照它做的人會把那一格加回來——**而那一刻這條會紅**。
#    引擎那一側接不上（`credential_entry` 沒有任何函式收得下一把 key），
#    所以那會是一個收下憑證然後丟掉的框。
CREDENTIALS_PROBE = r"""
(function(){
  var fail = [];
  if (typeof render !== 'function' || typeof paint !== 'function' || typeof S === 'undefined')
    return JSON.stringify({fail:['找不到 render()／paint()／S']});
  var keep = JSON.stringify(S);
  function body(){ return document.getElementById('body').textContent; }

  // ① 憑證入口不准收任何輸入。
  var fields = document.querySelectorAll('input, textarea, select, [contenteditable]');
  if (fields.length)
    fail.push('這一頁出現了 ' + fields.length + ' 個輸入欄位 —— 憑證入口一格都不准收');

  // ② 沒推 setup 的時候不准畫結論。編一個「都正常」出來的代價，
  //    正好是這一頁存在的理由。
  var warn = console.warn; console.warn = function(){};
  S = { mode: S.mode };
  paint();
  console.warn = warn;
  if (document.getElementById('verdict'))
    fail.push('還沒收到檢查結果就畫出了結論');
  if (body().indexOf('可以開始') >= 0)
    fail.push('還沒收到檢查結果就說「可以開始了」');

  // ③ 有東西擋著要講件數，而且「沒登入」那一列要有可複製的指令與
  //    「為什麼 AICC 不代跑」。那兩塊在原生端只出現在按下按鈕之後的訊息框裡
  //    ——也就是他問「為什麼不直接幫我登入」的那一刻看不到。
  S = JSON.parse(keep);
  S.setup = {providers:[
    {key:'claude', name:'A', state:'signed_out', detail:'d',
     action_label:'去', typed:'claude.cmd setup-token', why:'因為'},
    {key:'codex', name:'B', state:'missing', detail:'d', can_pick_path:true}]};
  paint();
  var verdict = document.getElementById('verdict');
  if (!verdict) fail.push('兩件事擋著，卻沒有畫結論');
  else if (verdict.textContent.indexOf('2') < 0)
    fail.push('兩件事擋著，結論那一句沒有講件數：「' + verdict.textContent.slice(0,24) + '」');
  var out = document.querySelector('.pv.signedout');
  if (!out) fail.push('「沒登入」那一列沒有自己的樣子');
  else {
    if (!out.querySelector('.cmd')) fail.push('「沒登入」那一列少了可複製的指令行');
    if (!out.querySelector('.why')) fail.push('「沒登入」那一列少了「為什麼 AICC 不代跑」');
  }

  // ④ 「登入狀態不明」不准畫成完成。`_signed_in()` 回三態，而 `None`
  //    在畫面上跟「真的登入了」長得一樣的那一天，就是清單全綠、每一次
  //    執行都死在中間的那一天。
  S = JSON.parse(keep);
  S.setup = {providers:[
    {key:'claude', name:'A', state:'unknown', detail:'d'},
    {key:'codex',  name:'B', state:'ok',      detail:'d'}]};
  paint();
  var un = document.querySelector('.pv.unknown .dot');
  var ok = document.querySelector('.pv.ok .dot');
  if (!un || !ok) fail.push('「不明」與「可以用」沒有分成兩種列');
  else if (getComputedStyle(un).backgroundColor === getComputedStyle(ok).backgroundColor)
    fail.push('「登入狀態不明」跟「可以用」畫成同一顆點');

  // ⑤ 檢查中那顆按鈕要按不下去。按下去沒反應的按鈕會被按第二次。
  S = JSON.parse(keep);
  S.setup = {rechecking:true, providers:[{key:'claude', name:'A', state:'ok', detail:'d'}]};
  paint();
  if (!document.getElementById('scanbtn').disabled)
    fail.push('檢查進行中，那顆按鈕還按得下去');

  S = JSON.parse(keep); paint();
  return JSON.stringify({fail: fail});
})()
"""

# Keyed by the result's own label, so the shell's two halves can differ.
EXTRA_PROBES = {
    "credentials_v1.html": (
        "憑證入口：不收輸入／不編結論／不明不是完成／進行中按不下去",
        CREDENTIALS_PROBE,
    ),
    "knowledge_v1.html": ("Markdown 標籤樣式覆蓋", TYPO_PROBE),
    "settings_v1.html": ("空狀態不編造預設值／地板由 Python 決定", EMPTY_STATE_PROBE),
    "lineup_v1.html": (
        "三格＋計畫序列：誰會跑／能不能加／能不能移除；自訂身分與模式",
        LINEUP_SEQ_PROBE,
    ),
    "audit_v1.html": ("「人看過」與「AI 自行判斷」長得不一樣", AUDIT_CARDS_PROBE),
    "shell.html?part=top": (
        "執行列：樣式／局部更新／完成色／當前格／需要修正／執行中橫幅",
        SHELL_TOP_PROBE,
    ),
}


class Bridge(QObject):
    stateChanged = Signal(str)

    def __init__(self, on_ready):
        super().__init__()
        self._on_ready = on_ready
        self.actions: list[tuple[str, str]] = []

    @Slot()
    def ready(self):
        self._on_ready()

    @Slot(str, str)
    def on_action(self, kind: str, arg: str):
        self.actions.append((kind, arg))


class Page(QWebEnginePage):
    def __init__(self, profile):
        super().__init__(profile, None)
        self.console: list[str] = []    # error，會讓該頁失敗
        self.warnings: list[str] = []   # warn，只當備註（例如設定頁回報哪些 key 沒收到）

    def javaScriptConsoleMessage(self, level, message, line, source):
        # PySide6 6.11 的 JavaScriptConsoleMessageLevel 不能直接 int()，要比對列舉本身
        L = QWebEnginePage.JavaScriptConsoleMessageLevel
        if level == L.ErrorMessageLevel:
            self.console.append(f"console[{line}]: {message}")
        elif level == L.WarningMessageLevel:
            self.warnings.append(f"console[{line}]: {message}")


class Result:
    def __init__(self, name):
        self.name = name
        self.errors: list[str] = []
        self.notes: list[str] = []
        self.ok = False


# Views and profiles stay referenced until the run ends, then are torn down
# together in main(). Letting one die mid-run collects its page with it, and
# check_page has early returns that would otherwise each need their own cleanup.
_ALIVE: list[object] = []


def check_page(
    app: QApplication,
    filename: str,
    checks: dict,
    query: str = "",
    expect_shell: bool = False,
) -> Result:
    res = Result(f"{filename}?{query}" if query else filename)
    profile = QWebEngineProfile()
    page = Page(profile)
    # A view, shown, at desktop width - see the module docstring. The profile is
    # returned alongside the result because nothing else holds a reference to it
    # and a collected profile takes its page down with it.
    view = QWebEngineView()
    view.setPage(page)
    view.resize(VIEWPORT_W, VIEWPORT_H)
    view.show()
    _ALIVE.extend((profile, view))

    script = QWebEngineScript()
    script.setName("collector")
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    script.setSourceCode(COLLECTOR)
    page.scripts().insert(script)

    state = {"ready": False, "pushed": False, "probe": None}

    def on_ready():
        state["ready"] = True
        page.runJavaScript("window.__ready = true;")
        # 把頁面自己的 DEMO 抓出來，再從 Python 推回去 —— 模擬真實接線
        # 設定頁沒有 DEMO 常數，它用 DEMO_S；沒有 bridge 時才會自己套上，
        # 這裡有 bridge，所以要主動推過去，模擬 Python 推完整設定的情境。
        page.runJavaScript(
            "JSON.stringify("
            "  typeof DEMO   !== 'undefined' ? DEMO   :"
            "  typeof DEMO_S !== 'undefined' ? DEMO_S :"
            "  typeof S      !== 'undefined' ? S      : {})",
            push_back,
        )

    def push_back(payload):
        state["pushed"] = True
        bridge.stateChanged.emit(payload or "{}")

    bridge = Bridge(on_ready)
    channel = QWebChannel(page)
    channel.registerObject("bridge", bridge)
    page.setWebChannel(channel)

    loaded = {"done": False, "ok": None}

    def on_load(ok):
        loaded["done"] = True
        loaded["ok"] = ok

    page.loadFinished.connect(on_load)

    url = QUrl.fromLocalFile(str((HERE / filename).resolve()))
    if query:
        url.setQuery(query)
    page.load(url)

    # 事件迴圈：等載入 → 等 ready → 等推完 → 探測
    import time
    deadline = time.time() + 15
    while time.time() < deadline and not (loaded["done"] and state["pushed"]):
        app.processEvents()
        time.sleep(0.01)

    if not loaded["done"]:
        res.errors.append("載入逾時（15 秒）")
        return res
    if not loaded["ok"]:
        res.errors.append("loadFinished 回報失敗")
    if not state["ready"]:
        res.errors.append("bridge.ready() 沒有被呼叫 —— QWebChannel 沒接上")

    # 給 render() 一點時間跑完
    end = time.time() + 1.2
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

    done = {"v": None}

    def got(v):
        done["v"] = v

    page.runJavaScript(PROBE % _js_obj(checks), got)
    end = time.time() + 5
    while time.time() < end and done["v"] is None:
        app.processEvents()
        time.sleep(0.01)

    if done["v"] is None:
        res.errors.append("探測腳本沒有回傳")
        return res

    import json as _json
    data = _json.loads(done["v"])

    for e in data.get("errors", []):
        res.errors.append(f"JS {e}")
    for c in page.console:
        res.errors.append(f"JS {c}")
    for w in page.warnings:
        res.notes.append(f"warn {w}")

    # Before anything else: were we even measuring the right layout? A 0-wide
    # viewport makes every max-width media query match, so each check below
    # would be describing the phone layout while claiming to describe this one.
    vw, vh = data.get("viewportW"), data.get("viewportH")
    if (vw, vh) != (VIEWPORT_W, VIEWPORT_H):
        res.errors.append(
            f"viewport {vw}x{vh}，預期 {VIEWPORT_W}x{VIEWPORT_H} —— "
            "view 沒顯示的話所有 max-width 媒體查詢都會命中，這輪驗的是手機版"
        )
    else:
        res.notes.append(f"viewport {vw}x{vh}")

    if not data["hasQWebChannel"]:
        res.errors.append("QWebChannel 未定義 —— qwebchannel.js 沒載到")
    if not data["hasTransport"]:
        res.errors.append("qt.webChannelTransport 不存在")
    # The shell (sidebar + execution bar) is its own always-visible view, so a
    # content page must not draw one. Two sidebars is the visible symptom; the
    # invisible one is that the page would be laying out against a width that
    # already excludes the real sidebar. Asserted in both directions - a shell
    # half that draws nothing is the same failure seen from the other side.
    if expect_shell and not data.get("drawsShell"):
        res.errors.append("外框沒有畫出來 —— shell.html 這一半是空的")
    if not expect_shell and data.get("drawsShell"):
        res.errors.append("這頁自己畫了外框 —— 外框由 shell.html 提供，內容頁不該有")

    for sel, expect in checks.items():
        got_n = data["counts"].get(sel, 0)
        if got_n < expect:
            res.errors.append(f"{sel} 只有 {got_n} 個，預期至少 {expect}")

    res.notes.append(f"body 子節點 {data['bodyChildren']}")

    # The execution bar cannot resize its own Qt widget, so it measures itself
    # after each render and reports back; ShellPanel turns that into
    # setFixedHeight. If the report stops coming the bar keeps its 64px
    # starting height and the decision banner is drawn below the fold of its
    # own view - visible in a browser, clipped in the app. This is checked with
    # the banner present, so the reported height has to exceed the bare bar.
    if query == "part=top":
        heights = [arg for kind, arg in bridge.actions if kind == "shell_height"]
        if not heights:
            res.errors.append(
                "沒有回報 shell_height —— Qt 無從得知橫幅出現後要多高，橫幅會被裁掉"
            )
        elif int(heights[-1]) <= 64:
            res.errors.append(
                f"回報高度 {heights[-1]}px，不比沒有橫幅的執行列高 —— 橫幅沒有被計入"
            )
        else:
            res.notes.append(f"橫幅出現時回報高度 {heights[-1]}px")

    # 額外檢查。Keyed on res.name, not filename: the shell is one file loaded
    # twice and only the top half has an execution bar to probe.
    if res.name in EXTRA_PROBES:
        label, js = EXTRA_PROBES[res.name]
        extra = {"v": None}
        page.runJavaScript(js, lambda v: extra.__setitem__("v", v))
        end = time.time() + 5
        while time.time() < end and extra["v"] is None:
            app.processEvents()
            time.sleep(0.01)
        if extra["v"] is None:
            res.errors.append(f"{label}：探測沒有回傳")
        else:
            # A probe that throws comes back as `null`, and json.loads(None)
            # used to blow up the whole run with a JSONDecodeError several
            # pages later - a traceback instead of a report, from a tool whose
            # entire output *is* the report. A broken probe is a failed page.
            try:
                payload = _json.loads(extra["v"])
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                res.errors.append(
                    f"{label}：探測腳本自己出錯了（回傳 {extra['v']!r}）"
                    " —— 這一輪什麼都沒量到，不是通過"
                )
            else:
                fails = payload.get("fail", [])
                for f in fails:
                    res.errors.append(f"{label}：{f}")
                if not fails:
                    res.notes.append(f"{label} 全部通過")

    # The activity strip's click, checked on this side of the bridge. The probe
    # can only prove the element was clickable; whether the action left the page
    # with the right argument is a fact about the wire, and this is the only
    # place that sees it.
    #
    # 「按了有反應」 and 「按了送出了正確的東西」 are different claims, and this
    # project has already shipped three buttons that satisfied the first.
    if query == "part=top":
        sent = [arg for kind, arg in bridge.actions if kind == "set:project"]
        if not sent:
            res.errors.append(
                "點橫幅上的專案沒有送出 set:project —— 那一項看得到、點不動"
            )
        elif sent[-1] != "csv":
            res.errors.append(
                f"點第一項送出的是 {sent[-1]!r}，預期那一項自己的 id 'csv'"
                " —— 送錯 id 會切到另一個專案，而畫面看起來完全正常"
            )
        else:
            res.notes.append("點橫幅送出 set:project csv")

    # 陣容頁的兩個新控制項，同樣在橋的這一側驗：清空身分要送出空字串
    # （空字串就是「回到出廠」，走的是同一條路），選模式要送出那個名字。
    if res.name == "lineup_v1.html":
        sent = dict(bridge.actions)
        if sent.get("set_slot_identity") != "0:":
            res.errors.append(
                f"清空身分送出的是 {sent.get('set_slot_identity')!r}，預期 '0:'"
                " —— 清空就是還原，送不出去的話那個框只能寫不能清"
            )
        if sent.get("apply_preset") != "三格（先規劃）":
            res.errors.append(
                f"選模式送出的是 {sent.get('apply_preset')!r}"
                " —— 選單看得到、換不動"
            )
        if not res.errors:
            res.notes.append("身分清空與套用模式都有送到 Python")

    res.ok = not res.errors
    page.setWebChannel(None)
    return res


def _teardown(app: QApplication) -> None:
    """Close every view before the QApplication goes away.

    Chromium does not survive being torn down at interpreter shutdown - that is
    the same exit-139 that keeps these checks out of pytest.
    """
    for obj in _ALIVE:
        if isinstance(obj, QWebEngineView):
            obj.hide()
            obj.setPage(None)
            obj.deleteLater()
    app.processEvents()
    _ALIVE.clear()


INLINE_HANDLER = re.compile(r'\son[a-z]+\s*=\s*"[^"]*\$\{')

# Samples the scanner is tested against before it is trusted, every run.
#
# This exists because of two failures in one day, both of the same shape: a
# check that kept passing after it had stopped checking anything. One was a
# regex that could not match any real case, so it reported a clean sweep
# forever. The other was a report block that got indented into a loop that
# never ran, so the per-page output vanished while the exit code stayed 0.
#
# Neither had a symptom. A green result and a broken checker look identical
# from the outside, which is what makes this class of failure worth a negative
# control rather than a note in a document.
SCANNER_MUST_CATCH = (
    """<div onclick="pick('${id}')">""",
    """<span onclick="set('${key}','${v}')">""",
    """<i onmouseover="show(${n})">""",
    """<b onclick="go( ${x} )">""",
)
SCANNER_MUST_IGNORE = (
    """<div onclick="pick('claude','c1')">""",     # no interpolation
    """<div data-act="save" data-id="${id}">""",   # data attribute, not a handler
    """const s = `onclick="${x}"`;""",             # inside a JS string, no leading space+on
)


def self_test_scanner() -> list[str]:
    """Check the checker. Returns the ways it is broken, empty if sound."""
    problems = []
    for sample in SCANNER_MUST_CATCH:
        if not INLINE_HANDLER.search(sample):
            problems.append(f"應該抓到卻沒抓到：{sample}")
    for sample in SCANNER_MUST_IGNORE:
        if INLINE_HANDLER.search(sample):
            problems.append(f"不該抓卻抓到了：{sample}")
    return problems


def scan_inline_handlers() -> list[str]:
    """Pages must not interpolate anything into an inline event handler.

    HTML escaping does not defend this position, and that is not a matter of
    escaping harder - it is the parse order. The HTML parser decodes `&#x27;`
    back to `'` before the JS parser ever sees the attribute, so

        onclick="pick('claude','x&#x27;);alert(1);//')"

    runs as pick('claude','x');alert(1);//'). Measured in QtWebEngine with a
    value that had been through html.escape(quote=True): the payload executed.

    The only defence is not to build handlers by interpolation - use data-*
    attributes and one delegated listener. This scan is deliberately blind to
    whether the interpolated value "looks trusted": today it is a hard-coded
    chip label, tomorrow someone wires it to imported data, and the person
    making that change will not remember this rule.
    """
    problems: list[str] = []
    for path in sorted(HERE.glob("*.html")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if INLINE_HANDLER.search(line):
                problems.append(f"{path.name}:{number}  {line.strip()[:90]}")
    return problems


def report_mounting(results: list[Result]) -> list[str]:
    """Say where each page is opened from, and name the ones nothing opens.

    The three failures this reports are all invisible to the per-page checks
    above, because all three are about the relationship between a page file and
    the application rather than about the file:

    * a page in the build that no panel renders - it passes every check and no
      user can ever see it;
    * a page a panel renders that is not in the build - it fails at the moment
      someone clicks the sidebar entry, which can be months after the deletion;
    * a mounted page missing from CHECKS - the run says nothing about it while
      the summary counts pages, so adding a page and forgetting to verify it
      reads as "one more page passing".
    """
    from app.ui.shell_panel import PAGES as SIDEBAR
    from tools.page_wiring import missing_pages, mounted_pages, orphan_pages

    labels = dict(SIDEBAR)
    mounted = mounted_pages()
    missing = set(missing_pages(HERE))
    # Names carry the query string ("shell.html?part=top"); the file is what is
    # mounted, and one of its two loads having run is enough.
    checked = {r.name.split("?", 1)[0] for r in results}

    print("\n" + "-" * 52)
    print("掛載狀態（讀自 main_window._add_page 與 shell_panel.PAGES）")

    problems: list[str] = []
    for page, where in sorted(mounted.items()):
        via = f"側邊欄 {where}（{labels[where]}）" if where in labels else where
        if page in missing:
            print(f"        X {page:<26}{via} —— 但 app/ui/web 裡沒有這個檔案")
            problems.append(f"{page} 有面板指名，檔案卻不在 build 裡")
        elif page not in checked:
            print(f"        X {page:<26}{via} —— CHECKS 沒有這頁，這輪沒驗到它")
            problems.append(f"{page} 有掛載卻不在 CHECKS 裡，這輪沒有被檢查")
        else:
            print(f"        · {page:<26}{via}")

    for page in orphan_pages(HERE):
        print(f"        X {page:<26}沒有面板會開啟它 —— 驗過也沒有人看得到")
        problems.append(f"{page} 沒有任何面板會開啟它")

    if not problems:
        print(f"        每一頁都有面板會開啟，也都在這輪驗過（{len(mounted)} 頁）")
    return problems


def _js_obj(d: dict) -> str:
    import json as _json
    return _json.dumps(d)


def pages_without_checks() -> list[str]:
    """Pages that get built but have no entry in `CHECKS`.

    `CHECKS` is hard-coded, not a glob - deliberately, because a glob would put
    a page nobody is ready to ship into the verified set automatically. The
    cost of that choice is a list somebody has to remember, and this is what
    replaces the remembering: **hard-coded but noisy beats automatic but
    silent** (the design session's phrasing, 回覆給系統開發-16.md).

    Without it, adding a page and forgetting `CHECKS` produces a green run that
    simply never looked at the new page.
    """
    from app.ui.web import build_pages

    built = [
        name
        for name in build_pages.PAGES
        if name.endswith(".html") and name != "shell.html"
    ]
    return [name for name in built if name not in CHECKS]


def main() -> int:
    broken = self_test_scanner()
    if broken:
        print("X inline handler 掃描器本身壞了，這次的結果不可信：")
        for item in broken:
            print(f"    {item}")
        return 2

    missing = pages_without_checks()
    if missing:
        print("X 有頁面會被 build 進來，但 CHECKS 裡沒有對應的檢查：")
        for name in missing:
            print(f"    {name}")
        print("    這一輪的綠燈不代表那些頁面被看過。請補上 CHECKS 再跑一次。")
        return 2

    app = QApplication(sys.argv)

    # (filename, query, checks, expect_shell)
    plan = [(name, "", checks, False) for name, checks in CHECKS.items()]
    plan += [("shell.html", query, checks, True) for query, checks in SHELL_CHECKS]

    results = []
    for filename, query, checks, expect_shell in plan:
        label = f"{filename}?{query}" if query else filename
        if not (HERE / filename).exists():
            print(f"SKIP  {label}（找不到檔案）")
            continue
        r = check_page(app, filename, checks, query, expect_shell)
        results.append(r)
        mark = "PASS" if r.ok else "FAIL"
        print(f"{mark}  {label}")
        for n in r.notes:
            print(f"        · {n}")
        for e in r.errors:
            print(f"        ✗ {e}")

    _teardown(app)

    inline = scan_inline_handlers()
    print("\n" + "-" * 52)
    if inline:
        print(f"行內事件處理器帶插值（{len(inline)} 處）—— HTML 跳脫救不了這個位置：")
        for item in inline:
            print(f"        X {item}")
    else:
        print("行內事件處理器：沒有任何一處帶插值")

    mounting = report_mounting(results)

    # Second negative control: assert how many pages actually reported, not
    # merely that none failed. When the report block was once indented into a
    # loop that never ran, every page silently stopped being checked and the
    # exit code stayed 0 - "no failures" is also what checking nothing looks
    # like.
    expected = len([entry for entry in plan if (HERE / entry[0]).exists()])
    if len(results) != expected:
        print(f"X 只驗了 {len(results)} 頁，預期 {expected} 頁 —— 有頁面被跳過了")
        return 2

    bad = [r for r in results if not r.ok]
    print("\n" + "=" * 52)
    if bad or inline or mounting:
        if bad:
            print(f"{len(bad)} / {len(results)} 頁有問題：" + "、".join(r.name for r in bad))
        if inline:
            print(f"另有 {len(inline)} 處行內事件處理器帶插值（見上方）")
        # Listed after the page results and counted separately: a page can be
        # perfect and unreachable, and rolling that into "N 頁有問題" would put
        # it back inside the number that was already covering for it.
        for problem in mounting:
            print(f"掛載：{problem}")
    else:
        print(
            f"全部 {len(results)} 頁通過（載入、QWebChannel、render、沒有自畫外框），"
            "且每一頁都有面板會開啟"
        )
    return 1 if (bad or inline or mounting) else 0


if __name__ == "__main__":
    sys.exit(main())
