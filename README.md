# Pesk Messenger (Django + Channels)

## پیش‌نیازها

- Ubuntu 22.04/24.04 (یا معادل)
- Python 3.11+ و venv
- Redis (برای Channels)
- Nginx (Reverse Proxy + سرو Static/Media)
- (اختیاری ولی پیشنهادی) PostgreSQL برای محیط Production

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

برای تغییر Tailwind:

```bash
cd frontend
npx tailwindcss -i ./src/input.css -o ../static/css/tailwind.css --minify
```

روی VPS فقط زمانی Node لازم است که بخواهید روی خود سرور Tailwind را build کنید.

## 5) تنظیم ENV روی سرور

پیشنهاد: یک فایل env بسازید و در systemd به سرویس معرفی کنید.

مسیر پیشنهادی:

`/etc/pesk-messenger/pesk-messenger.env`

نمونه محتوا:

```env
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=CHANGE_ME_TO_A_RANDOM_LONG_SECRET
DJANGO_ALLOWED_HOSTS=example.com,www.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://example.com,https://www.example.com

REDIS_HOST=127.0.0.1
REDIS_PORT=6379

OFFLINE_MODE=0

WEBPUSH_VAPID_PUBLIC_KEY=
WEBPUSH_VAPID_PRIVATE_KEY=
WEBPUSH_VAPID_CLAIMS_SUB=mailto:admin@example.com

SMSIR_API_KEY=
SMSIR_LINE_NUMBER=
SMSIR_TEMPLATE_ID=
```

نکته‌ها:

- اگر `OFFLINE_MODE=1` باشد، ارسال SMS و WebPush سمت سرور غیرفعال می‌شود.
- اگر `OFFLINE_MODE=0` باشد ولی کلیدهای WebPush یا SMS خالی باشند، برنامه بدون خطا اجرا می‌شود؛ فقط نوتیفیکیشن‌های مربوطه فعال نمی‌شوند.

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
    listen 80;
    server_name example.com www.example.com;

    client_max_body_size 25m;

    location /static/ {
        alias /opt/pesk-messenger/staticfiles/;
        expires 30d;
        add_header Cache-Control "public";
    }

    location /media/ {
        alias /opt/pesk-messenger/media/;
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
    }
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
