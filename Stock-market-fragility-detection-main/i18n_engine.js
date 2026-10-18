// 中 / EN 語言切換(build_i18n.py 會把字典 window.I18N_EN 接在本檔前面,輸出 i18n.js)
// 作法:英文模式下把頁面文字中的中文片語依字典替換(最長優先),並用 MutationObserver 持續翻譯
// 之後由各頁 JS 動態產生的內容;title / placeholder / aria-label 也一併處理。選擇存在 localStorage。
(function () {
  'use strict';
  var KEY = 'lang', lang = null;
  try { lang = localStorage.getItem(KEY); } catch (e) {}
  if (lang !== 'en' && lang !== 'zh') lang = /[?&]lang=en\b/.test(location.search) ? 'en' : 'zh';   // 預設中文;?lang=en 或按鈕切換
  var root = document.documentElement;
  root.setAttribute('lang', lang === 'en' ? 'en' : 'zh-Hant');
  window.__lang = lang;

  function addButton() {
    if (document.getElementById('langbtn')) return;
    var b = document.createElement('button');
    b.id = 'langbtn';
    b.type = 'button';
    b.setAttribute('data-noi18n', '');
    b.textContent = lang === 'en' ? '中文' : 'EN';
    b.title = lang === 'en' ? '切換為中文' : 'Switch to English';
    var right = document.getElementById('th') ? 62 : 12;
    b.style.cssText = 'position:fixed;top:12px;right:' + right + 'px;z-index:50;background:var(--surface,#fff);color:var(--tp,#111);' +
      'border:1px solid var(--border,#ddd);border-radius:8px;padding:6px 10px;cursor:pointer;font:600 12px/1.2 -apple-system,Segoe UI,sans-serif';
    b.onclick = function () {
      try { localStorage.setItem(KEY, lang === 'en' ? 'zh' : 'en'); } catch (e) {}
      location.reload();
    };
    document.body.appendChild(b);
  }

  if (lang !== 'en') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addButton); else addButton();
    return;
  }

  // ---------------- 英文模式 ----------------
  var st = document.createElement('style');
  st.textContent = 'html.i18n-wait body{visibility:hidden}';
  document.head.appendChild(st);
  root.classList.add('i18n-wait');
  setTimeout(function () { root.classList.remove('i18n-wait'); }, 2500);   // 保險:最多遮 2.5 秒

  var D = window.I18N_EN || {};
  var keys = Object.keys(D).sort(function (a, b) { return b.length - a.length; });
  var esc = function (s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); };
  var RX = keys.length ? new RegExp(keys.map(esc).join('|'), 'g') : null;
  var CJK = /[㐀-鿿豈-﫿]/;
  var FW = { '，': ', ', '：': ': ', '；': '; ', '（': ' (', '）': ') ', '、': ', ', '。': '. ', '「': '“', '」': '”', '！': '! ', '？': '? ', '～': '~', '％': '%', '＋': '+', '／': '/', '＝': '=', '　': ' ' };
  var SEP = '\u0001';

  // 公司名稱是專有名詞:從各頁內嵌資料(全域 D)收集「不在字典裡」的短中文字串,翻譯時整串保護不動
  var NRX = null;
  function collectNames() {
    var seen = {}, names = [];
    function add(v) {
      if (typeof v === 'string' && v.length >= 2 && v.length <= 8 && CJK.test(v) && !D[v] && !seen[v]) { seen[v] = 1; names.push(v); }
    }
    function scan(o, depth) {
      if (!o || depth > 4) return;
      if (Array.isArray(o)) { for (var i = 0; i < o.length && i < 50000; i++) { var x = o[i]; if (typeof x === 'string') add(x); else if (typeof x === 'object') scan(x, depth + 1); } return; }
      if (typeof o === 'object') for (var k in o) { var v = o[k]; if (k === 'n' || k === 'name' || k === 'nm') add(v); else if (v && typeof v === 'object') scan(v, depth + 1); }
    }
    try { if (typeof D_ !== 'undefined') scan(D_, 0); } catch (e) {}
    try { /* eslint-disable-next-line no-undef */ var PD = (0, eval)('typeof D!=="undefined"?D:null'); if (PD && PD.stocks) scan(PD.stocks, 0); if (PD && PD.members) scan(PD.members, 0); if (PD && PD.inds) scan(PD.inds, 0); } catch (e) {}
    if (names.length) { names.sort(function (a, b) { return b.length - a.length; }); NRX = new RegExp(names.map(esc).join('|'), 'g'); }
  }

  function tr(s) {
    if (!s || !CJK.test(s)) return s;
    var lead = s.match(/^\s*/)[0], trail = s.match(/\s*$/)[0];
    var keep = [];
    if (NRX) {   // 整段只有公司名稱(表格儲存格、成分股清單)→ 原樣保留;句子中的片語照常翻譯
      NRX.lastIndex = 0;
      var rest = s.replace(NRX, '').replace(/[\s,\u3001\uff0c()\uff08\uff09\-\/\d.%:\uff1a·]+/g, '');
      NRX.lastIndex = 0;
      if (!CJK.test(rest)) return s;
    }
    var t = RX ? s.replace(RX, function (m) { return SEP + D[m] + SEP; }) : s;
    t = t.replace(/[，：；（）、。「」！？～％＋／＝　]/g, function (c) { return FW[c]; });
    t = t.replace(/\u0001+/g, ' ')
      .replace(/(\d)\s+(d|m|y|yr|mo|x|×)(?![a-z])/g, '$1$2')        // 20 d → 20d
      .replace(/\s+([,.;:!?)%])/g, '$1')
      .replace(/\(\s+/g, '(')
      .replace(/([(“])\s+/g, '$1').replace(/\s+(”)/g, '$1')
      .replace(/\s+-(yr|d|m|day|month|year)/g, '-$1')
      .replace(/[ \t]{2,}/g, ' ')
      .trim();
    return lead + t + trail;
  }
  window.__tr = tr;

  var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1, CODE: 0 };
  function skipped(el) {
    for (var n = el; n && n.nodeType === 1; n = n.parentNode) {
      if (SKIP[n.nodeName] || (n.hasAttribute && n.hasAttribute('data-noi18n'))) return true;
    }
    return false;
  }
  var ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];
  function doAttrs(el) {
    for (var i = 0; i < ATTRS.length; i++) {
      var v = el.getAttribute && el.getAttribute(ATTRS[i]);
      if (v && CJK.test(v)) el.setAttribute(ATTRS[i], tr(v));
    }
    if (el.nodeName === 'INPUT' && /^(button|submit|reset)$/i.test(el.type) && CJK.test(el.value)) el.value = tr(el.value);
  }
  var ALNUM = /[A-Za-z0-9)\]%]/;
  function trNode(n) {
    var o = n.nodeValue, t = tr(o);
    if (t === o) return;
    // 中文相鄰元素之間沒有空白;翻成英文後補空白,避免 "Slow layerStructural"
    if (CJK.test(o.charAt(0)) && /^[A-Za-z0-9(]/.test(t) && n.previousSibling) {
      var pv = n.previousSibling.textContent || '';
      if (pv && ALNUM.test(pv.charAt(pv.length - 1))) t = ' ' + t;
    }
    if (CJK.test(o.charAt(o.length - 1)) && ALNUM.test(t.charAt(t.length - 1)) && n.nextSibling) {
      var nx = n.nextSibling.textContent || '';
      if (nx && /^[A-Za-z0-9(]/.test(nx) || (n.nextSibling.nodeType === 1 && CJK.test(nx.charAt(0)))) t = t + ' ';
    }
    n.nodeValue = t;
  }
  function walk(node) {
    if (node.nodeType === 3) {
      if (CJK.test(node.nodeValue) && !skipped(node.parentNode)) trNode(node);
      return;
    }
    if (node.nodeType !== 1 || skipped(node)) return;
    doAttrs(node);
    var w = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (n.nodeType === 1) return (SKIP[n.nodeName] || n.hasAttribute('data-noi18n')) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
        return CJK.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });
    var n, texts = [];
    while ((n = w.nextNode())) { if (n.nodeType === 3) texts.push(n); else doAttrs(n); }
    for (var i = 0; i < texts.length; i++) trNode(texts[i]);
  }

  function start() {
    collectNames();
    if (document.title && CJK.test(document.title)) document.title = tr(document.title);
    walk(document.body);
    addButton();
    root.classList.remove('i18n-wait');
    new MutationObserver(function (ms) {
      for (var i = 0; i < ms.length; i++) {
        var m = ms[i];
        if (m.type === 'characterData') { var t = m.target; if (CJK.test(t.nodeValue) && !skipped(t.parentNode)) trNode(t); }
        else if (m.type === 'attributes') { doAttrs(m.target); }
        else for (var j = 0; j < m.addedNodes.length; j++) walk(m.addedNodes[j]);
      }
      if (document.title && CJK.test(document.title)) document.title = tr(document.title);
    }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
