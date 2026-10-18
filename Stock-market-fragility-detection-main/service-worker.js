// 脆弱度儀表板 PWA service worker
// 策略:本站頁面 network-first 並強制向伺服器重新驗證(拿得到就一定是最新,離線才退回快取);
//      version.json 永遠走網路不快取;FinMind/FRED/stockanalysis 等跨站請求不攔截(即時抓)。
const CACHE = 'fragility-v3';
const SHELL = ['./', 'index.html', 'us.html', 'autoupdate.js', 'i18n.js', 'manifest.webmanifest', 'icon-192.png', 'icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const u = new URL(req.url);
  if (u.origin !== location.origin) return;                  // 跨站 → 走網路
  if (u.pathname.endsWith('/version.json')) return;          // 版本檔 → 永遠走網路
  const key = u.origin + u.pathname;                         // 忽略 ?_v= / ?v= 之類的查詢字串
  e.respondWith(
    fetch(req, { cache: 'no-cache' })
      .then(r => {
        if (r.ok) { const cp = r.clone(); caches.open(CACHE).then(c => c.put(key, cp)); }
        return r;
      })
      .catch(() => caches.match(key).then(m => m || (req.mode === 'navigate' ? caches.match(u.origin + u.pathname.replace(/[^/]*$/, '') + 'index.html') : undefined))
        .then(m => m || Response.error()))
  );
});
