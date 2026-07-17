# Pesk Messenger (Django + Channels)

## پیش‌نیازها

- Ubuntu 22.04/24.04 (یا معادل)
- Python 3.11+ و venv
- Redis (برای Channels)
- Nginx (Reverse Proxy + سرو Static/Media)
- (اختیاری ولی پیشنهادی) PostgreSQL برای محیط Production
- (اختیاری) LibreOffice/soffice برای پیش‌نمایش فایل‌های Office به PDF

## اجرای پروژه با Docker (پیشنهادی برای سرور بدون اینترنت)

اگر می‌خواهید پروژه را روی یک سرور «بدون اینترنت» و «بدون نصب Python/Django/Node/Redis/PostgreSQL/Nginx روی خود سرور» اجرا کنید، از این روش استفاده کنید.

نکته‌ی مهم: تنها چیزی که روی سرور باید وجود داشته باشد Docker Engine و پلاگین/کامند Docker Compose است. (بدون Docker اصولاً اجرای کانتینر ممکن نیست.)

فایل‌های مربوط به Docker در این ریپو:

- [Dockerfile](./Dockerfile)
- [docker-compose.yml](./docker-compose.yml)
- [docker/nginx.conf](./docker/nginx.conf)

### اجرای محلی (برای تست)

```bash
docker compose up -d --build
docker compose logs -f web
```

بعد از بالا آمدن سرویس‌ها:

- آدرس برنامه: `http://localhost/`
- دیتابیس و فایل‌ها داخل Volumeهای Docker نگهداری می‌شوند (با `docker compose down` پاک نمی‌شوند مگر با `-v`)

### دیپلوی روی سرور بدون اینترنت (انتقال آفلاین)

ایده‌ی کلی این است که ایمیج‌ها را روی یک سیستم دارای اینترنت Build/Pull کنید، سپس به صورت فایل `.tar` منتقل کنید و روی سرور `docker load` بزنید.

1) روی سیستم Build (دارای اینترنت) داخل ریشه پروژه:

```bash
docker compose build
docker pull nginx:1.27-alpine postgres:16-alpine redis:7-alpine
docker save -o pesk-messenger-images.tar pesk-messenger-web:latest nginx:1.27-alpine postgres:16-alpine redis:7-alpine
```

2) فایل‌های زیر را به سرور منتقل کنید:

- `pesk-messenger-images.tar`
- `docker-compose.yml`
- پوشه `docker/` (برای `nginx.conf`)
- یک فایل `.env` یا `messenger.env` شامل تنظیمات (Secretها، Hostها، پسورد دیتابیس و ...)

3) روی سرور (بدون اینترنت):

```bash
docker load -i pesk-messenger-images.tar
docker compose up -d --no-build
docker compose ps
```

### آپدیت بعد از تغییر پروژه (بدون اینترنت)

هر بار که پروژه آپدیت شد، کافی است روی سیستم دارای اینترنت ایمیج `web` را دوباره Build کنید و دوباره فایل `.tar` بسازید و به سرور منتقل کنید:

روی سیستم Build:

```bash
docker compose build web
docker save -o pesk-messenger-images.tar pesk-messenger-web:latest nginx:1.27-alpine postgres:16-alpine redis:7-alpine
```

روی سرور:

```bash
docker load -i pesk-messenger-images.tar
docker compose up -d --no-build --force-recreate
docker compose logs -f web
```

نکته‌ها:

- برای اینکه کلاینت‌ها (PWA/Static) حتماً آپدیت را ببینند، مقدار `APP_VERSION` را در `.env` تغییر دهید (مثلاً شماره نسخه یا تاریخ).
- اگر HTTPS ندارید، حتماً این‌ها را در `.env` روی `0` بگذارید تا ریدایرکت/کوکی امن مشکل ایجاد نکند:
  - `DJANGO_SECURE_SSL_REDIRECT=0`
  - `DJANGO_SESSION_COOKIE_SECURE=0`
  - `DJANGO_CSRF_COOKIE_SECURE=0`
