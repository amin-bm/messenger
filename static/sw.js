const SW_VERSION = (() => {
  try {
    return new URL(self.location.href).searchParams.get("v") || "dev";
  } catch (e) {
    return "dev";
  }
})();

const CACHE_PREFIX = "pesk-messenger-";
const CACHE_NAME = `${CACHE_PREFIX}${SW_VERSION}`;

// حداکثر زمانی که منتظر شبکه می‌مانیم تا respondWith هیچ‌وقت برای همیشه معلق نماند.
const NAV_TIMEOUT_MS = 8000;

// ── صفحات حساس به session/CSRF ───────────────────────────────────────────────
// HTML این مسیرها شامل csrfmiddlewaretoken است که به کوکی csrftoken همان لحظه گره خورده.
// اگر نسخه‌ی کش‌شده‌ی این صفحات بعداً سرو شود، token کهنه است و submit کاربر
// با خطای «403 - تأیید نشد. درخواست لغو شد CSRF» رد می‌شود.
// پس این صفحات: نه کش می‌شوند، نه از کش سرو می‌شوند.
const AUTH_SENSITIVE_PREFIXES = ["/accounts/", "/otp/", "/profile/onboarding"];

function isAuthSensitiveHtml(pathname) {
  for (const p of AUTH_SENSITIVE_PREFIXES) {
    if (pathname.startsWith(p)) return true;
  }
  return false;
}

// پاک‌سازی نسخه‌های کش‌شده‌ی صفحات حساس که ممکن است از نسخه‌های قبلی اپ باقی مانده باشند.
async function purgeAuthSensitiveCache() {
  let removed = 0;
  try {
    const cache = await caches.open(CACHE_NAME);
    const keys = await cache.keys();
    for (const request of keys) {
      let pathname = "";
      try {
        pathname = new URL(request.url).pathname;
      } catch (e) {
        continue;
      }
      if (isAuthSensitiveHtml(pathname)) {
        try {
          await cache.delete(request);
          removed += 1;
        } catch (e) {}
      }
    }
  } catch (e) {}
  return removed;
}

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
  "/static/notification-badge-96.png",
].map(versioned);

// یک صفحه‌ی fallback حداقلی که هیچ‌وقت سفید نیست و به‌محض برگشتن اتصال، خودش را رفرش می‌کند.
function offlineFallbackResponse() {
  const html = `<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>در حال اتصال…</title>
<style>
  html,body{height:100%;margin:0}
  body{display:flex;align-items:center;justify-content:center;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,sans-serif;
    background:#0ea5e9;color:#fff;text-align:center;padding:24px}
  .card{max-width:360px}
  .spin{width:36px;height:36px;border:3px solid rgba(255,255,255,.35);
    border-top-color:#fff;border-radius:50%;margin:0 auto 16px;
    animation:s 1s linear infinite}
  @keyframes s{to{transform:rotate(360deg)}}
  button{margin-top:16px;border:none;background:#fff;color:#0ea5e9;
    font-size:15px;font-weight:700;padding:10px 18px;border-radius:10px}
</style>
</head>
<body>
  <div class="card">
    <div class="spin"></div>
    <div style="font-size:16px;font-weight:700">در حال اتصال به پسک…</div>
    <div style="font-size:13px;opacity:.85;margin-top:8px">اتصال شبکه هنوز آماده نشده است. خودکار دوباره تلاش می‌کنیم.</div>
    <button onclick="location.reload()">تلاش دوباره</button>
  </div>
  <script>
    // به‌محض برقراری اتصال یا هر چند ثانیه یک‌بار، صفحه را دوباره لود کن.
    function retry(){ location.reload(); }
    window.addEventListener('online', retry);
    setTimeout(retry, 3000);
  </script>
</body>
</html>`;
  return new Response(html, {
    status: 200,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

// لاگ تشخیصی سرویس‌ورکر → به /pwa/log می‌فرستد (این مسیر توسط fetch-handler رهگیری نمی‌شود).
function swLog(ev, data) {
  try {
    const body = JSON.stringify(
      Object.assign({ ev: ev, v: SW_VERSION, t: Date.now(), src: "sw" }, data || {})
    );
    fetch("/pwa/log", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: body,
      keepalive: true,
    }).catch(function () {});
  } catch (e) {}
}

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
  // «/» را به‌عنوان fallback ناوبری از قبل کش کن تا اگر شبکه هنگام باز شدن اپ آماده نبود،
  // چیزی برای نمایش وجود داشته باشد (به‌جای صفحه‌ی سفید). این کار best-effort است.
  event.waitUntil(
    (async () => {
      swLog("sw_install");
      let rootCached = false;
      let rootStatus = 0;
      let rootRedirected = false;
      try {
        const cache = await caches.open(CACHE_NAME);
        try {
          const rootRes = await fetch(new Request("/", { cache: "reload" }));
          rootStatus = rootRes ? rootRes.status : 0;
          rootRedirected = !!(rootRes && rootRes.redirected);
          // مهم: پاسخِ ریدایرکت‌شده (مثلاً صفحه‌ی لاگین) را به‌عنوان «/» کش نکن،
          // وگرنه fallback ناوبری ممکن است صفحه‌ی اشتباه/سفید نشان دهد.
          if (rootRes && rootRes.ok && !rootRes.redirected) {
            await cache.put("/", rootRes.clone());
            rootCached = true;
          }
        } catch (e) {}
      } catch (e) {}
      swLog("sw_install_done", { rootCached: rootCached, rootStatus: rootStatus, rootRedirected: rootRedirected });
    })()
  );
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
      .then(() => purgeAuthSensitiveCache())
      .then((removed) => { if (removed) swLog("sw_purge_sensitive", { removed: removed }); })
      .then(() => self.clients.claim())
      .then(() => {
        // کمی صبر کن تا client controllerchange رو process کنه، بعد پیام بفرست
        return new Promise(resolve => setTimeout(resolve, 100));
      })
      .then(() => { swLog("sw_activate"); return notifyClients({ type: "SW_ACTIVATED", version: SW_VERSION }); })
  );
});

