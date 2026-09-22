/* Offline support for the app shell.
 *
 * Strategy: network-first with a cache fallback, for everything.
 *
 * Cache-first is the usual advice for an app shell, and it is wrong here. The
 * shell is ~30 KB and it reads data files whose shape changes as the parsers
 * improve. A cache-first worker keeps serving an old app.js indefinitely, so a
 * fix pushed to the site never reaches a phone that has already installed it -
 * and an old app.js reading new data is exactly how a wrong number gets shown
 * confidently. Correctness beats the few milliseconds cache-first would save.
 *
 * Data files are never served from cache at all: a stale trade presented as
 * current is worse than no trade. Offline, the app says so instead.
 */
const SHELL = 'insider-shell-v2';
const FILES = [
  './', './index.html', './app.css', './app.js',
  './manifest.webmanifest', './icons/icon-192.png', './icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL)
      .then((cache) => cache.addAll(FILES))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin) return;

  // Data: network only. Never hand back a cached trade.
  if (url.pathname.includes('/data/')) {
    event.respondWith(
      fetch(event.request).catch(() => new Response(
        JSON.stringify({ error: 'offline' }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }
      ))
    );
    return;
  }

  // Shell: network-first, refreshing the cache, falling back when offline.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(SHELL).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then(
        (hit) => hit || caches.match('./index.html')
      ))
  );
});
