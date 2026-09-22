/* Offline shell cache.
 *
 * The app shell is cached so the PWA opens without a connection. Data files are
 * deliberately NOT cached: showing yesterday's trades as if they were current
 * would be worse than showing nothing. They are fetched network-first, and the
 * app states its own staleness from meta.json.
 */
const SHELL = 'insider-shell-v1';
const FILES = [
  './', './index.html', './app.css', './app.js',
  './manifest.webmanifest', './icons/icon-192.png', './icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
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

  // Data is always network-first; never serve a stale trade from cache.
  if (url.pathname.includes('/data/')) {
    event.respondWith(fetch(event.request).catch(() => new Response('{}', {
      status: 503, headers: { 'Content-Type': 'application/json' },
    })));
    return;
  }

  event.respondWith(
    caches.match(event.request).then((hit) => hit || fetch(event.request))
  );
});
