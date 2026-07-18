# Pesk Messenger (Django 5 + Channels)

مسنجرِ real-time مبتنی بر **Django 5 + Django Channels** با WebSocket و حضورِ آنلاین.
این README وضعیتِ **واقعیِ پروداکشن** روی سرور را مستند می‌کند (نه نمونه/مثال).

> استکِ واقعیِ در حال اجرا:
> **Gunicorn + Uvicorn worker (ASGI)** → **Django 5** → **PostgreSQL 14** + **Redis** پشتِ **Nginx**.

داخل کدها، هرجا your-domain.ir بود با دامنه ی خودتون جایگزین کنید

---

## فهرست سرویس‌ها و فایل‌های تنظیمیِ واقعی

| مورد | مقدار / مسیرِ واقعی |
|---|---|
| مسیر پروژه | `/opt/messenger` |
| کاربرِ اجرا | `pesk` |
| venv (Python 3.11) | `/opt/messenger/venv` |
| ماژول ASGI | `a_core.asgi:application` |
| ماژول settings | `a_core.settings` |
| فایل ENV | `/etc/messenger/messenger.env` |
| سرویس اپ | `/etc/systemd/system/messenger.service` |
| Drop-in سرویس | `/etc/systemd/system/messenger.service.d/override.conf` |
| اجرا روی | `127.0.0.1:8001` |
| دامنه | `chat.your-domain.ir` |
| Nginx (site) | `/etc/nginx/...` (محتوای واقعی در بخش Nginx) |
| PostgreSQL | کلاستر `14 main`، پورت `5432` |
| DB / User | `messenger_db` / `messenger_user` |
| کانفیگ PostgreSQL | `/etc/postgresql/14/main/postgresql.conf` |
| سرویس PostgreSQL | `postgresql@14-main.service` |
| Watchdog دیتابیس | `/etc/systemd/system/pg-watchdog.service` + `pg-watchdog.timer` |
| Redis | `127.0.0.1:6379` (Channels layer + Django cache روی DB `1`) |

---

## پیش‌نیازها

- Ubuntu / Debian (این سرور: cPanel روی Ubuntu)
- Python 3.11+ و venv
- PostgreSQL 14
- Redis (برای Channels و Cache)
- Nginx (Reverse Proxy + سرو Static/Media + WebSocket)
- (اختیاری) LibreOffice/soffice برای پیش‌نمایش فایل‌های Office به PDF

نصب پیش‌نیازهای سیستمی:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip nginx redis-server postgresql postgresql-contrib
sudo systemctl enable --now redis-server
# اختیاری (Office Preview):
sudo apt install -y libreoffice
```

---

## ۱) کاربر و مسیر پروژه

```bash
sudo useradd -r -m -d /opt/messenger -s /bin/bash pesk   # اگر از قبل نیست
sudo mkdir -p /opt/messenger
sudo chown -R pesk:pesk /opt/messenger
```

## ۲) دریافت کد و venv و نصب وابستگی‌ها

```bash
sudo -u pesk -H bash -lc '
cd /opt/messenger
git clone <REPO_URL> .
python3 -m venv venv
source venv/bin/activate
pip install --upgrade "pip>=23,<26"
pip install -r requirements.txt
'
```

### نصبِ سرورِ ASGIِ پروداکشن (مهم)

اپ با **Gunicorn + Uvicorn worker** اجرا می‌شود، نه daphne. حتماً uvicorn را با اکسترای `standard` نصب کن تا کتابخانه‌ی **WebSocket** (پکیج `websockets`) و `uvloop`/`httptools` نصب شوند؛ در غیر این‌صورت HTTP کار می‌کند ولی WebSocket وصل نمی‌شود و خطای «در حال اتصال مجدد» می‌گیری:

```bash
sudo -u pesk /opt/messenger/venv/bin/pip install gunicorn "uvicorn[standard]"
```

تأیید نصبِ WebSocket:

```bash
/opt/messenger/venv/bin/python -c "import websockets; print('websockets OK')"
```

---

## ۳) PostgreSQL

### ساخت دیتابیس و کاربر

```bash
sudo -u postgres psql <<'SQL'
CREATE USER messenger_user WITH PASSWORD 'CHANGE_ME';
CREATE DATABASE messenger_db OWNER messenger_user;
GRANT ALL PRIVILEGES ON DATABASE messenger_db TO messenger_user;
SQL
```

### فعال‌سازیِ خودکارِ کلاستر هنگام بوت

این قدم لازم است؛ در غیر این‌صورت اگر PostgreSQL بیفتد خودش بالا نمی‌آید و کلِ اپ می‌خوابد:

```bash
sudo systemctl enable postgresql@14-main
sudo systemctl start postgresql@14-main
sudo pg_lsclusters   # باید online باشد
```

### تنظیماتِ واقعیِ `postgresql.conf`

فایل: `/etc/postgresql/14/main/postgresql.conf` — این مقادیر برای سروری با ~۲GB RAM و ~۵۰ کاربر تنظیم شده‌اند:

```conf
max_connections = 60
shared_buffers = 256MB
work_mem = 4MB
maintenance_work_mem = 64MB
effective_cache_size = 768MB
```

> نکته: `max_connections = 60` عمداً کمی بالاتر است چون هر کاربر ممکن است هم‌زمان با موبایل و دسکتاپ (دو دیوایس) وصل شود.

بعد از تغییر:

```bash
sudo systemctl restart postgresql@14-main
```

### Watchdog دیتابیس (خودترمیمی)

اگر PostgreSQL به هر دلیل بیفتد، این تایمر هر ۶۰ ثانیه چک می‌کند و در صورت down بودن، دوباره بالا می‌آورد.

فایل `/etc/systemd/system/pg-watchdog.service`:

```ini
[Unit]
Description=PostgreSQL watchdog - start cluster if down

