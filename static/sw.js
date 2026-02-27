const SW_VERSION = (() => {
  try {
    return new URL(self.location.href).searchParams.get("v") || "dev";
  } catch (e) {
    return "dev";
  }
})();

const CACHE_PREFIX = "pesk-messenger-";
const CACHE_NAME = `${CACHE_PREFIX}${SW_VERSION}`;

function versioned(url) {
  if (!SW_VERSION) return url;
  if (url.includes("?")) return `${url}&v=${encodeURIComponent(SW_VERSION)}`;
  return `${url}?v=${encodeURIComponent(SW_VERSION)}`;
}

const APP_SHELL = [
  "/",
  "/static/css/tailwind.css",
  "/static/css/fonts.css",
  "/static/css/style.css",
  "/static/vendor/htmx.min.js",
  "/static/vendor/ws.min.js",
  "/static/vendor/alpine.min.js",
  "/static/vendor/hyperscript.min.js",
  "/static/logo.png",
  "/static/logo-large.png",
  "/static/favicon.ico",
  "/static/favicon-128.ico",
  "/static/favicon-16.png",
  "/static/favicon-32.png",
  "/static/pwa-icon-48.png",
  "/static/pwa-icon-72.png",
  "/static/pwa-icon-96.png",
  "/static/pwa-icon-128.png",
  "/static/pwa-icon-144.png",
  "/static/pwa-icon-152.png",
  "/static/pwa-icon-192.png",
  "/static/pwa-icon-256.png",
  "/static/pwa-icon-384.png",
  "/static/pwa-icon-512.png",
  "/static/pwa-maskable-192.png",
  "/static/pwa-maskable-512.png",
  "/static/pwa-apple-touch-180.png",
].map(versioned);

async function notifyClients(message) {
  const clientList = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of clientList) {
    try {
      client.postMessage(message);
    } catch (e) {
      continue;
    }
  }
}

async function cacheAppShell() {
  const cache = await caches.open(CACHE_NAME);
  const failed = [];

  for (const url of APP_SHELL) {
    try {
      const req = new Request(url, { cache: "reload" });
      const res = await fetch(req);
      if (!res || !res.ok) {
        failed.push(url);
        continue;
      }
      await cache.put(url, res.clone());
    } catch (e) {
      failed.push(url);
    }
  }
  return { ok: failed.length === 0, failed };
}

self.addEventListener("install", (event) => {
  event.waitUntil(Promise.resolve());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.map((k) => {
            if (!k.startsWith(CACHE_PREFIX)) return null;
            if (k === CACHE_NAME) return null;
            return caches.delete(k);
          })
        )
      )
      .then(() => self.clients.claim())
      .then(() => {
        // کمی صبر کن تا client controllerchange رو process کنه، بعد پیام بفرست
        return new Promise(resolve => setTimeout(resolve, 100));
      })
      .then(() => notifyClients({ type: "SW_ACTIVATED", version: SW_VERSION }))
  );
});

self.addEventListener("message", (event) => {
  const data = event && event.data;

  if (data === "SKIP_WAITING") {
    self.skipWaiting();
    return;
  }

  if (data && data.type === "GET_VERSION") {
    if (event.ports && event.ports[0]) {
      event.ports[0].postMessage({ type: "VERSION_REPLY", version: SW_VERSION });
    }
    return;
  }

  if (data === "PREPARE_UPDATE") {
    event.waitUntil(
      cacheAppShell().then((result) =>
        notifyClients({ type: "PWA_UPDATE_PREPARED", version: SW_VERSION, ...result })
      )
    );
  }
});

async function cacheFirst(req) {
  const cached = await caches.match(req);
  if (cached) return cached;

  let url;
  try {
    url = new URL(req.url);
  } catch (e) {
    url = null;
  }

  if (url && url.origin === self.location.origin) {
    if (!url.search) {
      const alt = await caches.match(versioned(url.pathname));
      if (alt) return alt;
    } else {
      const alt = await caches.match(url.pathname);
      if (alt) return alt;
    }
  }

  try {
    const res = await fetch(req);
    if (res && res.ok) {
      const cache = await caches.open(CACHE_NAME);
      await cache.put(req, res.clone());
    }
    return res;
  } catch (e) {
    return cached || new Response("", { status: 408 });
  }
}

async function networkFirstNavigation(req) {
  try {
    const res = await fetch(new Request(req, { cache: "no-store" }));
    if (res && res.ok) {
      const cache = await caches.open(CACHE_NAME);
      await cache.put(req, res.clone());
    }
    return res;
  } catch (e) {
    const cached = await caches.match(req);
    if (cached) return cached;
    return (await caches.match(versioned("/"))) || new Response("", { status: 503 });
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/ws/") || url.pathname.startsWith("/api/") || url.pathname.startsWith("/pwa/")) {
    return;
  }

  const isNavigation = req.mode === "navigate" || (req.headers.get("accept") || "").includes("text/html");
  if (isNavigation) {
    event.respondWith(networkFirstNavigation(req));
    return;
  }

  const isStatic = url.pathname.startsWith("/static/");
  if (isStatic) {
    event.respondWith(cacheFirst(req));
    return;
  }

  event.respondWith(fetch(req));
});

self.addEventListener("push", (event) => {
  event.waitUntil(
    (async () => {
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
      const url = data.url || "/";
      const options = {
        body: data.body || "",
        icon: "/static/logo.png",
        badge: "/static/logo.png",
        tag: `rtchat:${url}`,
        renotify: false,
        data: {
          url,
        },
      };

      await self.registration.showNotification(title, options);
    })()
  );
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