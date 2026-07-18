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

مقادیرِ واقعیِ این سرور (کلیدهای محرمانه **عمداً حذف شده‌اند**):

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

## 9) عیب‌یابی و پایش

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

## اجرا با Docker (نصب کامل روی سرورِ خودتان)

این روش کلِ پروژه (وب‌اپ + PostgreSQL + Redis + Nginx) را به‌صورتِ کانتینری روی سرورتان بالا می‌آورد، **بدونِ نصبِ دستیِ Python / Node / PostgreSQL / Redis / Nginx** روی خودِ سرور.

### پیش‌نیاز
فقط این دو مورد روی سرور لازم است:

- **Docker Engine**
- **Docker Compose plugin** (کامندِ `docker compose`)

```bash
docker --version
docker compose version
```

### فایل‌های دخیل در Docker
- `Dockerfile` (ریشه‌ی ریپو) — ساختِ ایمیجِ وب: buildِ فرانت (Tailwind با Node) + اجرای **gunicorn + uvicorn worker**.
- `docker-compose.yml` (ریشه‌ی ریپو) — تعریفِ ۴ سرویس: `web` ، `nginx` ، `redis` ، `db` (PostgreSQL 14).
- `docker/nginx.conf` — کانفیگِ Nginxِ داخلِ کانتینر (پورت ۸۰، پشتیبانیِ WebSocket، آپلودِ تا `5g`).

### گامِ مشترک: ساخت فایل `.env`
کنارِ `docker-compose.yml` یک فایلِ `.env` بساز (Compose خودکار می‌خواندش). حداقل این‌ها را تنظیم کن:

```env
# --- Django ---
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=یک-کلیدِ-تصادفیِ-بلند-اینجا
DJANGO_ALLOWED_HOSTS=chat.your-domain.ir,www.chat.your-domain.ir
DJANGO_CSRF_TRUSTED_ORIGINS=https://chat.your-domain.ir,https://www.chat.your-domain.ir

# اگر هنوز HTTPS نداری، این‌ها را 0 بگذار تا ریدایرکت/کوکیِ امن مشکل نسازد
DJANGO_SECURE_SSL_REDIRECT=0
DJANGO_SESSION_COOKIE_SECURE=0
DJANGO_CSRF_COOKIE_SECURE=0

APP_VERSION=1.0.0

# --- PostgreSQL (داخلِ کانتینرِ db) ---
POSTGRES_DB=chat_db
POSTGRES_USER=chat_user
POSTGRES_PASSWORD=یک-پسوردِ-قوی

# --- Redis (داخلِ کانتینرِ redis) ---
REDIS_HOST=redis
REDIS_PORT=6379

# --- تعدادِ ورکرِ وب ---
WEB_CONCURRENCY=2

# --- حالتِ آفلاین: اگر سرورت به اینترنت وصل نیست 1 بگذار (SMS/WebPush غیرفعال) ---
OFFLINE_MODE=0

# --- Web Push (اختیاری، فقط وقتی OFFLINE_MODE=0) ---
WEBPUSH_VAPID_PUBLIC_KEY=
WEBPUSH_VAPID_PRIVATE_KEY=
WEBPUSH_VAPID_CLAIMS_SUB=mailto:admin@your-domain.ir

# --- SMS.ir (اختیاری، فقط وقتی OFFLINE_MODE=0) ---
SMSIR_API_KEY=
SMSIR_LINE_NUMBER=
SMSIR_TEMPLATE_ID=
```

> تولیدِ SECRET_KEYِ تصادفی:
> ```bash
> python3 -c "import secrets; print(secrets.token_urlsafe(64))"
> ```

نکته درباره‌ی دیتابیس در Docker: نیازی به نصب/ساختِ دستیِ Postgres نیست؛ کانتینرِ `db` خودش با همان `POSTGRES_*` بالا و مقداردهی می‌شود و وب‌اپ به `POSTGRES_HOST=db` وصل می‌شود (پیش‌فرضِ compose). دستورهای `migrate` و `collectstatic` هم خودکار موقعِ استارتِ کانتینرِ `web` اجرا می‌شوند.

---

### روش A — سروری که اینترنت دارد

۱) گرفتنِ کد:
```bash
git clone <REPO_URL> pesk-messenger
cd pesk-messenger
```

۲) ساختِ فایلِ `.env` (طبقِ بالا):
```bash
nano .env
```