[Service]
Type=oneshot
ExecStart=/bin/bash -c '/usr/bin/pg_isready -h 127.0.0.1 -p 5432 -q || /bin/systemctl start postgresql@14-main'
```

فایل `/etc/systemd/system/pg-watchdog.timer`:

```ini
[Unit]
Description=Run PostgreSQL watchdog periodically

[Timer]
OnBootSec=60
OnUnitActiveSec=60

[Install]
WantedBy=timers.target
```

فعال‌سازی و بررسی:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pg-watchdog.timer
sudo systemctl list-timers --all | grep pg-watchdog
sudo systemctl status pg-watchdog.service --no-pager
```

---

## ۴) Redis

Redis هم برای **Channels layer** (WebSocket/حضور آنلاین) و هم برای **Django cache** (روی DB شماره‌ی `1`، مخصوصِ throttle/debounce) استفاده می‌شود.

```bash
sudo systemctl enable --now redis-server
redis-cli ping   # باید PONG بدهد
```

---

## ۵) فایل ENV

مسیرِ واقعی: `/etc/messenger/messenger.env`

ترتیبِ بارگذاریِ env در کد (طبق `a_core/settings.py`):
1. اگر `DJANGO_ENV_FILE` ست شده باشد، همان اول خوانده می‌شود.
2. وگرنه اولین فایلِ موجود از: `./messenger.env` → `./.env` → `/etc/messenger/messenger.env` → `/etc/pesk-messenger/pesk-messenger.env`.
3. اگر کلیدی از قبل در Environment پروسه ست شده باشد (مثلاً از طریق systemd `EnvironmentFile=`)، فایلِ env آن را Override نمی‌کند.

مقادیرِ واقعیِ این سرور (کلیدهای محرمانه **عمداً حذف شده‌اند** — در README نگذار، فقط روی سرور باشند):

```env
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=***REDACTED***
DJANGO_ALLOWED_HOSTS=chat.your-domain.ir,www.chat.your-domain.ir
DJANGO_CSRF_TRUSTED_ORIGINS=https://chat.your-domain.ir,https://www.chat.your-domain.ir

APP_VERSION=2.8.2

# حالت آفلاین: SMS و WebPush سمت سرور غیرفعال می‌شوند (این سرور آفلاین است)
OFFLINE_MODE=1
IGNORE_NAVIGATOR_ONLINE=0

REDIS_HOST=127.0.0.1
REDIS_PORT=6379

DATABASE_NAME=messenger_db
DATABASE_USER=messenger_user
DATABASE_PASSWORD=***REDACTED***
DATABASE_HOST=localhost
DATABASE_PORT=5432

WEBPUSH_VAPID_PUBLIC_KEY=<public-key>
WEBPUSH_VAPID_PRIVATE_KEY=***REDACTED***
WEBPUSH_VAPID_CLAIMS_SUB=mailto:admin@chat.your-domain.ir

SMSIR_API_KEY=***REDACTED***
SMSIR_LINE_NUMBER=<line-number>
SMSIR_TEMPLATE_ID=<template-id>
```

> ⚠️ امنیت: `messenger.env` را هرگز در ریپو Commit نکن. مقادیرِ محرمانه (`DJANGO_SECRET_KEY`، `DATABASE_PASSWORD`، `WEBPUSH_VAPID_PRIVATE_KEY`، `SMSIR_API_KEY`) نباید داخل README یا git بروند. اگر قبلاً لو رفته‌اند، **Rotate کن** (بخش امنیت).

---