- برای WebSocketها در Nginx تنظیمات لازم داخل `docker/nginx.conf` انجام شده و مسیرهای `ws/` را پشتیبانی می‌کند.

## وابستگی‌های Python (خلاصه)

این پروژه بر پایه این ابزارها اجرا می‌شود:

- Django + Daphne (ASGI) برای اجرای وب‌اپ
- Channels + channels_redis برای WebSocket و حضور آنلاین
- redis برای اتصال لایه‌ی Channels به Redis
- psycopg2-binary برای اتصال PostgreSQL (در صورت فعال بودن)
- pillow برای پردازش تصاویر
- pywebpush برای Web Push Notification (در صورت تنظیم VAPID)
- django-allauth برای احراز هویت/ورود
- django-cleanup برای پاک‌کردن فایل‌های Media هنگام حذف مدل
- django-htmx برای تعاملات HTMX

## 1) آماده‌سازی سرور

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip nginx redis-server
sudo systemctl enable --now redis-server
```

اگر PostgreSQL می‌خواهید:

```bash
sudo apt install -y postgresql postgresql-contrib
```

اگر پیش‌نمایش فایل‌های Word/Excel به PDF را می‌خواهید (Office Preview):

```bash
sudo apt install -y libreoffice
```

## 2) ساخت یوزر و مسیر پروژه

```bash
sudo adduser --disabled-password --gecos "" pesk
sudo mkdir -p /opt/pesk-messenger
sudo chown -R pesk:pesk /opt/pesk-messenger
```

## 3) دریافت کد و نصب وابستگی‌ها

```bash
sudo -u pesk -H bash -lc '
cd /opt/pesk-messenger
git clone <REPO_URL> .
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
'
```

## 4) آماده‌سازی فایل‌های Static (Tailwind)

این پروژه در زمان اجرا به Node نیاز ندارد، چون خروجی Tailwind به‌صورت فایل آماده در static موجود است.

برای تغییر Tailwind روی لینوکس:

```bash
cd /opt/pesk-messenger/frontend
npm ci
npx tailwindcss -i ./src/input.css -o ../static/css/tailwind.css --minify
```

روی VPS فقط زمانی Node لازم است که بخواهید روی خود سرور Tailwind را build کنید.

نکته:

- این مرحله به pip نیاز ندارد؛ فقط Node.js (پیشنهادی: 18+ یا 20 LTS) و npm لازم است.
- pip فقط برای نصب وابستگی‌های Python پروژه استفاده می‌شود (مرحله 3). پیشنهاد: داخل venv از pip نسخه `>=23,<26` استفاده کنید:

```bash
python3 -m pip install --upgrade "pip>=23,<26"
```

## 5) تنظیم ENV روی سرور

پیشنهاد: یک فایل env بسازید و در systemd به سرویس معرفی کنید.

مسیر پیشنهادی:

`/etc/pesk-messenger/pesk-messenger.env`

نحوه‌ی لود شدن env در کد (طبق [settings.py](~/messenger/a_core/settings.py)):

- اگر متغیر `DJANGO_ENV_FILE` ست شده باشد، ابتدا همان فایل خوانده می‌شود.
- در غیر این صورت اولین فایل موجود از این لیست خوانده می‌شود:
  - `./messenger.env`
  - `./.env`
  - `/etc/messenger/messenger.env`
  - `/etc/pesk-messenger/pesk-messenger.env`
- اگر یک کلید از قبل در Environment پروسه ست شده باشد (مثلاً توسط systemd `EnvironmentFile=`)، فایل env آن مقدار را Override نمی‌کند.

نمونه محتوا:

```env
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=CHANGE_ME_TO_A_RANDOM_LONG_SECRET
DJANGO_ALLOWED_HOSTS=example.com,www.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://example.com,https://www.example.com

APP_VERSION=dev
APP_RESET_REQUIRED=0
APP_RESET_MESSAGE=

CHAT_PUBLIC_CHAT_VISIBLE_TO_ALL=0

REDIS_HOST=127.0.0.1
REDIS_PORT=6379

OFFLINE_MODE=0
IGNORE_NAVIGATOR_ONLINE=0

