// 自動更新:比對 version.json(由 stamp.py 每次建置產生)與本頁的 build-id。
// ‧ 有新資料 → 右下角提示「重新整理」;若你離開分頁超過 30 分鐘再回來 → 直接重新載入。
// ‧ 建置超過 STALE_DAYS 天沒跑 → 顯示「自動更新可能停擺」,避免看著舊資料不自知。
// ‧ 本機直接開檔(沒有 version.json)時完全不動作。
(function () {
  'use strict';
  var POLL_MS = 10 * 60 * 1000;        // 前景每 10 分鐘檢查一次
  var AWAY_RELOAD_MS = 30 * 60 * 1000; // 離開超過 30 分鐘、回來時有新版 → 自動重整
  var STALE_DAYS = 4;                  // 建置超過 4 天未更新(含週末)→ 提醒

  var meta = document.querySelector('meta[name="build-id"]');
  var myId = meta ? meta.content : '';
  var page = decodeURIComponent(location.pathname.split('/').pop()) || 'index.html';
  var hiddenAt = document.hidden ? Date.now() : 0;
  var pending = null, box = null, timer = null;

  // 自動重整時帶的 ?_v= 只是為了繞過 HTTP 快取,載入後清掉,網址保持乾淨
  if (/[?&]_v=/.test(location.search) && history.replaceState) {
    var q = location.search.replace(/([?&])_v=[^&]*&?/, '$1').replace(/[?&]$/, '');
    history.replaceState(null, '', location.pathname + q + location.hash);
  }

  if ('serviceWorker' in navigator && location.protocol !== 'file:') {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('service-worker.js').then(function (r) { r.update && r.update(); }).catch(function () {});
    });
  }

  function ui() {
    if (box) return box;
    box = document.createElement('div');
    box.setAttribute('role', 'status');
    box.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:9999;max-width:calc(100vw - 32px);display:none;' +
      'align-items:center;gap:10px;padding:10px 12px 10px 14px;border-radius:12px;font:13px/1.4 -apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;' +
      'background:var(--surface,#fcfcfb);color:var(--tp,#0b0b0b);border:1px solid var(--border,#e2e2dd);box-shadow:0 6px 24px rgba(0,0,0,.18)';
    document.body.appendChild(box);
    return box;
  }
  function btn(label, primary, fn) {
    var b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = 'border:0;border-radius:8px;padding:6px 10px;font:inherit;cursor:pointer;white-space:nowrap;' +
      (primary ? 'background:var(--series-1,#2a78d6);color:#fff' : 'background:transparent;color:var(--muted,#8a8981)');
    b.onclick = fn;
    return b;
  }
  function show(text, actions, tone) {
    var b = ui();
    b.innerHTML = '';
    var t = document.createElement('span');
    t.textContent = text;
    if (tone === 'warn') t.style.color = 'var(--warn,#b77c00)';
    b.appendChild(t);
    actions.forEach(function (a) { b.appendChild(a); });
    b.style.display = 'flex';
  }
  function hide() { if (box) box.style.display = 'none'; }

  function reload(id) {
    var sep = location.search ? '&' : '?';
    location.replace(location.pathname + location.search + sep + '_v=' + encodeURIComponent(id || Date.now()) + location.hash);
  }

  function days(iso) { var t = Date.parse(iso); return isNaN(t) ? 0 : (Date.now() - t) / 864e5; }

  function check(cameBackAfter) {
    if (!myId || !navigator.onLine) return;
    fetch('version.json?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (v) {
        if (!v || !v.pages) return;
        var cur = v.pages[page];
        if (cur && cur.id && cur.id !== myId) {
          pending = cur;
          if (cameBackAfter > AWAY_RELOAD_MS) return reload(cur.id);
          show('資料已更新' + (cur.asof ? '(至 ' + cur.asof + ')' : ''), [
            btn('重新整理', true, function () { reload(cur.id); }),
            btn('稍後', false, hide)
          ]);
        } else if (v.built && days(v.built) > STALE_DAYS) {
          show('自動更新可能停擺:上次建置在 ' + Math.floor(days(v.built)) + ' 天前', [btn('知道了', false, hide)], 'warn');
        }
      })
      .catch(function () {});
  }

  function schedule() {
    clearInterval(timer);
    if (!document.hidden) timer = setInterval(function () { check(0); }, POLL_MS);
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { hiddenAt = Date.now(); }
    else { var away = hiddenAt ? Date.now() - hiddenAt : 0; hiddenAt = 0; check(away); }
    schedule();
  });
  window.addEventListener('online', function () { check(0); });
  window.addEventListener('load', function () { setTimeout(function () { check(0); }, 3000); schedule(); });
})();
