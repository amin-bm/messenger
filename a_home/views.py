import base64
import json

from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from a_core import settings as project_settings
from django.conf import settings
from a_users.models import PushSubscription


def _b64decode_any(value: str) -> bytes | None:
    raw = (value or "").strip()
    if not raw:
        return None
    raw = "".join(raw.split())
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    candidate = (raw + pad).encode("ascii", "ignore")
    try:
        if "-" in raw or "_" in raw:
            return base64.urlsafe_b64decode(candidate)
        return base64.b64decode(candidate, validate=True)
    except Exception:
        try:
            return base64.urlsafe_b64decode(candidate)
        except Exception:
            return None


def _normalize_vapid_public_key_for_browser(raw_value: str) -> str:
    raw = (raw_value or "").strip()
    if not raw:
        return ""

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import load_der_public_key, load_pem_public_key
    except Exception:
        return raw

    pub = None
    if "BEGIN PUBLIC KEY" in raw or "BEGIN EC PUBLIC KEY" in raw:
        try:
            pub = load_pem_public_key(raw.encode("utf-8"))
        except Exception:
            pub = None
    if pub is None:
        decoded = _b64decode_any(raw)
        if decoded:
            try:
                pub = load_der_public_key(decoded)
            except Exception:
                pub = None
        if pub is None and decoded and len(decoded) == 65 and decoded[0] == 4:
            return base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")

    if pub is None:
        return raw

    try:
        point = pub.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
    except Exception:
        return raw

    return base64.urlsafe_b64encode(point).decode("ascii").rstrip("=")


def home_view(request):
    context = {}
    return render(request, 'home.html', context)


@require_GET
def pwa_manifest(request):
    name = "manifest.webmanifest"
    try:
        with staticfiles_storage.open(name, "rb") as f:
            content = f.read()
    except FileNotFoundError:
        path = finders.find(name)
        if not path:
            raise Http404()
        with open(path, "rb") as f:
            content = f.read()

    response = HttpResponse(content, content_type="application/manifest+json")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    return response


@require_GET
def pwa_service_worker(request):
    name = "sw.js"
    try:
        with staticfiles_storage.open(name, "rb") as f:
            content = f.read()
    except FileNotFoundError:
        path = finders.find(name)
        if not path:
            raise Http404()
        with open(path, "rb") as f:
            content = f.read()

    # نسخه را داخل خود فایل تزریق کن تا بایت‌های پاسخ با هر دیپلوی عوض شود.
    version = str(getattr(settings, "APP_VERSION", "dev"))
    content = content.replace(b"__SW_APP_VERSION__", version.encode("utf-8"))

    response = HttpResponse(content, content_type="application/javascript")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
def pwa_version(request):
    response = JsonResponse(
        {
            "version": getattr(settings, "APP_VERSION", "dev"),
            "reset_required": bool(getattr(settings, "APP_RESET_REQUIRED", False)),
            "reset_message": getattr(settings, "APP_RESET_MESSAGE", ""),
        }
    )
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    return response


@require_GET
def vapid_public_key(request):
    raw = getattr(project_settings, "WEBPUSH_VAPID_PUBLIC_KEY", "") or ""
    return JsonResponse({"publicKey": _normalize_vapid_public_key_for_browser(raw)})


@login_required
@require_POST
def pwa_subscribe(request):
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except Exception:
        payload = {}

    endpoint = (payload or {}).get("endpoint") or ""
    keys = (payload or {}).get("keys") or {}

    if not endpoint:
        return JsonResponse({"ok": False}, status=400)

    obj, _ = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "keys": keys,
            "subscription": payload,
        },
    )
    return JsonResponse({"ok": True, "id": obj.id})


@login_required
@require_POST
def pwa_unsubscribe(request):
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except Exception:
        payload = {}

    endpoint = (payload or {}).get("endpoint") or ""
    if not endpoint:
        return JsonResponse({"ok": False}, status=400)

    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({"ok": True})
