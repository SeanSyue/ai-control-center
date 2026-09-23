/* ==========================================================================
   AICC Shell —— 六頁共用的側邊欄、執行控制列、決策橫幅
   --------------------------------------------------------------------------
   為什麼是一份 JS 而不是六份 HTML：
   切換分頁時整個 QWebEngineView 會被換掉，任何一頁的側邊欄差一像素，
   切換就會抖。「像素一致」不該靠六份複製品保持紀律，該靠只有一份。

   每一頁只要做兩件事：
     1. <script src="shell.js"></script>
     2. 在自己的 render() 裡呼叫 Shell.render(d)

   Python 端在 state 裡多推一個 shell 物件（見下方 SCHEMA），
   動作會經由同一個 bridge.on_action(kind, arg) 送回去。
   ========================================================================== */

/* SCHEMA ────────────────────────────────────────────────────────────────────
   d.shell = {
     active_page : "documents",              // 由 Python 決定，不要讓頁面自己記
     pages       : [{key,label,badge}],       // 可省略，省略就用 DEFAULT_PAGES
     project     : { name, path, sensitivity },
     projects    : [{id, name}],
     lineup      : ["claude","codex"],        // 唯讀摘要，點了送 navigate:lineup
     run         : { state:"idle"|"waiting"|"running"|"gated"|"error"|"partial"|"done",
                     label:"執行中", elapsed:"04:12",
                     active_slot:1,          // 正在跑第幾格（0 起算）→ 陣容摘要那一格亮起來
                     stage:"Codex 審查中" }, // 階段細節。不要塞進 label，
                                             // label 是封閉集合、而且綁著顏色
     gate        : { project, question, waiting } | null,
     drive       : { label:"雲端已同步 · 3 分鐘前" } | null,
     usage       : { state:"developing"|"at_least"|"exact",
                     tokens, input, output,
                     complete: true|false,      // false → 當成下限處理，跟 at_least 同一個講法
                     spike: { factor:3, window:5 } | null },  // 同一格，只是變金色
     activity    : [{id,name,state,stage,focused}],  // 有事的專案，空陣列＝整條不畫
     blocked     : { "<動作名>": "原因" }   // 現在按了不會有事的動作。
                                            // 沒列到＝可以按。key 就是下面那些
                                            // 動作名，所以頁面不用第二張對照表。
   }

   送回 Python 的動作：
     navigate:<page>        側邊欄點擊
     set:project            專案 id
     set:sensitivity        personal | client | sensitive_kyc
     run / stop             開始執行 / 停止
     open_results           查看結果檔
     new_project / remove_project / copy_path
     export_workspace / import_workspace
     line_settings / cloud_settings / sync_now / link_cloud_project
     gate_answer / gate_later
   ───────────────────────────────────────────────────────────────────────── */