WEBPUSH_VAPID_PUBLIC_KEY=
WEBPUSH_VAPID_PRIVATE_KEY=
WEBPUSH_VAPID_CLAIMS_SUB=mailto:admin@example.com

SMSIR_API_KEY=
SMSIR_LINE_NUMBER=
SMSIR_TEMPLATE_ID=

# اگر PostgreSQL فعال است:
DATABASE_NAME=chat_db
DATABASE_USER=postgres
DATABASE_PASSWORD=CHANGE_ME
DATABASE_HOST=127.0.0.1
DATABASE_PORT=5432

# اگر PostgreSQL فعال نیست (SQLite):
# SQLITE_PATH=/opt/pesk-messenger/db.sqlite3
```

نکته‌های مهم امنیتی:

- فایل env را داخل ریپو Commit نکنید و آن را عمومی نکنید (به‌خصوص `DJANGO_SECRET_KEY`، کلیدهای VAPID و کلیدهای SMS).
- اگر به اشتباه Secretها را منتشر کرده‌اید، فوراً Rotate کنید و کلیدهای جدید بسازید.

### معنی و کاربرد گزینه‌های ENV

**هسته‌ی Django**

- `DJANGO_DEBUG` (پیش‌فرض: `1`): حالت توسعه/پروداکشن. در پروداکشن حتماً `0` باشد.
- `DJANGO_SECRET_KEY` (پیش‌فرض: مقدار ناامن داخلی): کلید امنیتی Django؛ در پروداکشن باید طولانی و تصادفی باشد.
- `DJANGO_ALLOWED_HOSTS` (پیش‌فرض: `localhost,127.0.0.1,*`): لیست Hostهایی که Django قبول می‌کند (CSV). در پروداکشن `*` نگذارید.
- `DJANGO_CSRF_TRUSTED_ORIGINS` (پیش‌فرض: خالی): لیست Originهای مجاز برای CSRF (CSV) و باید شامل scheme باشد (مثل `https://example.com`).
- `DJANGO_ENV_FILE` (اختیاری): مسیر فایل env سفارشی که قبل از بقیه تلاش می‌شود.

**نسخه/آپدیت PWA**

- `APP_VERSION` (پیش‌فرض: `dev-<mtime>`): برای Cache Busting روی فایل‌های Static و Service Worker استفاده می‌شود. در پروداکشن بهتر است شماره نسخه‌ی Deploy را بگذارید.
- `APP_RESET_REQUIRED` (پیش‌فرض: `0`): اگر `1` باشد، UI آپدیت به کاربر می‌گوید بروزرسانی نیاز به پاکسازی داده‌های سایت دارد.
- `APP_RESET_MESSAGE` (پیش‌فرض: خالی): پیام تکمیلی برای `APP_RESET_REQUIRED`.
- `IGNORE_NAVIGATOR_ONLINE` (پیش‌فرض: در Debug=1 مقدار 1، در Debug=0 مقدار 0): اگر `1` باشد، UI وضعیت آنلاین/آفلاین مرورگر را نادیده می‌گیرد.

**قابلیت‌های چت**

- `CHAT_PUBLIC_CHAT_VISIBLE_TO_ALL` (پیش‌فرض: `0`): اگر `1` باشد، اتاق `public_chat` برای همه‌ی کاربران قابل مشاهده/جستجو است؛ اگر `0` باشد فقط برای اعضا و مدیران.

**Channels / Redis**

- `REDIS_HOST` (پیش‌فرض: `localhost`): آدرس Redis برای Channels.
- `REDIS_PORT` (پیش‌فرض: `6379`): پورت Redis برای Channels.

**دیتابیس**

