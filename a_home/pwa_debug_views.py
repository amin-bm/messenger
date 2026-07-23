"""
Endpoint تشخیصی PWA (موقت).

رویدادهایی که سرویس‌ورکر (sw.js) و کلاینت (base.html) می‌فرستند را
می‌گیرد و با همان logger موجود (ws_debug) در logs/ws_debug.log ثبت می‌کند.

نحوه‌ی استفاده:
  - این فایل را در اپ a_home (کنار بقیه‌ی pwa_* view‌ha) یا a_rtchat بگذار.
  - در urls.py یک مسیر pwa/log به آن وصل کن (پایین توضیح داده شده).
پس از حل شدن مشکل، می‌توانی این endpoint و لاگ‌ها را حذف کنی.
"""
import json

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

# از همان logger موجود که در logs/ws_debug.log می‌نویسد استفاده می‌کنیم.
from a_rtchat.ws_logger import ws_log


@csrf_exempt
@require_POST
def pwa_log(request):
    # بدنه‌ی درخواست را بخوان (JSON یا متن خام).
    try:
        raw = (request.body or b"").decode("utf-8", "replace")
    except Exception:
        raw = ""

    try:
        payload = json.loads(raw) if raw else {}
        if not isinstance(payload, dict):
            payload = {"data": payload}
    except Exception:
        payload = {"raw": raw[:2000]}

    # کاربر (اگر لاگین باشد) و چند متاداتای مفید.
    try:
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        uname = getattr(user, "username", None) or "anon"
    except Exception:
        uname = "anon"

    ev = str(payload.get("ev", "?"))[:40]
    src = str(payload.get("src", "?"))[:12]
    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", ""))

    try:
        detail = json.dumps(payload, ensure_ascii=False)
    except Exception:
        detail = str(payload)

    # در همان ws_debug.log با پیشوند [PWA] ثبت می‌شود.
    try:
        ws_log.info("[PWA] src=%s ev=%s user=%s ip=%s %s", src, ev, uname, ip, detail[:3000])
    except Exception:
        pass

    # 204 تا نه کش شود و نه بدنه‌ای برگردد.
    return HttpResponse(status=204)