۳) بالا آوردنِ سرویس‌ها (ایمیج‌ها روی همین سرور Build/Pull می‌شوند):
```bash
docker compose up -d --build
docker compose ps
docker compose logs -f web
```

۴) ساختِ کاربرِ ادمین:
```bash
docker compose exec web python manage.py createsuperuser
```

۵) ورود به `http://SERVER_IP/admin/` و ساختِ دو Chat Group با نام‌های `public_chat` و `online-status`.

آدرسِ برنامه: `http://SERVER_IP/` (پورتِ ۸۰). برای دامنه/HTTPS بخشِ پایین را ببین.

**آپدیت بعد از تغییرِ کد:**
```bash
git pull
docker compose up -d --build
docker compose logs -f web
```

---

### روش B — سروری که اینترنت ندارد (انتقالِ آفلاین)

ایده: ایمیج‌ها را روی یک سیستمِ **دارای اینترنت** بساز/دانلود کن، به `.tar` تبدیل کن، به سرور منتقل کن و آنجا `docker load` بزن.

**۱) روی سیستمِ دارای اینترنت** (داخلِ ریشه‌ی ریپو):
```bash
# ساختِ ایمیجِ وب
docker compose build

# دانلودِ ایمیج‌های پایه
docker pull nginx:1.27-alpine
docker pull redis:7-alpine
docker pull postgres:14-alpine

# ذخیره‌ی همه در یک فایلِ tar
docker save -o pesk-messenger-images.tar \
  pesk-messenger-web:latest \
  nginx:1.27-alpine \
  redis:7-alpine \
  postgres:14-alpine
```

**۲) انتقالِ این موارد به سرور** (با flash / scp / …):
- `pesk-messenger-images.tar`
- `docker-compose.yml`
- پوشه‌ی `docker/` (شاملِ `nginx.conf`)
- فایلِ `.env` (که خودت پر کرده‌ای)

**۳) روی سرورِ بدونِ اینترنت:**
```bash
# بارگذاریِ ایمیج‌ها از فایلِ tar
docker load -i pesk-messenger-images.tar

# بالا آوردن بدونِ build و بدونِ pull
docker compose up -d --no-build
docker compose ps
```

**۴) ساختِ ادمین و گروه‌ها** (مثلِ روش A):
```bash
docker compose exec web python manage.py createsuperuser
```
سپس در `/admin/` گروه‌های `public_chat` و `online-status` را بساز.

**آپدیتِ آفلاین بعد از تغییرِ کد:** روی سیستمِ اینترنت‌دار دوباره `docker compose build` و `docker save`، فایلِ tar را منتقل کن، روی سرور:
```bash
docker load -i pesk-messenger-images.tar
docker compose up -d --no-build --force-recreate
docker compose logs -f web
```

> برای اینکه مطمئن شوی کلاینت‌ها (PWA) آپدیت را می‌گیرند، مقدارِ `APP_VERSION` را در `.env` هر بار عوض کن.

---

### دامنه و HTTPS در حالتِ Docker
کانتینرِ `nginx` روی پورتِ ۸۰ گوش می‌دهد. برای دامنه و HTTPS دو راه داری:
1. یک reverse proxy / ترمینیتِ SSL جلوترش بگذار (مثلاً Nginx یا Caddy روی هاست، یا Cloudflare) که به `http://127.0.0.1:80` پاس بدهد.
2. یا خودت `docker/nginx.conf` را برای TLS و certbot توسعه بده و پورتِ ۴۴۳ را در `docker-compose.yml` باز کن.

وقتی HTTPS فعال شد، در `.env` این‌ها را `1` کن:
```env
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SESSION_COOKIE_SECURE=1
DJANGO_CSRF_COOKIE_SECURE=1
```

### دستورهای مفیدِ Docker
```bash
docker compose ps                 # وضعیت سرویس‌ها
docker compose logs -f web        # لاگِ زنده‌ی وب
docker compose exec web bash      # ورود به کانتینرِ وب
docker compose restart web        # ری‌استارتِ وب
docker compose down               # توقف (داده‌ها در volume می‌مانند)
docker compose down -v            # توقف + پاک‌کردنِ کاملِ داده‌ها (خطرناک)
```
داده‌های ماندگار در volumeها: `pgdata` (دیتابیس)، `redisdata`، `staticfiles`، `media`.
