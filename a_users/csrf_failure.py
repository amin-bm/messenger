"""ویوی سفارشی برای خطای CSRF.

دو کار می‌کند:
1) همه‌ی جزئیات لازم برای دیباگ را در همان فایل logs/ws_debug.log می‌نویسد.
2) اگر خطا روی صفحه‌ی لاگین/ثبت‌نام رخ داده، به‌جای نمایش صفحه‌ی ترسناک 403،
   کاربر را به یک فرم تازه با token معتبر برمی‌گرداند.
"""

import hashlib
import logging

from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse

# جانگو مقدار csrfmiddlewaretoken را هر بار با یک salt تصادفی ماسک می‌کند، پس
# برای مقایسه‌ی درست با کوکی باید اول unmask شود. این توابع private هستند،
# پس import‌شان محافظت‌شده است تا با تغییر نسخه‌ی جانگو اپ کرش نکند.
try:
    from django.middleware.csrf import CSRF_TOKEN_LENGTH, _unmask_cipher_token
except Exception:  # pragma: no cover - سازگاری با نسخه‌های دیگر جانگو
    CSRF_TOKEN_LENGTH = 64
    _unmask_cipher_token = None

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


def _unmask(form_token):
    """راز اصلی را از token ماسک‌شده‌ی فرم بیرون می‌کشد (۶۴ → ۳۲)."""
    if not form_token:
        return None
    try:
        if len(form_token) == CSRF_TOKEN_LENGTH and _unmask_cipher_token is not None:
            return _unmask_cipher_token(form_token)
        return form_token
    except Exception:
        return None


def _match_label(cookie_token, sent_token):
    """مقایسه‌ی درست کوکی و token فرم: True / False / "unknown"."""
    if not cookie_token or not sent_token:
        return False
    unmasked = _unmask(sent_token)
    if unmasked is None:
        return "unknown"
    return unmasked == cookie_token


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
        "tokens_match": _match_label(cookie_token, sent_token),
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
    #
    # گارد ضد لوپ: اگر همین الان از یک ریدایرکت بازیابی آمده‌ایم (csrf_retry=1)
    # و باز هم CSRF خطا داد، یعنی کوکی اصلاً ذخیره نمی‌شود. ریدایرکت دوباره
    # در این حالت لوپ بی‌پایان می‌سازد، پس صفحه‌ی خطای راهنما نشان می‌دهیم.
    already_retried = request.GET.get("csrf_retry") == "1"
    is_login_path = request.path in _login_paths()

    if is_login_path and already_retried:
        log.error(
            "[CSRF_FAIL] recovery_aborted=redirect_loop_guard path=%s cookie_present=%s",
            request.path,
            bool(cookie_token),
        )

    if is_login_path and not already_retried:
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

    # پیام متناسب با علت: اگر گارد لوپ فعال شده، مشکل قطعاً کوکی است.
    if is_login_path and already_retried:
        headline = "امکان ورود نبود"
        body = (
            "مرورگر شما اجازه‌ی ذخیره‌ی کوکی را نمی‌دهد، بنابراین فرم ورود تأیید نمی‌شود."
            "<br>لطفاً حالت مرور خصوصی را ببندید یا در تنظیمات مرورگر، کوکی را برای این سایت مجاز کنید."
        )
    else:
        headline = "درخواست معتبر نبود"
        body = "لطفاً صفحه را تازه کنید و دوباره تلاش کنید."

    html = (
        '<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>{h}</title></head>"
        '<body style="font-family:Tahoma,sans-serif;padding:24px;text-align:center">'
        "<h2>{h}</h2>"
        "<p>{b}</p>"
        '<p><a href="/">بازگشت به خانه</a></p>'
        "</body></html>"
    ).format(h=headline, b=body)
    response = HttpResponseForbidden(html, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response