/* ===== Background upload manager (chunked, survives page navigation) ===== */
const UPLOAD_QUEUE = [];
const UPLOAD_STATE = new Map(); // uploadId -> state
const UPLOAD_ABORTERS = new Map(); // uploadId -> AbortController
let UPLOAD_RUNNING = false;

function swGenerateUuid() {
  try {
    if (self.crypto && typeof self.crypto.randomUUID === "function") return self.crypto.randomUUID();
  } catch (e) {}
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    const v = ch === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function uploadSnapshot(s) {
  const total = Number(s.totalBytes) || 0;
  const sent = Number(s.sentBytes) || 0;
  return {
    uploadId: s.uploadId,
    chatKey: s.chatKey,
    fileName: s.fileName,
    fileSize: s.fileSize,
    sentBytes: sent,
    totalBytes: total,
    percent: total > 0 ? Math.min(100, (sent / total) * 100) : (s.status === "done" ? 100 : 0),
    status: s.status,
    batchId: s.batchId,
    batchIndex: s.batchIndex,
    batchTotal: s.batchTotal,
    error: s.error || null,
  };
}

function enqueueUpload(item) {
  const total = Number((item.file && item.file.size) || item.fileSize || 0);
  const state = {
    uploadId: item.uploadId,
    chatKey: item.chatKey,
    fileName: item.fileName || "file",
    fileSize: total,
    sentBytes: 0,
    totalBytes: total,
    status: "queued",
    batchId: item.batchId || null,
    batchIndex: Number(item.batchIndex) || 0,
    batchTotal: Number(item.batchTotal) || 1,
    error: null,
  };
  UPLOAD_STATE.set(item.uploadId, state);
  UPLOAD_QUEUE.push({ item: item, serverUploadId: swGenerateUuid() });
  notifyClients({ type: "UPLOAD_PROGRESS", upload: uploadSnapshot(state) });
  processUploadQueue();
}

async function processUploadQueue() {
  if (UPLOAD_RUNNING) return;
  UPLOAD_RUNNING = true;
  try {
    while (UPLOAD_QUEUE.length) {
      const job = UPLOAD_QUEUE.shift();
      await runUploadJob(job);
    }
  } finally {
    UPLOAD_RUNNING = false;
  }
}

async function runUploadJob(job) {
  const item = job.item;
  const state = UPLOAD_STATE.get(item.uploadId);
  if (!state) return;
  if (state.status === "canceled") { UPLOAD_STATE.delete(item.uploadId); return; }

  const controller = new AbortController();
  UPLOAD_ABORTERS.set(item.uploadId, controller);
  state.status = "uploading";
  notifyClients({ type: "UPLOAD_PROGRESS", upload: uploadSnapshot(state) });

  const file = item.file;
  const chunkSize = Number(item.chunkSize) || 512 * 1024;
  const totalBytes = Number((file && file.size) || 0);
  const totalChunks = Math.max(1, Math.ceil(totalBytes / chunkSize));

  try {
    for (let i = 0; i < totalChunks; i++) {
      if (state.status === "canceled") throw new Error("CANCELED");
      const start = i * chunkSize;
      const end = Math.min(start + chunkSize, totalBytes);
      const blob = file.slice(start, end);

      const fd = new FormData();
      fd.append("file", blob, item.fileName || "file");
      fd.append("upload_id", job.serverUploadId);
      fd.append("chunk_index", String(i));
      fd.append("total_chunks", String(totalChunks));
      fd.append("file_name", item.fileName || "file");
      if (item.replyTo) fd.append("reply_to", item.replyTo);
      if (item.caption) fd.append("body", item.caption);
      if (item.csrfToken) fd.append("csrfmiddlewaretoken", item.csrfToken);

      const res = await fetch(item.url, {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRFToken": item.csrfToken || "", "HX-Request": "true" },
        body: fd,
        signal: controller.signal,
      });
      if (!res || !res.ok) throw new Error("HTTP_" + (res ? res.status : "0"));

      state.sentBytes = end;
      notifyClients({ type: "UPLOAD_PROGRESS", upload: uploadSnapshot(state) });
    }

    state.status = "done";
    state.sentBytes = totalBytes;
    notifyClients({ type: "UPLOAD_DONE", upload: uploadSnapshot(state) });
  } catch (err) {
    const aborted = state.status === "canceled" || (err && (err.name === "AbortError" || String(err.message) === "CANCELED"));
    if (aborted) {
      state.status = "canceled";
      notifyClients({ type: "UPLOAD_CANCELED", upload: uploadSnapshot(state) });
    } else {
      state.status = "error";
      state.error = String((err && err.message) || "ERROR");
      notifyClients({ type: "UPLOAD_ERROR", upload: uploadSnapshot(state) });
    }
  } finally {
    UPLOAD_ABORTERS.delete(item.uploadId);
    setTimeout(() => UPLOAD_STATE.delete(item.uploadId), 60000);
  }
}

function cancelBackgroundUpload(uploadId) {
  const state = UPLOAD_STATE.get(uploadId);
  if (state) state.status = "canceled";
  const ab = UPLOAD_ABORTERS.get(uploadId);
  if (ab) { try { ab.abort(); } catch (e) {} }
  const idx = UPLOAD_QUEUE.findIndex((j) => j.item.uploadId === uploadId);
  if (idx >= 0) UPLOAD_QUEUE.splice(idx, 1);
  if (state) notifyClients({ type: "UPLOAD_CANCELED", upload: uploadSnapshot(state) });
}

function listBackgroundUploads(chatKey) {
  const out = [];
  for (const s of UPLOAD_STATE.values()) {
    if (!chatKey || s.chatKey === chatKey) out.push(uploadSnapshot(s));
  }
  return out;
}

self.addEventListener("message", (event) => {
  const data = event && event.data;

  if (data && data.type === "UPLOAD_START") {
    const list = Array.isArray(data.uploads) ? data.uploads : [data];
    for (const item of list) {
      if (item && item.uploadId && item.file) enqueueUpload(item);
    }
    return;
  }

  if (data && data.type === "UPLOAD_CANCEL") {
    cancelBackgroundUpload(data.uploadId);
    return;
  }

  if (data && data.type === "UPLOAD_QUERY") {
    const uploads = listBackgroundUploads(data.chatKey);
    if (event.ports && event.ports[0]) {
      event.ports[0].postMessage({ type: "UPLOAD_LIST", uploads: uploads });
    } else {
      notifyClients({ type: "UPLOAD_LIST", uploads: uploads });
    }
    return;
  }

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

// fetch با محدودیت زمانی و قابلیت لغو، تا respondWith هیچ‌وقت برای همیشه معلق نماند.
async function fetchWithTimeout(request, ms) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await fetch(request, { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function networkFirstNavigation(req) {
  let navPath = "?";
  try { navPath = new URL(req.url).pathname; } catch (e) {}
  const sensitive = isAuthSensitiveHtml(navPath);
  try {
    // مهم: درخواست را با redirect: "follow" بفرست. اگر درخواست ناوبری با
    // redirect: "manual" (پیش‌فرض) fetch شود و سرور 302 بدهد (مثلاً ریدایرکت به لاگین)،
    // پاسخ opaqueredirect در حالت standalone آی‌اواس به‌صورت صفحه‌ی سفید رندر می‌شود.
    const res = await fetchWithTimeout(
      new Request(req.url, {
        method: "GET",
        headers: req.headers,
        credentials: "include",
        redirect: "follow",
        cache: "no-store",
      }),
      NAV_TIMEOUT_MS
    );

    // اگر سرور ریدایرکت داده بود (مثلاً به /accounts/login/)، به‌جای برگرداندن مستقیمِ
    // پاسخِ ریدایرکت‌شده (که در iOS standalone سفید می‌شود)، یک ریدایرکتِ سمت‌کلاینت
    // به آدرس نهایی بده تا اپ درست جابه‌جا شود.
    if (res && res.redirected && res.url) {
      const dest = res.url;
      swLog("nav_redirect", { path: navPath, dest: dest });
      const html =
        '<!DOCTYPE html><html><head><meta charset="utf-8">' +
        "<script>location.replace(" + JSON.stringify(dest) + ");</script>" +
        "</head><body></body></html>";
      return new Response(html, {
        status: 200,
        headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
      });
    }

    // مهم: صفحات حساس (لاگین/ثبت‌نام/OTP) هرگز کش نمی‌شوند، چون csrf token داخلشان
    // فقط برای همان لحظه معتبر است.
    if (res && res.ok && !sensitive) {
      const cache = await caches.open(CACHE_NAME);
      // کش کردن نباید پاسخ‌دهی به صفحه را بلاک یا fail کند.
      cache.put(req, res.clone()).catch(() => {});
    }
    swLog("nav_net", {
      path: navPath,
      status: res ? res.status : 0,
      ok: !!(res && res.ok),
      sensitive: sensitive,
      stored: !!(res && res.ok && !sensitive),
    });
    return res;
  } catch (e) {
    const errStr = String((e && e.message) || e);

    // ۰) برای صفحات حساس هیچ‌وقت نسخه‌ی کش‌شده را نده.
    //    یک صفحه‌ی لاگینِ کهنه = csrf token منقضی = خطای 403 بعد از submit.
    //    بهتر است صفحه‌ی «در حال اتصال…» را ببیند که خودش retry می‌کند.
    if (sensitive) {
      swLog("nav_fallback", { path: navPath, kind: "offline_sensitive", sensitive: true, err: errStr });
      return offlineFallbackResponse();
    }

    // ۱) اگر همین آدرس قبلاً کش شده بود.
    const cached = await caches.match(req);
    if (cached) { swLog("nav_fallback", { path: navPath, kind: "cached", err: errStr }); return cached; }

    // ۲) ریشه‌ی سایت را بدون توجه به query/version برگردان.
    const root =
      (await caches.match("/", { ignoreSearch: true })) ||
      (await caches.match(versioned("/"))) ||
      (await caches.match("/"));
    if (root) { swLog("nav_fallback", { path: navPath, kind: "root", err: errStr }); return root; }

    // ۳) هرگز پاسخ خالی نده — صفحه‌ی fallback که خودش دوباره تلاش می‌کند.
    swLog("nav_fallback", { path: navPath, kind: "offline", err: errStr });
    return offlineFallbackResponse();
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

  // بکاپ/بازگردانی نباید از سرویس‌ورکر عبور کند (فایل ممکن است حجیم/زمان‌بر باشد).
  // بی دخالت سرویس‌ورکر، مرورگر خودش دانلود/آپلود را مدیریت می‌کند (بدون تایم‌اوت و کش).
  if (url.pathname.startsWith("/profile/manager/backup") || url.pathname.startsWith("/profile/manager/restore")) {
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
        icon: "/static/pwa-icon-192.png",
        badge: "/static/notification-badge-96.png",
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