- اگر یکی از این‌ها ست شود، PostgreSQL فعال می‌شود: `POSTGRES_HOST/POSTGRES_DB/POSTGRES_USER/POSTGRES_PASSWORD` یا `DATABASE_HOST/DATABASE_NAME/DATABASE_USER/DATABASE_PASSWORD`.
- این پروژه برای مقداردهی به Database از `DATABASE_*` استفاده می‌کند و اگر نبود از `POSTGRES_*` می‌خواند.
- متغیرهای قابل تنظیم:
  - `DATABASE_NAME` یا `POSTGRES_DB` (پیش‌فرض: `chat_db`)
  - `DATABASE_USER` یا `POSTGRES_USER` (پیش‌فرض: `postgres`)
  - `DATABASE_PASSWORD` یا `POSTGRES_PASSWORD` (پیش‌فرض: خالی)
  - `DATABASE_HOST` یا `POSTGRES_HOST` (پیش‌فرض: `localhost`)
  - `DATABASE_PORT` یا `POSTGRES_PORT` (پیش‌فرض: `5432`)
- اگر PostgreSQL فعال نشود، SQLite استفاده می‌شود:
  - `SQLITE_PATH` (پیش‌فرض: `./db.sqlite3`)

**HTTPS / Security (فقط وقتی `DJANGO_DEBUG=0`)**

- `DJANGO_SECURE_SSL_REDIRECT` (پیش‌فرض: `1`): ریدایرکت HTTP به HTTPS (در پشت Nginx/Proxy باید `X-Forwarded-Proto` درست تنظیم شود).
- `DJANGO_SESSION_COOKIE_SECURE` (پیش‌فرض: `1`): ارسال Session Cookie فقط روی HTTPS.
- `DJANGO_CSRF_COOKIE_SECURE` (پیش‌فرض: `1`): ارسال CSRF Cookie فقط روی HTTPS.
- `DJANGO_SECURE_HSTS_SECONDS` (پیش‌فرض: `0`): فعال‌سازی HSTS با ثانیه‌ی دلخواه (مثلاً `31536000`).
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` (پیش‌فرض: `0`): اعمال HSTS روی زیردامنه‌ها.
- `DJANGO_SECURE_HSTS_PRELOAD` (پیش‌فرض: `0`): سازگار با HSTS preload (فقط اگر مطمئنید).

**حالت آفلاین و وابستگی‌های اینترنتی**

- `OFFLINE_MODE` (پیش‌فرض: `0`): اگر `1` باشد، ارسال SMS و WebPush سمت سرور غیرفعال می‌شود.
  - در حالت `OFFLINE_MODE=1`، ارسال OTP روی کنسول چاپ می‌شود.
  - اگر `OFFLINE_MODE=0` باشد ولی کلیدهای WebPush یا SMS خالی باشند، برنامه اجرا می‌شود؛ فقط قابلیت‌های مربوطه فعال نمی‌شوند.

**Web Push (PWA)**

- `WEBPUSH_VAPID_PUBLIC_KEY`: کلید عمومی VAPID (Base64) برای مرورگر.
- `WEBPUSH_VAPID_PRIVATE_KEY`: کلید خصوصی VAPID (برای سرور).
- `WEBPUSH_VAPID_CLAIMS_SUB` (پیش‌فرض: خالی): مقدار `sub` در VAPID Claims (مثلاً `mailto:admin@example.com`).

**SMS.ir**

- `SMSIR_API_KEY`: کلید API برای SMS.ir.
- `SMSIR_LINE_NUMBER`: شماره‌ی خط برای ارسال Bulk.
- `SMSIR_TEMPLATE_ID`: شناسه‌ی Template برای OTP/Verify.

### تنظیم دیتابیس

این پروژه به‌صورت پیش‌فرض از SQLite استفاده می‌کند. برای Production پیشنهاد می‌شود PostgreSQL تنظیم شود.

برای فعال شدن PostgreSQL کافی است یکی از `POSTGRES_HOST` یا `POSTGRES_DB` ست شود:

```env
POSTGRES_DB=chat_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=CHANGE_ME
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

اگر PostgreSQL تنظیم نشود، SQLite با `SQLITE_PATH` (اختیاری) استفاده می‌شود:

```env
SQLITE_PATH=/opt/pesk-messenger/db.sqlite3
```

## 6) migrate / collectstatic / superuser

```bash
sudo -u pesk -H bash -lc '
cd /opt/pesk-messenger
source venv/bin/activate
export DJANGO_SETTINGS_MODULE=a_core.settings
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
'
```

