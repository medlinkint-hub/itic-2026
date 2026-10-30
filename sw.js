// Offline support for the ITIC planner: the app shell is cached on the phone so it opens
// without signal. Team data itself is cached by Firestore's offline persistence.
const CACHE = "itic-2026-v1";
const CORE = ["./", "./index.html", "./manifest.json", "./icon.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

const timeout = ms => new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms));

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Pages: try the network first (so updates arrive), fall back to the saved copy on weak Wi-Fi.
  if (req.mode === "navigate") {
    e.respondWith(Promise.race([fetch(req), timeout(4000)])
      .then(res => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put("./index.html", copy)); }
        return res;
      })
      .catch(() => caches.match("./index.html")));
    return;
  }

  // Live Firestore traffic is never cached here.
  const cacheable = url.origin === location.origin || url.hostname === "www.gstatic.com" ||
    url.hostname === "fonts.gstatic.com" || url.hostname === "fonts.googleapis.com";
  if (!cacheable) return;

  // Scripts, fonts, icons: serve the saved copy at once and refresh it in the background.
  e.respondWith(caches.match(req).then(hit => {
    const net = fetch(req).then(res => {
      if (res.ok || res.type === "opaque") { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return res;
    }).catch(() => hit);
    return hit || net;
  }));
});
