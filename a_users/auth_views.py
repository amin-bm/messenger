from random import randint

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Profile
from .sms_service import send_otp_sms_ir


_PERSIAN_ARABIC_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def _normalize_phone(value: str) -> str:
    value = (value or "").translate(_PERSIAN_ARABIC_DIGITS)
    value = value.replace(" ", "").replace("-", "").replace("_", "").replace("(", "").replace(")", "")
    return value


def _clear_otp_session(session):
    keys = [
        "otp_login_phone",
        "otp_login_code",
        "otp_login_expires",
        "otp_login_attempts",
        "otp_login_sent_at",
        "otp_login_next",
    ]
    for k in keys:
        session.pop(k, None)

def _clear_otp_password_reset_session(session):
    keys = [
        "otp_reset_phone",
        "otp_reset_code",
        "otp_reset_expires",
        "otp_reset_attempts",
        "otp_reset_sent_at",
        "otp_reset_verified_until",
    ]
    for k in keys:
        session.pop(k, None)


@require_POST
def otp_login_start(request):
    phone = _normalize_phone(request.POST.get("phone", ""))
    next_url = request.POST.get("next") or ""

    if not phone:
        messages.error(request, "شماره موبایل را وارد کنید.")
        return redirect(reverse("account_login") + "?otp=start")

    profile = Profile.objects.filter(phone=phone).select_related("user").first()
    if not profile:
        messages.error(request, "این شماره موبایل ثبت‌نام نشده است.")
        return redirect(reverse("account_login") + "?otp=start")

    now = int(timezone.now().timestamp())
    last_sent_at = int(request.session.get("otp_login_sent_at") or 0)
    if now - last_sent_at < 60:
        messages.error(request, "برای ارسال مجدد کمی صبر کنید.")
        return redirect(reverse("account_login") + "?otp=verify")

    otp = str(randint(100000, 999999))
    request.session["otp_login_phone"] = phone
    request.session["otp_login_code"] = otp
    request.session["otp_login_expires"] = now + 300
    request.session["otp_login_attempts"] = 0
    request.session["otp_login_sent_at"] = now
    request.session["otp_login_next"] = next_url

    if send_otp_sms_ir(phone, otp):
        messages.success(request, "کد یکبار مصرف ارسال شد.")
        return redirect(reverse("account_login") + "?otp=verify")

    _clear_otp_session(request.session)
    messages.error(request, "مشکلی در ارسال پیامک پیش آمد.")
    return redirect(reverse("account_login") + "?otp=start")


@require_POST
def otp_login_verify(request):
    entered = (request.POST.get("otp") or "").strip()
    now = int(timezone.now().timestamp())

    phone = request.session.get("otp_login_phone")
    code = request.session.get("otp_login_code")
    expires = int(request.session.get("otp_login_expires") or 0)

    if not phone or not code or expires <= now:
        _clear_otp_session(request.session)
        messages.error(request, "کد منقضی شده است. دوباره تلاش کنید.")
        return redirect(reverse("account_login") + "?otp=start")

    attempts = int(request.session.get("otp_login_attempts") or 0)
    if attempts >= 5:
        _clear_otp_session(request.session)
        messages.error(request, "تعداد تلاش‌ها زیاد شد. دوباره کد بگیرید.")
        return redirect(reverse("account_login") + "?otp=start")

    if entered != str(code):
        request.session["otp_login_attempts"] = attempts + 1
        messages.error(request, "کد تایید اشتباه است.")
        return redirect(reverse("account_login") + "?otp=verify")

    profile = Profile.objects.filter(phone=phone).select_related("user").first()
    if not profile:
        _clear_otp_session(request.session)
        messages.error(request, "حساب کاربری با این شماره پیدا نشد.")
        return redirect(reverse("account_login") + "?otp=start")

    user = profile.user
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    next_url = request.session.get("otp_login_next") or request.POST.get("next") or ""
    _clear_otp_session(request.session)

    if next_url:
        return redirect(next_url)
    return redirect(getattr(settings, "LOGIN_REDIRECT_URL", "/"))


def otp_login_reset(request):
    _clear_otp_session(request.session)
    return redirect(reverse("account_login") + "?otp=start")