## 7) ساخت Chat Group های لازم

بعد از ورود به Admin:

- در Chat Groups دو Group با `Group name` بسازید:
  - `public_chat`
  - `online-status`

## 8) ساخت سرویس systemd (Daphne)

فایل زیر را بسازید:

`/etc/systemd/system/pesk-messenger.service`

```ini
[Unit]
Description=Pesk Messenger (Django ASGI via Daphne)
After=network.target redis-server.service
Requires=redis-server.service

[Service]
User=pesk
Group=pesk
WorkingDirectory=/opt/pesk-messenger
EnvironmentFile=/etc/pesk-messenger/pesk-messenger.env
ExecStart=/opt/pesk-messenger/venv/bin/daphne -b 127.0.0.1 -p 8001 a_core.asgi:application
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

سپس:

```bash
sudo mkdir -p /etc/pesk-messenger
sudo nano /etc/pesk-messenger/pesk-messenger.env
sudo nano /etc/systemd/system/pesk-messenger.service
sudo systemctl daemon-reload
sudo systemctl enable --now pesk-messenger
sudo systemctl status pesk-messenger --no-pager
```

لاگ‌ها:

```bash
sudo journalctl -u pesk-messenger -f
```

## 9) تنظیم Nginx (Reverse Proxy + WebSocket)

نمونه فایل:

`/etc/nginx/sites-available/pesk-messenger`

```nginx
server {
    server_name example.com www.example.com;

    
    client_max_body_size 20g;

    location /static/ { alias /opt/messenger/staticfiles/; }
    location /media/  { alias /opt/messenger/media/; }

    # Internal-only location: reachable ONLY via X-Accel-Redirect.
    # nginx serves backup files directly (no python/daphne involvement).
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

        # Longer timeouts for slow/large operations (backup upload/restore).
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        # Stream uploads straight to the backend instead of buffering the whole body.
        proxy_request_buffering off;
    }

    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/example.com-0001/fullchain.pem; # managed by Certbot
    ssl_certificate_key /etc/letsencrypt/live/example.com-0001/privkey.pem; # managed by Certbot
    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # managed by Certbot

}
server {
    if ($host = example.com) {
        return 301 https://$host$request_uri;
    } # managed by Certbot


    listen 80;
    server_name example.com;
    return 404; # managed by Certbot

}
```

فعال‌سازی:

```bash
sudo ln -s /etc/nginx/sites-available/pesk-messenger /etc/nginx/sites-enabled/pesk-messenger
sudo nginx -t
sudo systemctl reload nginx
```

## 10) فعال‌سازی HTTPS (پیشنهادی)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d example.com -d www.example.com
```

در صورت استفاده از HTTPS، این env ها را هم می‌توانید فعال کنید:

```env
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SESSION_COOKIE_SECURE=1
DJANGO_CSRF_COOKIE_SECURE=1
DJANGO_SECURE_HSTS_SECONDS=31536000
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1
DJANGO_SECURE_HSTS_PRELOAD=1
```

## تغییرات لازم روی کد بعد از Deploy

نیازی به تغییر دستی کد بعد از Deploy نیست؛ تنظیمات Production و موارد امنیتی از طریق ENV کنترل می‌شوند:

- `DJANGO_DEBUG` برای خاموش کردن Debug
- `DJANGO_SECRET_KEY` برای کلید امن
- `DJANGO_ALLOWED_HOSTS` و `DJANGO_CSRF_TRUSTED_ORIGINS` برای دامنه
- `REDIS_HOST` و `REDIS_PORT` برای Channels
- (اختیاری) متغیرهای PostgreSQL
- `OFFLINE_MODE` برای کنترل وابستگی‌های اینترنتی (SMS/WebPush)

## عیب‌یابی سریع

چک تنظیمات:

```bash
sudo -u pesk -H bash -lc '
cd /opt/pesk-messenger
source venv/bin/activate
python manage.py check --deploy
'
```

مشاهده وضعیت سرویس:

```bash
sudo systemctl status pesk-messenger --no-pager
sudo journalctl -u pesk-messenger -n 200 --no-pager
```
