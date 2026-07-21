from allauth.account.adapter import DefaultAccountAdapter


def is_mobile_request(request) -> bool:
    """تشخیص ساده‌ی موبایل/WebView بر اساس User-Agent."""
    ua = (request.META.get("HTTP_USER_AGENT", "") or "").lower()
    return (
        "android" in ua
        or "iphone" in ua
        or "ipad" in ua
        or "ipod" in ua
        or "mobile" in ua
        or " wv" in ua        # WebView اندروید
        or "; wv)" in ua
    )


class CustomAccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        # توجه: این متد فقط وقتی اجرا می‌شود که next خالی/نامعتبر باشد؛
        # اگر next مقدار داشته باشد allauth آن را ترجیح می‌دهد.
        if is_mobile_request(request):
            return "/"
        return super().get_login_redirect_url(request)