@require_POST
def otp_password_reset_start(request):
    phone = _normalize_phone(request.POST.get("phone", ""))

    if not phone:
        messages.error(request, "شماره موبایل را وارد کنید.")
        return redirect(reverse("account_login") + "?otp=reset_start")

    profile = Profile.objects.filter(phone=phone).select_related("user").first()
    if not profile:
        messages.error(request, "حسابی با این شماره موبایل پیدا نشد.")
        return redirect(reverse("account_login") + "?otp=reset_start")

    now = int(timezone.now().timestamp())
    last_sent_at = int(request.session.get("otp_reset_sent_at") or 0)
    if now - last_sent_at < 60:
        messages.error(request, "برای ارسال مجدد کمی صبر کنید.")
        return redirect(reverse("account_login") + "?otp=reset_verify")

    otp = str(randint(100000, 999999))
    request.session["otp_reset_phone"] = phone
    request.session["otp_reset_code"] = otp
    request.session["otp_reset_expires"] = now + 300
    request.session["otp_reset_attempts"] = 0
    request.session["otp_reset_sent_at"] = now
    request.session.pop("otp_reset_verified_until", None)

    if send_otp_sms_ir(phone, otp):
        messages.success(request, "کد بازیابی ارسال شد.")
        return redirect(reverse("account_login") + "?otp=reset_verify")

    _clear_otp_password_reset_session(request.session)
    messages.error(request, "مشکلی در ارسال پیامک پیش آمد.")
    return redirect(reverse("account_login") + "?otp=reset_start")


@require_POST
def otp_password_reset_verify(request):
    entered = (request.POST.get("otp") or "").strip()
    now = int(timezone.now().timestamp())

    phone = request.session.get("otp_reset_phone")
    code = request.session.get("otp_reset_code")
    expires = int(request.session.get("otp_reset_expires") or 0)

    if not phone or not code or expires <= now:
        _clear_otp_password_reset_session(request.session)
        messages.error(request, "کد منقضی شده است. دوباره تلاش کنید.")
        return redirect(reverse("account_login") + "?otp=reset_start")

    attempts = int(request.session.get("otp_reset_attempts") or 0)
    if attempts >= 5:
        _clear_otp_password_reset_session(request.session)
        messages.error(request, "تعداد تلاش‌ها زیاد شد. دوباره کد بگیرید.")
        return redirect(reverse("account_login") + "?otp=reset_start")

    if entered != str(code):
        request.session["otp_reset_attempts"] = attempts + 1
        messages.error(request, "کد تایید اشتباه است.")
        return redirect(reverse("account_login") + "?otp=reset_verify")

    request.session["otp_reset_verified_until"] = now + 300
    messages.success(request, "تایید شد. رمز جدید را وارد کنید.")
    return redirect(reverse("account_login") + "?otp=reset_set")


@require_POST
def otp_password_reset_set(request):
    now = int(timezone.now().timestamp())
    verified_until = int(request.session.get("otp_reset_verified_until") or 0)
    phone = request.session.get("otp_reset_phone")

    if not phone or verified_until <= now:
        _clear_otp_password_reset_session(request.session)
        messages.error(request, "برای تنظیم رمز، دوباره کد بگیرید.")
        return redirect(reverse("account_login") + "?otp=reset_start")

    profile = Profile.objects.filter(phone=phone).select_related("user").first()
    if not profile:
        _clear_otp_password_reset_session(request.session)
        messages.error(request, "حساب کاربری با این شماره پیدا نشد.")
        return redirect(reverse("account_login") + "?otp=reset_start")

    password1 = (request.POST.get("password1") or "").strip()
    password2 = (request.POST.get("password2") or "").strip()
    if not password1:
        messages.error(request, "رمز جدید را وارد کنید.")
        return redirect(reverse("account_login") + "?otp=reset_set")
    if password1 != password2:
        messages.error(request, "رمز و تکرار آن یکسان نیست.")
        return redirect(reverse("account_login") + "?otp=reset_set")

    user = profile.user
    user.set_password(password1)
    user.save(update_fields=["password"])

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    _clear_otp_password_reset_session(request.session)
    messages.success(request, "رمز عبور با موفقیت تغییر کرد.")
    return redirect(getattr(settings, "LOGIN_REDIRECT_URL", "/"))


def otp_password_reset_reset(request):
    _clear_otp_password_reset_session(request.session)
    return redirect(reverse("account_login") + "?otp=reset_start")