const Shell = (() => {
  /* 瀏覽器單獨預覽時的 fallback。實際清單由 Python 推（只有它知道哪些面板掛上了）。
     只有需要分隔的項目帶 group —— 八個項目都塞一個空字串在每個呼叫點都是雜訊。

     **這是同一份清單的第二份副本，所以它是被比對的、不是被信任的**
     （`tests/test_sidebar_fallback_matches_python.py`）。沒有人會發現一份
     fallback 過期：它只在沒有後端的時候出現，而那正好是設計端唯一看得到的
     版面。2026-08-11 校對時它已經漂了兩項——列著 2026-08-09 就刪掉的
     「對話庫」，而且少了「即時紀錄」。 */
  const DEFAULT_PAGES = [
    { key:'lineup',        label:'陣容' },
    { key:'dashboard',     label:'儀表板' },
    { key:'session',       label:'執行中對話' },
    { key:'knowledge',     label:'共享記憶' },
    { key:'documents',     label:'文件' },
    { key:'roadmap',       label:'Roadmap' },
    { key:'settings',      label:'設定' },
    { key:'live',          label:'即時紀錄' },
    { key:'audit',         label:'決策紀錄', group:'proof' },
  ];

  const RUN_LABEL = {
    idle: '尚未執行', waiting: '等待開始', running: '執行中',
    gated: '等你決策', error: '執行失敗', done: '完成',
    /* 「跑完了但有狀況」不是完成 —— 給它自己的狀態與顏色。
       否則薄荷色的「完成」旁邊寫著「Codex 未完成審查」，畫面自相矛盾。 */
    partial: '部分完成',
  };

  /* 執行列的唯讀陣容摘要 —— 按下「開始執行」前最後一眼，
     而「這次由誰跑」正是那一刻最該確認的事。 */
  const PROV_SHORT = { claude: 'Claude', codex: 'Codex' };

  /* 外框是它自己的 view，不住在任何一頁裡。
     Qt widget 是矩形，而側邊欄（左直條）與執行列（上橫條）組不成一個矩形，
     所以是兩個 view 載同一個 shell.html，用 ?part= 決定畫哪一半。 */
  const PART = new URLSearchParams(location.search).get('part') || 'side';

  let mounted = false;
  let onAction = () => {};

  function mount() {
    if (mounted) return;
    const isSide = PART === 'side';
    const el = document.createElement(isSide ? 'aside' : 'div');
    el.className = isSide ? 'shell-side' : 'shell-top';
    el.id = isSide ? 'shellSide' : 'shellTop';
    document.body.appendChild(el);
    mounted = true;
  }

  function sideHTML(d) {
    const s = d.shell || {};
    const pages = s.pages || DEFAULT_PAGES;
    const act = s.active_page || '';
    const items = pages.map((p, i) => {
      const prev = pages[i - 1];
      /* 有 group 而前一項沒有（或不同）→ 上面畫一條線。
         前八項是「做事」，決策紀錄是「證明做過的事」——不同類別。
         分隔線讓它可預期地存在，又不跟每天在用的項目搶注意力。 */
      const start = p.group && (!prev || prev.group !== p.group) ? ' group-start' : '';
      const badge = p.badge ? `<span class="badge">${p.badge}</span>` : '';
      return `<div class="shell-nv${p.key === act ? ' act' : ''}${start}" data-nav="${p.key}">
                <span class="ic"></span><span class="t">${p.label}</span>${badge}</div>`;
    }).join('');
    return `<div class="brand"><span class="orb"></span>
              <div><b>AICC</b><small>CONTROL CENTER</small></div></div>
            <nav>${items}</nav>
            <div class="hem fabric"></div>`;
  }

  function execHTML(d) {
    const s = d.shell || {};
    const p = s.project || {};
    const run = s.run || { state: 'idle' };
    const state = run.state || 'idle';
    const busy = state === 'running' || state === 'waiting';

    const projects = (s.projects || []).map(x =>
      `<option value="${x.id}"${x.name === p.name ? ' selected' : ''}>${x.name}</option>`).join('');

    const sens = ['personal', 'client', 'sensitive_kyc'];
    const sensLabel = { personal: '個人', client: '客戶', sensitive_kyc: 'KYC／高敏感' };
    const sensOpts = sens.map(v =>
      `<option value="${v}"${p.sensitivity === v ? ' selected' : ''}>${sensLabel[v]}</option>`).join('');

    /* ── 那個值是怎麼來的 ────────────────────────────────────────────
       Python 推 `sensitivity_source`：chosen（他自己選的）／default（還沒
       選過）／unreadable（**設定檔讀不到，用最嚴的那一級頂著**）。

       這三種以前在畫面上長得一模一樣，而那不是體驗問題：一個設定檔壞掉
       的專案看起來跟他親手設成 KYC 的一樣，他最合理的反應是「這個又不是
       KYC，改低一點」——改下去那個專案的逐字執行紀錄就開始往外同步。

       `unreadable` 給一個**看得見的**記號（不是只有 title），因為它要擋的
       正是「隨手改低」那個動作；`default` 只在 title 裡講，它不危險。 */
    const SENS_WHY = {
      unreadable: { mark: '⚠', cls: ' sens-unreadable',
        why: '這個專案的設定檔讀不到，所以先用最嚴的那一級頂著。改低之前先確認它到底是什麼。' },
      /* `default` 以前跟 `chosen` 一樣什麼記號都沒有——而那是**同一種外流
         的另一端**：他開一個客戶專案，畫面顯示「個人」（最寬鬆的那一級），
         他以為那是設好的，**而那個值是 AICC 填的，不是他給的**。
         `unreadable` 那一端還要他動手改低；這一端**連手都不用動**。

         用灰色虛線不用警告色：對一個真的是個人的專案，`personal` 完全正確，
         給它金色或玫瑰是噪音。它要說的只有一句「**這個值不是你給的**」，
         而這個專案已經有那個語彙——**虛線＝推測出來的，不是讀到的**
         （偵測卡那張「猜的值用虛線標籤」、`.bdg.quiet` 的既有註解）。 */
      default:    { mark: '·', cls: ' sens-default',
        why: '這是出廠預設，你還沒選過。它是最寬鬆的一級——如果這個專案碰得到客戶資料，先改它。' },
      chosen:     { mark: '',  cls: '', why: '你自己選的。' },
    };
    const why = SENS_WHY[p.sensitivity_source] || SENS_WHY.chosen;
    const sensTitle = `專案敏感度：${p.sensitivity_label || ''}　${why.why}`;
    const sensWhy = why.mark
      ? `<span class="sens-why${why.cls}" title="${why.why}">${why.mark}</span>` : '';

    return `<div class="shell-exec">
      <div class="proj">
        <select data-shell-set="project" title="切換目前專案">${projects}</select>
        <span class="path" title="${p.path || ''}">${p.path || ''}</span>
      </div>
      <select data-shell-set="sensitivity" title="${sensTitle}">${sensOpts}</select>${sensWhy}

      ${(s.lineup || []).length
        ? `<span class="lineup" data-shell-act="navigate:lineup"
                 title="這次由誰跑 —— 點一下去編組">${
             s.lineup.map((v, i) =>
               `<b class="${run.active_slot === i ? 'on' : ''}">${PROV_SHORT[v] || v}</b>`
             ).join('<i>→</i>')}</span>`
        : ''}

      <span class="spacer"></span>

      <span class="status ${state}"><span class="k"></span>
        ${run.label || RUN_LABEL[state] || state}
        ${run.elapsed ? `<span class="t">${run.elapsed}</span>` : ''}</span>
      ${runInfoHTML(s)}

      ${s.drive ? `<span class="drive">${s.drive.label}</span>` : ''}

      ${busy
        ? `<button class="btn sm q" data-shell-act="stop">停止</button>`
        : `<button class="btn sm" data-shell-act="run">開始執行</button>`}
      <button class="btn sm q" data-shell-act="open_results">查看結果</button>

      <div class="menu" id="shellMore">
        <button class="btn q sm" data-menu="shellMore" title="專案、Workspace、通知與同步">更多</button>
        <div class="items right">
          <div class="lbl">專案</div>
          <button data-shell-act="new_project">新增專案</button>
          <button data-shell-act="copy_path">複製路徑</button>
          <button data-shell-act="remove_project">從清單移除<small>不會刪掉資料夾</small></button>
          <div class="sep"></div>
          <div class="lbl">WORKSPACE</div>
          <button data-shell-act="export_workspace">匯出 Workspace<small>打包前會掃描金鑰</small></button>
          <button data-shell-act="import_workspace">匯入 Workspace</button>
          <div class="sep"></div>
          <div class="lbl">通知與同步</div>
          <button data-shell-act="line_settings">LINE 設定</button>
          <button data-shell-act="cloud_settings">雲端設定</button>
          <button data-shell-act="sync_now">立即同步</button>
          <button data-shell-act="link_cloud_project">連結雲端專案</button>
          ${s.gate ? `
          <div class="sep"></div>
          <div class="lbl">決策</div>
          <button data-shell-act="gate_dismiss">結束這次等待<small>不回答，把專案放回可執行</small></button>` : ''}
        </div>
      </div>
    </div>`;
  }

  /* ── 執行資訊格：階段細節與用量共用同一個位置 ──────────────────
     執行列已經滿了（65px、13 個動作、一個陣容摘要），塞不下第 15 個元素。
     但這兩件事在時間上互斥：跑的時候只有階段細節，跑完才有用量。
     所以不是擠出一格，是同一格換內容。

     時態也跟著換：
       執行中／等待中          → 階段細節
       跑完（done/partial/error）→「這次執行：…」
       閒置                    →「上次執行：…」← 按下開始之前看到的是上一次的數字，
                                                 寫「這次」會是錯的
     ────────────────────────────────────────────────────────── */
  const NUM = n => (n || 0).toLocaleString('en-US');

  function runInfoHTML(s) {
    const run = s.run || {}, u = s.usage;
    const state = run.state || 'idle';
    const live = state === 'running' || state === 'waiting' || state === 'gated';

    const stage = run.stage
      ? `<span class="stage" title="${run.stage}">${run.stage}</span>` : '';

    /* 執行中只有階段細節（那時候還沒有用量可以算）。
       但終結狀態不是互斥的：「Codex 未完成審查」與「至少 27,000 tokens」
       是同一次執行的兩個事實，而且那正是使用者最想同時看到兩者的時刻。
       所以並列，原因在前（不截斷）、用量在後（不夠寬就截斷）。 */
    if (live || !u) return stage;

    const when = state === 'idle' ? '上次執行' : '這次執行';
    const join = html => stage ? stage + `<span class="sep">·</span>` + html : html;

    const sp0 = u.spike;
    /* 絕對數字要等語意驗證，倍數不用 —— 同一個未知的換算同時作用在
       「這次」與「前幾次」上，相除的時候就消掉了。
       所以「還不知道用了多少」和「這次比平常高很多」可以同時是真話。
       但倍數必須帶主詞：沒有「這次」兩個字，3 倍會變成沒有指涉對象的數字。 */
    if (u.state === 'developing')
      return join(`<span class="usage dev${sp0 ? ' spike' : ''}"
        title="絕對數字還在驗證；倍數不受影響，因為同一個未知換算在相除時會消掉">用量統計開發中${
        sp0 ? ` · 這次比最近 ${sp0.window} 次高 ${sp0.factor} 倍` : ''}</span>`);

    /* 「是不是下限」由兩件事一起決定，頁面自己合併，你們不用先算：
         u.state !== 'exact'  → 累計語意還沒驗完
         u.complete === false → 有呼叫沒回報，數字偏低
       兩者的誤差都只有一個方向（真值只會更大），所以講法一樣。 */
    const bound = u.state !== 'exact' || u.complete === false;
    const sp = u.spike;

    return join(`<span class="usage${sp ? ' spike' : ''}"
                  title="${bound ? '真值只會更大，不會更小' : ''}">${when}：<b>${
        bound ? '至少 ' : ''}${NUM(u.tokens)}</b> tokens${
        sp ? ` · 比最近 ${sp.window} 次高 ${sp.factor} 倍` : ''}</span>`);
  }

  /* A-2：這一條永遠畫在外框上，不可收合、不可摺疊。
     它不屬於儀表板 —— 使用者在任何一頁都必須看得到有人在等他回答。 */
  function gateHTML(d) {
    const g = (d.shell || {}).gate;
    if (!g) return '';
    return `<div class="shell-gate">
      <div class="txt">${g.project} · 需要你的判斷：${g.question}
        <small>已等待 ${g.waiting} · 其餘專案照常執行</small></div>
      <button class="y" data-shell-act="gate_answer">查看並回答</button>
      <button class="n" data-shell-act="gate_later">稍後</button>
    </div>`;
  }

  /* ── 執行中橫幅（ROADMAP P4）──────────────────────────────────
     使用者被問到「你想同時看兩個專案的時候到底想看什麼」，答案是
     **「有沒有出事」**。那句話換掉了整個功能：即時輸出是「現在這一秒」，
     會滾走，對一個沒在盯著的人沒有價值；狀態是「從你上次看到現在」，
     它在那裡等。把長任務丟著去睡覺的人要的是後者。

     照 Python 給的順序畫，不要重排：順序依據是**漏看的代價**
     （waiting → error → running），不是時間也不是名字。

     空陣列整條不畫。用一行螢幕每天報告「目前沒事」，成本比它的價值高。

     刻意沒有「跑到第幾格」：那個索引要對到那個專案自己的陣容，而外框手上
     只有聚焦專案的陣容——對其他每一格都會是錯的索引，**而亮錯格比不亮更糟**。
     ──────────────────────────────────────────────────────────── */
  function activityHTML(d) {
    const list = (d.shell || {}).activity || [];
    if (!list.length) return '';
    return `<div class="shell-act">${list.map(a =>
      `<span class="it ${a.state || ''}${a.focused ? ' here' : ''}"
             data-shell-go="${a.id}" title="${a.name} · ${a.stage || ''}">
         <span class="k"></span><b class="n">${a.name}</b>
         <span class="s">${a.stage || ''}</span></span>`).join('')}</div>`;
  }

  /* ── 現在按了不會有任何事發生的動作 ─────────────────────────────
     Python 推 `shell.blocked = {動作名: 原因}`，沒列到的就是可以按。

     以前這些條件只存在於每個 handler 自己的開頭：條件不符就靜靜 `return`。
     選單項目是亮的、按下去畫面完全沒變，而**使用者分不出那跟壞掉的差別**。
     最明顯的是同步中的「開始執行」——執行列的 busy 只看 workflow 不看 sync，
     所以那顆按鈕還寫著「開始執行」。

     做成**掃過所有 `[data-shell-act]` 一次**，不是在每顆按鈕上寫條件：
     下一顆按鈕不用記得加，它自動就會參與。停用樣式 aicc.css 早就有
     （`.btn:disabled`、`.menu .items button:disabled`），所以這裡不動 CSS。 */
  let BLOCKED = {};

  function applyBlocked(root, blocked) {
    BLOCKED = blocked || {};
    root.querySelectorAll('[data-shell-act]').forEach(el => {
      const why = BLOCKED[el.dataset.shellAct];
      if (!why) return;
      el.disabled = true;
      /* 原因寫進 title。一顆停用而不說為什麼的按鈕，是另一種比較小的謊——
         使用者會以為功能壞了，而不是知道「等同步跑完就可以」。 */
      el.title = why;
    });
  }

  let lastH = 0, lastKey = null;

  /* 執行中每秒會推一次 state（計時器、已等待秒數）。
     整條 innerHTML 換掉的話，使用者打開的 ⋯ 選單會在一秒內自己關起來，
     展開中的 select 清單、文字選取也一起被打斷。
     所以只有「形狀」變了才重建；秒數這種只改那一個文字節點。 */
  function shapeKey(d) {
    const s = d.shell || {}, p = s.project || {}, r = s.run || {}, g = s.gate || null;
    return JSON.stringify([
      /* `sensitivity_source` 與 `sensitivity_label` 也要進來。
         漏掉 source 的下場跟下面那段講的一樣：使用者把敏感度從「還沒選過」
         改成他自己選的，那個記號**不會消失**——直到剛好有別的東西改變形狀。
         而那個記號的意思正好是「這個值不是你給的」，所以它留著就是在說謊。
         label 同理：來源換一份（引擎那邊要收成唯一一份）時字不會跟著換。 */
      p.name, p.path, p.sensitivity, p.sensitivity_source, p.sensitivity_label,
      (s.lineup || []).join('>'), r.active_slot, !!r.stage,
      s.usage ? [s.usage.state, s.usage.tokens, s.usage.complete,
                 s.usage.spike ? [s.usage.spike.factor, s.usage.spike.window] : null] : null,
      (s.projects || []).map(x => x.id + ' ' + x.name),
      r.state, r.label, !!r.elapsed,
      g ? [g.project, g.question] : null,
      (s.drive || {}).label || null,
      /* 橫幅的每一項都要進形狀鍵。`stage` 也算 —— 它不像秒數只是數字在跳，
         它一變就代表那個專案換了狀態，而那是這條橫幅存在的唯一理由。
         漏掉的話，一個專案從「執行中」變成「需要你決策」，橫幅會維持原樣
         直到剛好有別的東西改變形狀為止。 */
      (s.activity || []).map(a => [a.id, a.state, a.stage, a.focused].join('|')),
      /* 哪些動作被擋也是形狀。漏掉的話，同步一結束「立即同步」會一直
         停用到剛好有別的東西改變形狀為止——而那可能是好幾分鐘後。 */
      Object.keys(s.blocked || {}).sort().join(','),
    ]);
  }

  function render(d) {
    mount();
    if (PART === 'side') {
      document.getElementById('shellSide').innerHTML = sideHTML(d);
      return;
    }
    const top = document.getElementById('shellTop');
    const s = d.shell || {};
    const key = shapeKey(d);

    if (key === lastKey) {
      /* 只有秒數在動 —— 動兩個文字節點就好，其他 DOM 一概不碰 */
      const t = top.querySelector('.status .t');
      if (t) t.textContent = (s.run || {}).elapsed || '';
      const st = top.querySelector('.stage');           // 階段文字也是會動的
      if (st) st.textContent = (s.run || {}).stage || '';
      const w = top.querySelector('.shell-gate .txt small');
      if (w && s.gate) w.textContent = `已等待 ${s.gate.waiting} · 其餘專案照常執行`;
      return;                    // 高度不會變，不用回報
    }

    /* 形狀真的變了才重建。即使如此也把選單開合狀態接回去，
       因為專案名稱之類的東西也可能在選單開著時被改。 */
    const wasOpen = !!top.querySelector('#shellMore.open');
    lastKey = key;
    top.innerHTML = execHTML(d) + gateHTML(d) + activityHTML(d);
    applyBlocked(top, (d.shell || {}).blocked);
    if (wasOpen) document.getElementById('shellMore')?.classList.add('open');

    /* 決策橫幅出現時這一條會變高，但它是一個獨立的 Qt widget ——
       網頁改不了自己 widget 的高度，必須回報給 Python 去 setFixedHeight。
       沒有這一步，橫幅會被自己的 view 裁掉，而那正是 A-2 最不能出事的地方。 */
    const h = top.offsetHeight;
    if (h !== lastH) { lastH = h; onAction('shell_height', String(h)); }
  }

  /* 事件委派 —— 與各頁一致，任何插值都不進 inline handler */
  document.addEventListener('click', e => {
    const mb = e.target.closest('#shellMore [data-menu]');
    if (mb) {
      document.getElementById('shellMore').classList.toggle('open');
      return;
    }
    if (!e.target.closest('#shellMore')) {
      document.getElementById('shellMore')?.classList.remove('open');
    }
    const nav = e.target.closest('[data-nav]');
    if (nav) { onAction('navigate:' + nav.dataset.nav, ''); return; }
    /* 橫幅上的一項 = 切過去那個專案。刻意重用專案下拉選單那個動作，
       不發明新的：Python 那邊「切換專案」已經有一條完整的路（收拾目前的
       執行狀態、換掉每一頁的專案），第二條入口只會變成第二種切法。 */
    const go = e.target.closest('[data-shell-go]');
    if (go) { onAction('set:project', go.dataset.shellGo); return; }
    const btn = e.target.closest('[data-shell-act]');
    /* `disabled` 之外再問一次 BLOCKED：`.lineup` 那個摘要是 `<span>`，
       span 上的 `disabled` 什麼都不會做。今天沒有任何被擋的動作畫在 span 上，
       但這一行讓「哪天有」不會變成一顆看起來停用、實際上送得出去的控制項。 */
    if (btn && !btn.disabled && !BLOCKED[btn.dataset.shellAct])
      onAction(btn.dataset.shellAct, '');
  });
  document.addEventListener('change', e => {
    const sel = e.target.closest('[data-shell-set]');
    if (sel) onAction('set:' + sel.dataset.shellSet, sel.value);
  });

  /* 單獨用瀏覽器檢視設計時的示範外框。
     放在這裡而不是各頁，理由跟側邊欄一樣：一份就好。 */
  const DEMO = {
    active_page: 'dashboard',
    project: { name: 'notes-app',
               path: 'C:\\…\\notes-app\\notes-app',
               sensitivity: 'client' },
    projects: [{ id: 'kyc', name: 'notes-app' },
               { id: 'aicc', name: 'AICC 自身開發' },
               { id: 'csv', name: 'csv-summary' }],
    lineup: ['claude', 'codex'],
    /* active_slot 加回來了：Python 現在有真實來源
       （WorkflowWorker.active_role_changed，index 取自 EXECUTED_ROLES，
        沒在跑的時候不推這個 key —— 0 跟「第一格在跑」分不出來）。
       示範資料站在現況那一邊，現況變了它就跟著變。 */
    run: { state: 'running', label: '執行中', elapsed: '04:12',
           active_slot: 1, stage: 'Codex 審查中 · 第 2 步／共 2 步' },
    /* 今天的實際狀態就是 developing —— 示範資料照實機，不先畫一個好看的數字 */
    usage: { state: 'developing' },
    gate: { project: 'csv-summary', question: '要覆寫 index_v2.html 嗎？', waiting: '2 分 08 秒' },
    /* 順序照 Python 給的：waiting → error → running。示範資料刻意排出
       「聚焦的那一個也在裡面」（kyc, focused），因為那是最容易被實作成
       「從陣列拿掉」的一項，而拿掉會讓使用者看著的專案憑空消失。 */
    activity: [
      { id: 'csv', name: 'csv-summary',     state: 'waiting',
        stage: '需要使用者決策', focused: false },
      { id: 'aicc',  name: 'AICC 自身開發', state: 'error',
        stage: 'Codex 執行失敗', focused: false },
      { id: 'kyc',   name: 'notes-app', state: 'running',
        stage: 'Codex 正在審查', focused: true },
    ],
    drive: { label: '雲端已同步 · 3 分鐘前' },
    /* 現在按了不會有事的動作，附原因。示範資料刻意擋一個**選單裡**的
       （立即同步）而不是執行列上的：選單要打開才看得到，那正是最容易
       實作成「條件算了但沒套上去」的位置。 */
    blocked: { sync_now: '這個專案正在執行' },
  };

  return {
    render, DEMO,
    /* 各頁在載入時呼叫一次，把外框的動作導回同一個 bridge */
    connect(fn) { onAction = fn; },
  };
})();
