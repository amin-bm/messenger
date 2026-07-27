import hashlib
import logging

from django.urls import reverse

from .adapters import is_mobile_request

# مطمئن شو handlerهای ws_debug وصل شده‌اند (مسیر ماجول ممکن است متفاوت باشد).
for _candidate in ("a_users.ws_logger", "a_core.ws_logger", "ws_logger"):
    try:
        __import__(_candidate)
        break
    except Exception:
        continue

log = logging.getLogger("ws_debug")

# جانگو مقدار csrfmiddlewaretoken فرم را هر بار با یک salt تصادفی ماسک می‌کند،
# بنابراین هیچ‌وقت با مقدار خام کوکی csrftoken برابر نیست (۶۴ کاراکتر در برابر ۳۲).
# برای مقایسه‌ی درست باید اول unmask شود؛ این توابع private هستند پس import‌شان محافظت‌شده است.
try:
    from django.middleware.csrf import CSRF_TOKEN_LENGTH, _unmask_cipher_token
except Exception:  # pragma: no cover - سازگاری با نسخه‌های دیگر جانگو
    CSRF_TOKEN_LENGTH = 64
    _unmask_cipher_token = None


def _fp(value):
    """انگشت‌نگاری کوتاه از یک token — خود token لاگ نمی‌شود."""
    if not value:
        return "-"
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    except Exception:
        return "?"


def _unmask(form_token):
    """راز اصلی را از token ماسک‌شده‌ی فرم بیرون می‌کشد.

    اگر token طول ماسک‌شده (۶۴) را داشته باشد unmask می‌شود؛ اگر از قبل خام باشد
    (۳۲) همان برگردانده می‌شود. در صورت هر خطا None برمی‌گردد تا مقایسه نامعلوم بماند.
    """
    if not form_token:
        return None
    try:
        if len(form_token) == CSRF_TOKEN_LENGTH and _unmask_cipher_token is not None:
            return _unmask_cipher_token(form_token)
        return form_token
    except Exception:
        return None


def _tokens_match(cookie_token, form_token):
    """مقایسه‌ی درست کوکی csrftoken با token فرم.

    خروجی: True (هم‌خوان)، False (ناهم‌خوان) یا None (قابل ارزیابی نبود).
    """
    if not cookie_token or not form_token:
        return False
    unmasked = _unmask(form_token)
    if unmasked is None:
        return None
    return unmasked == cookie_token


def _resolve_login_path() -> str:
    try:
        return reverse("account_login")
    except Exception:
        return "/accounts/login/"


class MobileLoginNextMiddleware:
    """سه کار روی صفحه‌ی لاگین انجام می‌دهد:

    1) روی موبایل، هنگام POST به صفحه‌ی لاگین، next خطرناک/کهنه را به "/" تبدیل می‌کند
       تا بعد از لاگینِ session-expired کاربر به صفحه‌ی کهنه‌ی قبلی (با CSRF token منقضی) برنگردد.
    2) روی هر GETِ صفحه‌ی لاگین، هدرهای no-store می‌گذارد تا WebView نسخه‌ی cache‌شده با CSRF token منقضی نشان ندهد.
    3) هر GET/POST مسیر لاگین را با انگشت‌نگاری csrf لاگ می‌کند تا اگر دوباره 403 رخ داد،
       بتوانیم دقیقاً ببینیم token فرم و کوکی هم‌خوان بودند یا نه.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.login_path = _resolve_login_path()

    def __call__(self, request):
        is_login_path = request.path == self.login_path
        mobile = is_mobile_request(request) if is_login_path else False

        if is_login_path:
            cookie_token = request.COOKIES.get("csrftoken") or ""

            if request.method == "POST":
                sent = request.POST.get("csrfmiddlewaretoken") or ""
                nxt = (request.POST.get("next") or "").strip()
                match = _tokens_match(cookie_token, sent)

                # (3) لاگ تشخیصی
                log.info(
                    "[LOGIN_POST] mobile=%s cookie_fp=%s cookie_len=%s form_fp=%s form_len=%s "
                    "match=%s cookie_present=%s next=%r csrf_retry=%s",
                    mobile,
                    _fp(cookie_token),
                    len(cookie_token),
                    _fp(sent),
                    len(sent),
                    "unknown" if match is None else match,
                    bool(cookie_token),
                    nxt or "-",
                    request.GET.get("csrf_retry", "0"),
                )

                # (1) اصلاح next در POSTِ موبایل
                if mobile and (
                    not nxt
                    or nxt.startswith("/profile/")
                    or "/profile/onboarding" in nxt
                ):
                    request.POST = request.POST.copy()
                    request.POST["next"] = "/"

            elif request.method == "GET":
                log.info(
                    "[LOGIN_GET] mobile=%s cookie_present=%s cookie_fp=%s next=%r csrf_retry=%s",
                    mobile,
                    bool(cookie_token),
                    _fp(cookie_token),
                    request.GET.get("next", "-"),
                    request.GET.get("csrf_retry", "0"),
                )

        response = self.get_response(request)

        # (2) no-store کردن صفحه‌ی لاگین (مخصوصاً GET)
        if is_login_path:
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
            if getattr(response, "status_code", 200) == 403:
                log.error("[LOGIN_403] path=%s mobile=%s", request.path, mobile)

        return response