## ۶) سرویس systemd (Gunicorn + Uvicorn worker)

فایلِ واقعی `/etc/systemd/system/messenger.service`:

```ini
[Unit]
Description=Messenger (Django ASGI via Gunicorn/Uvicorn)
After=network.target postgresql@14-main.service redis-server.service
Wants=postgresql@14-main.service redis-server.service

[Service]
User=pesk
WorkingDirectory=/opt/messenger
EnvironmentFile=/etc/messenger/messenger.env
ExecStart=/opt/messenger/venv/bin/gunicorn a_core.asgi:application \
    -k uvicorn.workers.UvicornWorker \
    -w 2 \
    -b 127.0.0.1:8001 \
    --timeout 120 \
    --graceful-timeout 30 \
    --max-requests 2000 \
    --max-requests-jitter 200
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> چرا `-w 2`؟ معماریِ قبلی تک‌پروسه‌ی daphne بود که زیرِ بارِ همزمان (چند کاربر با هم آنلاین) خفه می‌شد. دو ورکرِ Uvicorn بار را پخش می‌کنند. `--max-requests` هم ورکرها را دوره‌ای بازیافت می‌کند تا نشتِ حافظه جمع نشود.

Drop-in واقعی `/etc/systemd/system/messenger.service.d/override.conf` (برای مسیرِ داخلیِ بکاپ در Nginx):

```ini
[Service]
Environment=BACKUP_XACCEL_PREFIX=/protected-backups
```

فعال‌سازی و بررسی:

```bash
sudo mkdir -p /etc/messenger
sudo systemctl daemon-reload
sudo systemctl enable --now messenger
sudo systemctl status messenger --no-pager
sudo journalctl -u messenger -f
```

> پیام `ASGI 'lifespan' protocol appears unsupported` در لاگ **طبیعی** است (اپِ ASGIِ جنگو lifespan را پیاده نمی‌کند) و خطا نیست.

---

## ۷) Nginx (Reverse Proxy + WebSocket)

محتوای واقعیِ کانفیگِ سایت (proxy به `127.0.0.1:8001`، پشتیبانیِ WebSocket روی `/ws/`، و مسیرِ داخلیِ بکاپ). SSL/HTTPS در این سرور توسط لایه‌ی cPanel/Certbot مدیریت می‌شود:

```nginx
server {
    listen 80;
    server_name chat.your-domain.ir www.chat.your-domain.ir;

    # اجازه‌ی آپلود فایل بزرگ (برای بازگردانی بکاپ حجیم)
    client_max_body_size 5g;

    location /static/ {
        alias /opt/messenger/staticfiles/;
        expires 30d;
        add_header Cache-Control "public";
    }

    location /media/ {
        alias /opt/messenger/media/;
    }

    # مسیر داخلی: فقط از طریق X-Accel-Redirect قابل دسترسی است.
    location /protected-backups/ {
        internal;
        alias /opt/messenger/backups/;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # مهلت بیشتر برای عملیات طولانی (آپلود/بازگردانی بکاپ حجیم)
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        # آپلود را مستقیم استریم کن (کم‌مصرف برای دیسک/رم)
        proxy_request_buffering off;
    }
}
```

بعد از تغییر:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

> توجه: چون WebSocket از `AllowedHostsOriginValidator` در `a_core/asgi.py` عبور می‌کند، دامنه باید در `DJANGO_ALLOWED_HOSTS` باشد وگرنه اتصالِ WS رد می‌شود.

---

## ۸) migrate / collectstatic / superuser / گروه‌های چت

```bash
sudo -u pesk -H bash -lc '
cd /opt/messenger
source venv/bin/activate
export DJANGO_SETTINGS_MODULE=a_core.settings
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
'
```

سپس در Admin دو Chat Group با این `Group name`ها بساز (لازم برای حضور آنلاین و چتِ عمومی):

- `public_chat`
- `online-status`

---

## ۹) تغییراتِ کدِ پایدارسازی (اعمال‌شده)

این تغییرات برای رفعِ خفگیِ سرور زیرِ بارِ همزمان اعمال شده‌اند:

### `a_core/settings.py`

بلوکِ Cache (روی Redis DB `1`) — لازم برای throttle/debounce:

```python
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://{host}:{port}/1".format(
            host=(os.getenv("REDIS_HOST", "localhost").strip() or "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
        ),
    }
}
```

نگه‌داشتنِ اتصالِ دیتابیس (کاهش فشارِ باز/بسته‌شدنِ مداوم) — در بلوکِ `DATABASES["default"]`:

```python
"CONN_MAX_AGE": 60,
```

### `a_rtchat/consumers.py`

```python
from django.core.cache import cache
```

- `_touch_last_seen`: throttleِ ۳۰ ثانیه‌ای برای هر کاربر تا نوشتنِ مکرر در دیتابیس کم شود:

```python
if not cache.add(f"last_seen_touch:{user.id}", 1, timeout=30):
    return
