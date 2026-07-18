# syntax=docker/dockerfile:1.6

FROM node:20-bookworm-slim AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ /src/frontend/
COPY templates/ /src/templates/
COPY static/ /src/static/
COPY a_core/ /src/a_core/
COPY a_home/ /src/a_home/
COPY a_rtchat/ /src/a_rtchat/
COPY a_users/ /src/a_users/
COPY manage.py /src/manage.py
RUN npx tailwindcss -i ./src/input.css -o ../static/css/tailwind.css --minify

FROM python:3.12-bookworm
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg \
    libreoffice \
    fonts-dejavu-core \
    fonts-liberation \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/
COPY --from=frontend-build /src/static/css/tailwind.css /app/static/css/tailwind.css

RUN mkdir -p /app/staticfiles /app/media

EXPOSE 8000

# Production ASGI server: Gunicorn + Uvicorn worker (same as the bare-metal server).
# NOTE: requirements.txt must include gunicorn and uvicorn[standard] (brings the
# `websockets` lib) or WebSocket connections will fail.
# Worker count is configurable via WEB_CONCURRENCY (defaults to 2).
CMD ["bash","-lc","python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn a_core.asgi:application -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:8000 --timeout 120 --graceful-timeout 30 --max-requests 2000 --max-requests-jitter 200"]
