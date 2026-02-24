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
CMD ["bash","-lc","python manage.py migrate --noinput && python manage.py collectstatic --noinput && daphne -b 0.0.0.0 -p 8000 a_core.asgi:application"]
