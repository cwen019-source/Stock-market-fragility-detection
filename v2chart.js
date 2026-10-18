// 結構風險 v2 互動圖(與舊版脆弱度趨勢圖相同操作):
//   滑過看數值 · 滾輪縮放(以游標日期為錨點)· 點一下固定(📌)· 點兩下取消
//   固定時 ← → 鍵逐月移動(Shift = 12 個月),超出視窗會自動平移;區間按鈕 5年/10年/15年/全部
// 資料:window.V2DATA[code] = {history, bears, onsets, levels};容器:<div class="v2c" data-code="TW" data-years="15">
(function () {
  'use strict';
  var EN = function () { return /^en/.test(document.documentElement.lang || ''); };
  var T = function (zh, en) { return EN() ? en : zh; };
  var LV = { zh: ['平穩', '留意', '結構脆弱', '主動減碼'], en: ['Calm', 'Watch', 'Vulnerable', 'Reduce longs'] };
  var NS = 'http://www.w3.org/2000/svg';
  var active = null;                                  // 最近操作的圖(鍵盤只控制它)

  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function pc(x) { return x == null ? '–' : Math.round(x * 100); }
  function colr(p) { return p == null ? 'var(--muted)' : p >= 0.8 ? 'var(--red)' : p >= 0.6 ? 'var(--warn)' : 'var(--ok)'; }

  function Chart(box) {
    var code = box.getAttribute('data-code');
    var D = (window.V2DATA || {})[code];
    if (!D) return;
    var H = D.history, N = H.dates.length;
    var TS = H.dates.map(function (d) { return Date.parse(d + '-15'); });
    var first = 0;
    while (first < N && H.comp_pct[first] == null) first++;
    var yrs = +box.getAttribute('data-years') || 0;
    var a = yrs ? Math.max(first, N - 1 - yrs * 12) : first, b = N - 1;
    var pinned = false, pinIdx = null, cur = null;
    var panel = box.closest('.v2');                     // 儀表板上的面板:同步四類分數等

    // 工具列
    var bar = document.createElement('div');
    bar.className = 'v2c-bar';
    bar.setAttribute('data-noi18n', '');
    var spans = [[60, '5年', '5y'], [120, '10年', '10y'], [180, '15年', '15y'], [0, '全部', 'All']];
    spans.forEach(function (s) {
      var bt = document.createElement('button');
      bt.type = 'button';
      bt.textContent = T(s[1], s[2]);
      bt.dataset.m = s[0];
      bt.onclick = function () { b = N - 1; a = s[0] ? Math.max(first, N - 1 - s[0]) : first; mark(s[0]); render(); };
      bar.appendChild(bt);
    });
    var hint = document.createElement('span');
    hint.className = 'v2c-hint';
    bar.appendChild(hint);
    var view = document.createElement('div');
    view.className = 'v2c-view';
    view.setAttribute('data-noi18n', '');
    var wrap = document.createElement('div');
    wrap.className = 'v2c-wrap';
    var tip = document.createElement('div');
    tip.className = 'v2c-tip';
    tip.setAttribute('data-noi18n', '');
    box.innerHTML = '';
    box.appendChild(bar);
    box.appendChild(view);
    box.appendChild(wrap);
    wrap.appendChild(tip);
    var svg = el('svg', { role: 'img' }, wrap);
    function mark(m) { bar.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', +x.dataset.m === m); }); }
    mark(yrs ? yrs * 12 : 0);

    var W, Hh = 200, L = 34, R = 10, Tp = 18, B = 22, X, Y;
    function idxAtX(px) {
      var t = TS[a] + (px - L) / (W - L - R) * (TS[b] - TS[a]);
      var lo = a, hi = b;
      while (lo < hi) { var mid = (lo + hi) >> 1; if (TS[mid] < t) lo = mid + 1; else hi = mid; }
      if (lo > a && Math.abs(TS[lo - 1] - t) < Math.abs(TS[lo] - t)) lo--;
      return Math.max(a, Math.min(b, lo));
    }
    function evIdx(ev) { var r = svg.getBoundingClientRect(); return idxAtX((ev.clientX - r.left) * (W / r.width)); }

    function render() {
      W = Math.max(560, wrap.clientWidth || 800);
      svg.setAttribute('viewBox', '0 0 ' + W + ' ' + Hh);
      svg.innerHTML = '';
      var t0 = TS[a], t1 = TS[b] || TS[a] + 1;
      X = function (t) { return L + (t - t0) / (t1 - t0) * (W - L - R); };
      Y = function (v) { return Tp + (1 - v) * (Hh - Tp - B); };
      (D.bears || []).forEach(function (bb) {
        var p = Math.max(Date.parse(bb.peak), t0), q = Math.min(Date.parse(bb.trough), t1);
        if (q > p) el('rect', { x: X(p), y: Tp, width: X(q) - X(p), height: Hh - Tp - B, fill: 'var(--red)', opacity: '.13' }, svg);
      });
      [0.2, 0.5, 0.8].forEach(function (v) {
        el('line', { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: 'var(--border)', 'stroke-dasharray': v === 0.8 ? '4 3' : '' }, svg);
        var tx = el('text', { x: L - 6, y: Y(v) + 4, 'text-anchor': 'end', 'font-size': 10, fill: 'var(--muted)' }, svg);
        tx.textContent = Math.round(v * 100);
      });
      var tickW = Math.max(1.5, Math.min(4, (W - L - R) / (b - a + 1) * 0.7));
      for (var i = a; i <= b; i++) if (H.level[i] != null && H.level[i] >= 2)
        el('rect', { x: X(TS[i]) - tickW / 2, y: Hh - B + 3, width: tickW, height: 5, fill: 'var(--warn)' }, svg);
      var pts = [], segs = [];
      for (var j = a; j <= b; j++) {
        if (H.comp_pct[j] == null) { if (pts.length > 1) segs.push(pts); pts = []; continue; }
        pts.push(X(TS[j]).toFixed(1) + ',' + Y(H.comp_pct[j]).toFixed(1));
      }
      if (pts.length > 1) segs.push(pts);
      segs.forEach(function (s) { el('polyline', { points: s.join(' '), fill: 'none', stroke: 'var(--series-1)', 'stroke-width': 1.6 }, svg); });
      (D.onsets || []).forEach(function (o) {
        var t = Date.parse(o.date);
        if (t < t0 || t > t1) return;
        var x = X(t);
        el('path', { d: 'M' + (x - 4) + ',' + (Tp - 14) + ' L' + (x + 4) + ',' + (Tp - 14) + ' L' + x + ',' + (Tp - 6) + ' Z', fill: 'var(--red)' }, svg);
      });
      // 年份刻度
      var y0 = new Date(t0).getFullYear(), y1 = new Date(t1).getFullYear(), span = y1 - y0 + 1;
      var step = span > 40 ? 10 : span > 20 ? 5 : span > 10 ? 2 : 1;
      for (var y = Math.ceil(y0 / step) * step; y <= y1; y += step) {
        var xx = X(Date.UTC(y, 0, 1));
        if (xx < L || xx > W - R) continue;
        var tt = el('text', { x: xx, y: Hh - 4, 'text-anchor': 'middle', 'font-size': 10, fill: 'var(--muted)' }, svg);
        tt.textContent = y;
      }
      cx = el('line', { x1: 0, x2: 0, y1: Tp - 4, y2: Hh - B, stroke: 'var(--tp)', opacity: 0, 'stroke-width': 1 }, svg);
      cd = el('circle', { r: 4, cx: 0, cy: 0, fill: 'var(--series-1)', stroke: 'var(--surface)', 'stroke-width': 2, opacity: 0 }, svg);
      if (pinned && pinIdx != null) show(pinIdx); else reset();
    }
    var cx, cd;

    function onsetIn(i) {
      var s = Date.parse(H.dates[i] + '-01'), e = i + 1 < N ? Date.parse(H.dates[i + 1] + '-01') : s + 32 * 864e5;
      return (D.onsets || []).filter(function (o) { var t = Date.parse(o.date); return t >= s && t < e; });
    }
    function show(i) {
      cur = i;
      var x = X(TS[i]), v = H.comp_pct[i];
      cx.setAttribute('x1', x); cx.setAttribute('x2', x); cx.setAttribute('opacity', pinned ? 0.6 : 0.35);
      if (v != null) { cd.setAttribute('cx', x); cd.setAttribute('cy', Y(v)); cd.setAttribute('opacity', 1); } else cd.setAttribute('opacity', 0);
      var lv = H.level[i], ons = onsetIn(i);
      var html = (pinned ? '📌 ' : '') + '<b>' + H.dates[i] + '</b><br>' + T('綜合 ', 'Composite ') + pc(v) +
        ' · ' + (lv == null ? '–' : LV[EN() ? 'en' : 'zh'][lv]) +
        (H.redzone[i] ? ' · <span style="color:var(--red)">' + T('紅區', 'red zone') + '</span>' : '') +
        '<br>V ' + pc(H.V[i]) + ' · L ' + pc(H.L[i]) + ' · M ' + pc(H.M[i]) + ' · S ' + pc(H.S[i]) +
        (H.gar[i] != null ? '<br>' + T('12月壞情境 ', '12m bad case ') + (H.gar[i] * 100).toFixed(0) + '%' : '');
      ons.forEach(function (o) {
        html += '<br><span style="color:var(--red)">▼ ' + T('主動減碼日 ', 'Reduce longs ') + o.date +
          (o.maxloss12m != null ? T(' · 之後12月最深 ', ' · next-12m worst ') + (o.maxloss12m * 100).toFixed(0) + '%' : '') + '</span>';
      });
      tip.innerHTML = html;
      tip.style.opacity = 1;
      var r = svg.getBoundingClientRect(), sc = r.width / W, px = x * sc;
      var tw = tip.offsetWidth || 180;
      tip.style.left = (px + 12 + tw > r.width ? Math.max(0, px - tw - 12) : px + 12) + 'px';
      tip.style.top = '6px';
      view.innerHTML = pinned ? T('📌 已固定於 ' + H.dates[i] + '(← → 鍵逐月移動,Shift 一次 12 個月;點兩下取消)',
                                    '📌 Pinned at ' + H.dates[i] + ' (← → move by month, Shift = 12 months; double-click to release)')
                              : T('檢視 ' + H.dates[i] + ' — 上方四類分數同步(點一下可固定)', 'Viewing ' + H.dates[i] + ' — category scores above follow the cursor (click to pin)');
      sync(i);
    }
    function reset() {
      cur = null;
      if (cx) { cx.setAttribute('opacity', 0); cd.setAttribute('opacity', 0); }
      tip.style.opacity = 0;
      view.innerHTML = T('最新(' + H.dates[N - 1] + ')— 滑過看數值 · 滾輪縮放 · 點一下固定 · 固定後 ← → 移動',
                         'Latest (' + H.dates[N - 1] + ') — hover for values · scroll to zoom · click to pin · ← → to move when pinned');
      sync(null);
    }
    // 同步面板上的四類分數、綜合分數、壞情境、等級
    function sync(i) {
      if (!panel) return;
      var latest = i == null;
      ['V', 'L', 'M', 'S'].forEach(function (k) {
        var e = panel.querySelector('[data-v2p="' + k + '"]');
        if (!e) return;
        if (e.dataset.orig == null) e.dataset.orig = e.textContent;
        var v = latest ? null : H[k][i];
        e.textContent = latest ? e.dataset.orig : pc(v);
        if (!latest) e.style.color = colr(v); else e.style.color = e.dataset.origc || (e.dataset.origc = e.style.color);
        var barI = panel.querySelector('[data-v2bar="' + k + '"]');
        if (barI) {
          if (barI.dataset.orig == null) { barI.dataset.orig = barI.style.width; barI.dataset.origc = barI.style.background; }
          barI.style.width = latest ? barI.dataset.orig : ((v || 0) * 100) + '%';
          barI.style.background = latest ? barI.dataset.origc : colr(v);
        }
      });
      var c = panel.querySelector('[data-v2k="comp"]'), g = panel.querySelector('[data-v2k="gar"]');
      if (c) { if (c.dataset.orig == null) c.dataset.orig = c.textContent; c.textContent = latest ? c.dataset.orig : pc(H.comp_pct[i]); }
      if (g) { if (g.dataset.orig == null) g.dataset.orig = g.textContent; g.textContent = latest ? g.dataset.orig : (H.gar[i] == null ? '–' : (H.gar[i] > 0 ? '+' : '') + (H.gar[i] * 100).toFixed(0) + '%'); }
      panel.classList.toggle('v2-hist', !latest);
      var hd = panel.querySelector('[data-v2k="histdate"]');
      if (hd) hd.textContent = latest ? '' : (EN() ? ' · showing ' : ' · 顯示 ') + H.dates[i];
    }

    // 縮放:以游標日期為錨點,最少 12 個月
    function zoomAt(ci, f) {
      var span = Math.max(1, b - a), ns = Math.round(span * f);
      ns = Math.max(12, Math.min(N - 1 - first, ns));
      if (ns === span && f < 1) ns = Math.max(12, span - 1);
      var frac = (ci - a) / span, na = Math.round(ci - frac * ns), nb = na + ns;
      if (na < first) { nb += first - na; na = first; }
      if (nb > N - 1) { na -= nb - (N - 1); nb = N - 1; }
      na = Math.max(first, na);
      if (nb - na < 12) return;
      a = na; b = nb; mark(-1); render();
    }
    svg.addEventListener('wheel', function (ev) { ev.preventDefault(); active = api; zoomAt(evIdx(ev), ev.deltaY < 0 ? 0.82 : 1.22); }, { passive: false });
    svg.addEventListener('mousemove', function (ev) { active = api; if (!pinned) show(evIdx(ev)); });
    svg.addEventListener('mouseleave', function () { if (pinned) show(pinIdx); else reset(); });
    svg.addEventListener('click', function (ev) {
      active = api;
      var i = evIdx(ev);
      if (pinned && Math.abs(i - pinIdx) <= 0) { unpin(); return; }
      pinned = true; pinIdx = i; show(i);
    });
    svg.addEventListener('dblclick', function () { unpin(); });
    function unpin() { pinned = false; pinIdx = null; reset(); }
    // 鍵盤:固定時 ← → 逐月(Shift 12 個月);超出視窗自動平移
    function key(dir, big) {
      if (!pinned) return false;
      var ni = Math.max(first, Math.min(N - 1, pinIdx + dir * (big ? 12 : 1)));
      if (ni === pinIdx) return true;
      pinIdx = ni;
      if (ni < a || ni > b) {
        var span = b - a, na = ni < a ? ni : ni - span;
        na = Math.max(first, Math.min(N - 1 - span, na));
        a = na; b = na + span; mark(-1); render();
      } else show(ni);
      return true;
    }
    var api = { key: key, pinned: function () { return pinned; } };
    window.addEventListener('resize', function () { render(); });
    render();
    return api;
  }

  // 點到 v2 圖以外的地方 → 鍵盤交還給頁面上其他圖(例如舊版趨勢圖)
  document.addEventListener('mousedown', function (e) { if (!e.target.closest || !e.target.closest('.v2c')) active = null; }, true);
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    var tg = e.target;
    if (tg && /INPUT|SELECT|TEXTAREA/.test(tg.tagName)) return;
    if (active && active.pinned() && active.key(e.key === 'ArrowLeft' ? -1 : 1, e.shiftKey)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);

  function init() {
    var boxes = document.querySelectorAll('.v2c[data-code]');
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].offsetParent === null && boxes[i].closest('.L-en,.L-zh')) {
        // 隱藏語系的圖延後到可見時再畫(方法論頁中/英兩份)
        if (getComputedStyle(boxes[i].closest('.L-en,.L-zh')).display === 'none') continue;
      }
      Chart(boxes[i]);
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
