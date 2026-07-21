from django.urls import reverse

from .adapters import is_mobile_request


def _resolve_login_path() -> str:
    try:
        return reverse("account_login")
    except Exception:
        return "/accounts/login/"


class MobileLoginNextMiddleware:
    """دو کار روی صفحه‌ی لاگین انجام می‌دهد:

    1) روی موبایل، هنگام POST به صفحه‌ی لاگین، next خطرناک/کهنه را به "/" تبدیل می‌کند
       تا بعد از لاگینِ session-expired کاربر به صفحه‌ی کهنه‌ی قبلی (با CSRF token منقضی) برنگردد.
    2) روی هر GETِ صفحه‌ی لاگین، هدرهای no-store می‌گذارد تا WebView نسخه‌ی cache‌شده با CSRF token منقضی نشان ندهد.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.login_path = _resolve_login_path()

    def __call__(self, request):
        is_login_path = request.path == self.login_path

        # (1) اصلاح next در POSTِ موبایل
        if (
            is_login_path
            and request.method == "POST"
            and is_mobile_request(request)
        ):
            nxt = (request.POST.get("next") or "").strip()
            if (
                not nxt
                or nxt.startswith("/profile/")
                or "/profile/onboarding" in nxt
            ):
                request.POST = request.POST.copy()
                request.POST["next"] = "/"

        response = self.get_response(request)

        # (2) no-store کردن صفحه‌ی لاگین (مخصوصاً GET)
        if is_login_path:
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"

        return response