```

- `OnlineStatusConsumer.online_status`: debounceِ ۲ ثانیه‌ای برای جلوگیری از طوفانِ N² هنگام آنلاین‌شدنِ هم‌زمانِ چند نفر:

```python
if not cache.add("online_status_broadcast_lock", 1, timeout=2):
    return
```

- ارسالِ push فقط وقتی حالتِ آفلاین خاموش است:

```python
if not bool(getattr(settings, "OFFLINE_MODE", False)):
    transaction.on_commit(lambda: _send_push_notifications_for_message(message.id))
```

- رفعِ باگِ فراخوانیِ `log_error` در بخشِ reaction (افزودنِ آرگومانِ کاربر):

```python
log_error('ChatroomConsumer', self.user, f'reaction create failed msg={message.id}')
```

### `templates/base.html`  (مسیرِ واقعی: `/opt/messenger/templates/base.html`)

تلاش برای اتصالِ مجددِ WebSocket از `setInterval(5s)` به **backoff نمایی + jitter** تغییر کرد تا هجومِ هم‌زمانِ همه‌ی کلاینت‌ها (reconnect storm) پیش نیاید:

```javascript
const base = Math.min(30000, 3000 * Math.pow(2, reconnectAttempts));
const delay = base * (0.6 + Math.random() * 0.8);
```

---

## ۱۰) عیب‌یابی و پایش

بررسیِ سلامتِ اپ (با هدرِ Host واقعی؛ بدونِ آن جنگو ۴۰۰ می‌دهد چون `127.0.0.1` در ALLOWED_HOSTS نیست):

```bash
curl -I -H "Host: chat.your-domain.ir" http://127.0.0.1:8001/
```

تعدادِ اتصال‌های دیتابیس (باید زیرِ `60` بماند):

```bash
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity;"
```

مصرفِ حافظه‌ی پروسه‌ها:

```bash
ps -eo rss,pid,user,args --sort=-rss | head -20
```

لاگِ زنده (موقع اتصالِ کاربر باید `"WebSocket ..." [accepted]` و `connection open` ببینی):

```bash
sudo journalctl -u messenger -f
```

چکِ deploy جنگو:

```bash
sudo -u pesk -H bash -lc 'cd /opt/messenger && source venv/bin/activate && python manage.py check --deploy'
```

---

## ۱۱) امنیت — چرخاندنِ کلیدهای محرمانه

اگر این مقادیر جایی لو رفته‌اند، همه را عوض کن:

- `DJANGO_SECRET_KEY`
- `DATABASE_PASSWORD` (هم در PostgreSQL با `ALTER USER messenger_user WITH PASSWORD '...';` و هم در `messenger.env`، سپس `sudo systemctl restart messenger`)
- `WEBPUSH_VAPID_PRIVATE_KEY` (و کلیدِ عمومیِ متناظر)
- `SMSIR_API_KEY`

---

## (اختیاری) اجرا با Docker — برای run از روی GitHub

> این روشِ جاریِ پروداکشنِ این سرور نیست (سرور bare-metal با systemd بالا آمده). اما فایل‌های Docker حالا با استکِ جدید هم‌سان شده‌اند تا هر کس از GitHub کلون کرد بتواند مستقیم run کند.

تغییراتِ اعمال‌شده روی فایل‌های Docker:

- **`Dockerfile`**: دستورِ اجرا از `daphne` به **Gunicorn + Uvicorn worker** تغییر کرد (همانِ پروداکشن)؛ تعدادِ ورکر از `WEB_CONCURRENCY` (پیش‌فرض 2).
- **`docker/nginx.conf`**: `client_max_body_size` از `50m` به `5g`، افزودنِ `proxy_read/send_timeout 3600s` و `proxy_request_buffering off`، و تایم‌اوتِ طولانی‌تر روی `/ws/`.
- **`docker-compose.yml`**: افزودنِ `WEB_CONCURRENCY` و passthroughِ کلیدهای WebPush/SMS.
- **`requirements.txt`**: افزودنِ `gunicorn` و `uvicorn[standard]` (پکیجِ `websockets` را می‌آورد).

اجرای محلی (تست):

```bash
docker compose up -d --build
docker compose logs -f web
```

دیپلویِ آفلاین (انتقال با `.tar`): روی سیستمِ دارای اینترنت `docker compose build` + `docker save`، سپس روی سرور `docker load` + `docker compose up -d --no-build`.
