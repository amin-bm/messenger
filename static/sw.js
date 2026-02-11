const CACHE_NAME = "pesk-messenger-v10";

const APP_SHELL = [
  "/",
  "/static/css/tailwind.css?v=3",
  "/static/css/fonts.css",
  "/static/css/style.css?v=7",
  "/static/vendor/htmx.min.js",
  "/static/vendor/ws.min.js",
  "/static/vendor/alpine.min.js",
  "/static/vendor/hyperscript.min.js",
  "/static/logo.png",
  "/static/logo-large.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.map((k) => (k === CACHE_NAME ? null : caches.delete(k)))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  const isNavigation = req.mode === "navigate" || (req.headers.get("accept") || "").includes("text/html");
  if (isNavigation) {
    event.respondWith(
      fetch(req)
        .catch(() => caches.match("/"))
    );
    return;
  }

  const isStatic = url.pathname.startsWith("/static/");
  if (!isStatic) {
    event.respondWith(fetch(req));
    return;
  }

  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
        return res;
      });
    })
  );
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    try {
      data = { body: event.data ? event.data.text() : "" };
    } catch (e2) {
      data = {};
    }
  }

  const title = data.title || "پیام جدید";
  const options = {
    body: data.body || "",
    icon: "/static/logo.png",
    badge: "/static/logo.png",
    data: {
      url: data.url || "/"
    }
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  const url = (event.notification && event.notification.data && event.notification.data.url) || "/";
  event.notification && event.notification.close();

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url === url || client.url.endsWith(url)) {
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
