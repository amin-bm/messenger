"""ویوی سفارشی برای خطای CSRF.

دو کار می‌کند:
1) همه‌ی جزئیات لازم برای دیباگ را در همان فایل logs/ws_debug.log می‌نویسد.
2) اگر خطا روی صفحه‌ی لاگین/ثبت‌نام رخ داده، به‌جای نمایش صفحه‌ی ترسناک 403،
   کاربر را به یک فرم تازه با token معتبر برمی‌گرداند.
"""

import hashlib
import logging

from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.middleware.csrf import CSRF_TOKEN_LENGTH
from django.urls import reverse

# مطمئن شو handlerهای ws_debug وصل شده‌اند (مسیر ماجول ممکن است متفاوت باشد).
for _candidate in ("a_users.ws_logger", "a_core.ws_logger", "ws_logger"):
    try:
        __import__(_candidate)
        break
    except Exception:
        continue

log = logging.getLogger("ws_debug")


def _fp(value):
    """انگشت‌نگاری کوتاه از یک token — خود token را لاگ نمی‌کنیم."""
    if not value:
        return "-"
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    except Exception:
        return "?"


def _login_paths():
    paths = set()
    for name in ("account_login", "account_signup"):
        try:
            paths.add(reverse(name))
        except Exception:
            continue
    paths.add("/accounts/login/")
    return paths


def csrf_failure(request, reason="", template_name="403_csrf.html"):
    cookie_token = request.COOKIES.get("csrftoken") or ""
    post_token = ""
    try:
        post_token = request.POST.get("csrfmiddlewaretoken") or ""
    except Exception:
        post_token = ""
    header_token = request.META.get("HTTP_X_CSRFTOKEN", "") or ""
    sent_token = post_token or header_token

    try:
        session_key = request.session.session_key
    except Exception:
        session_key = None

    ua = request.META.get("HTTP_USER_AGENT", "") or ""

    details = {
        "path": request.path,
        "method": request.method,
        "reason": str(reason),
        "user": getattr(getattr(request, "user", None), "username", None) or "anon",
        "cookie_present": bool(cookie_token),
        "cookie_fp": _fp(cookie_token),
        "cookie_len": len(cookie_token),
        "sent_from": "post" if post_token else ("header" if header_token else "none"),
        "sent_fp": _fp(sent_token),
        "sent_len": len(sent_token),
        "tokens_match": bool(cookie_token) and bool(sent_token) and _fp(cookie_token) == _fp(sent_token),
        "expected_len": CSRF_TOKEN_LENGTH,
        "session_key_present": bool(session_key),
        "referer": request.META.get("HTTP_REFERER", "-"),
        "origin": request.META.get("HTTP_ORIGIN", "-"),
        "next": (request.POST.get("next") if request.method == "POST" else None) or "-",
        "is_secure": request.is_secure(),
        "ua": ua[:180],
    }

    log.error(
        "[CSRF_FAIL] "
        + " ".join("{}={}".format(k, v) for k, v in details.items())
    )

    # اگر خطا روی فرم لاگین/ثبت‌نام بود، کاربر را به فرم تازه بفرست
    # (با token سالم) تا صفحه‌ی 403 نبیند.
    if request.path in _login_paths():
        try:
            from django.contrib import messages

            messages.error(
                request,
                "نشست ورود منقضی شده بود. لطفاً دوباره وارد شوید.",
            )
        except Exception:
            pass

        log.info("[CSRF_FAIL] recovered=redirect_to_fresh_login path=%s", request.path)

        response = HttpResponseRedirect("{}?next=/&csrf_retry=1".format(request.path))
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    html = (
        '<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>درخواست معتبر نبود</title></head>"
        '<body style="font-family:Tahoma,sans-serif;padding:24px;text-align:center">'
        "<h2>درخواست معتبر نبود</h2>"
        "<p>لطفاً صفحه را تازه کنید و دوباره تلاش کنید.</p>"
        '<p><a href="/">بازگشت به خانه</a></p>'
        "</body></html>"
    )
    response = HttpResponseForbidden(html, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response
