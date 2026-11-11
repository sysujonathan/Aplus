"use strict";
/* Aplus 盘前 PWA service worker：
   - 应用外壳（页面/manifest/图标）缓存优先，装过一次后离线也能打开；
   - data/ 下的快照数据永远走网络（no-store），不许拿昨天的候选冒充今天的。 */
/* 改动任何外壳文件（index.html/manifest/图标）必须同步 bump 此版本号，
   否则已装机的外壳不会更新。v3：XSS 转义/搜索失焦/竞态/新鲜度闸门修复。 */
const SHELL_CACHE = "aplus-mobile-shell-v3";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;
  if (url.pathname.includes("/data/")) {
    event.respondWith(fetch(event.request, { cache: "no-store" }));
    return;
  }
  event.respondWith(
    caches.match(event.request, { ignoreSearch: true }).then((hit) => hit || fetch(event.request))
  );
});
