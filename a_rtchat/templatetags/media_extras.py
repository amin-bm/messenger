# a_rtchat/templatetags/media_extras.py
from urllib.parse import quote, unquote

from django import template
from django.urls import reverse

register = template.Library()

# فقط این پوشه‌های مدیا اجازه‌ی ساخت نسخه‌ی کوچک (thumbnail) دارند.
_ALLOWED_AVATAR_DIRS = ("avatars/", "group_avatars/")


@register.filter(name="thumb")
def thumb(url):
    """یک URL آواتار (پروفایل کاربر یا آواتار گروه) را به نسخه‌ی سبک thumbnail تبدیل می‌کند.

    - اگر آدرس خالی باشد، آواتار پیش‌فرض static (svg) باشد، یا مربوط به
      پوشه‌های مجاز آواتار نباشد، بدون تغییر برگردانده می‌شود.
    - در غیر این صورت به endpoint `avatar-thumb` اشاره می‌کند که یک WebP کوچک
      و cache‌شده تولید می‌کند.
    """
    if not url:
        return url

    s = str(url)

    # آواتار پیش‌فرض svg یا هر svg دیگری را دست نمی‌زنیم (خودش سبک است و PIL هم بازش نمی‌کند)
    if s.split("?", 1)[0].split("#", 1)[0].lower().endswith(".svg"):
        return s

    rel = None
    for prefix in _ALLOWED_AVATAR_DIRS:
        idx = s.find("/" + prefix)
        if idx != -1:
            rel = s[idx + 1:]
            break
        if s.startswith(prefix):
            rel = s
            break

    if not rel:
        return s

    # حذف query string / fragment احتمالی (مثلاً در storageهای ابری)
    rel = rel.split("?", 1)[0].split("#", 1)[0]

    # مهم: FieldFile.url از قبل percent-encode شده (مخصوصاً برای اسم فایل‌های فارسی/یونیکد).
    # اول به اسم واقعی decode می‌کنیم تا دوباره-انکد (double-encoding) رخ ندهد.
    rel = unquote(rel)
    if not rel or ".." in rel:
        return s

    return reverse("avatar-thumb") + "?p=" + quote(rel)
