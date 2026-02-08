import json

from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from a_core import settings as project_settings
from a_users.models import PushSubscription

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
    response["Cache-Control"] = "no-cache"
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

    response = HttpResponse(content, content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
def vapid_public_key(request):
    return JsonResponse({"publicKey": getattr(project_settings, "WEBPUSH_VAPID_PUBLIC_KEY", "") or ""})


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
