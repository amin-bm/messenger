import math
import time

from django import forms
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.contrib.auth.validators import UnicodeUsernameValidator

from allauth.account.forms import SignupForm
from allauth.account.forms import LoginForm
from allauth.account.adapter import get_adapter
from allauth.account import app_settings as allauth_app_settings
from allauth.core.internal import ratelimit as rl_impl

from .models import Profile


_PERSIAN_ARABIC_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def normalize_phone(value: str) -> str:
    value = (value or "").translate(_PERSIAN_ARABIC_DIGITS)
    value = value.replace(" ", "").replace("-", "").replace("_", "").replace("(", "").replace(")", "")
    return value


def _merge_class(value: str, extra: str) -> str:
    value = (value or "").strip()
    extra = (extra or "").strip()
    if not value:
        return extra
    if not extra:
        return value
    return f"{value} {extra}"


def _apply_widget_attrs(field: forms.Field, attrs: dict) -> None:
    current = dict(getattr(field.widget, "attrs", {}) or {})
    if "class" in attrs:
        current["class"] = _merge_class(current.get("class", ""), attrs.get("class", ""))
        attrs = dict(attrs)
        attrs.pop("class", None)
    current.update(attrs)
    field.widget.attrs = current


_INPUT_CLASS = "w-full rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-900 placeholder:text-gray-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-200"


class PhoneSignupForm(SignupForm):
    phone = forms.CharField(
        max_length=32,
        required=True,
        widget=forms.TextInput(attrs={"placeholder": "شماره موبایل"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        username = self.fields.get("username")
        if username:
            username.label = "نام کاربری (انگلیسی)"
            _apply_widget_attrs(
                username,
                {"class": _INPUT_CLASS, "placeholder": "user_nsme", "dir": "ltr", "autocomplete": "username"},
            )
            username.error_messages = {
                **getattr(username, "error_messages", {}),
                "required": "نام کاربری الزامی است.",
                "unique": "این نام کاربری قبلاً استفاده شده است.",
            }
            for validator in getattr(username, "validators", []):
                if isinstance(validator, UnicodeUsernameValidator):
                    validator.message = "نام کاربری معتبر نیست. فقط حروف انگلیسی، عدد و کاراکترهای @/./+/-/_ مجاز است."

        phone = self.fields.get("phone")
        if phone:
            phone.label = "شماره موبایل"
            _apply_widget_attrs(
                phone,
                {"class": _INPUT_CLASS, "placeholder": "09xxxxxxxxx", "dir": "ltr", "inputmode": "tel", "autocomplete": "tel"},
            )

        password1 = self.fields.get("password1")
        if password1:
            password1.label = "رمز عبور"
            _apply_widget_attrs(password1, {"class": _INPUT_CLASS, "placeholder": "رمز عبور"})

        password2 = self.fields.get("password2")
        if password2:
            password2.label = "تکرار رمز عبور"
            _apply_widget_attrs(password2, {"class": _INPUT_CLASS, "placeholder": "تکرار رمز عبور"})

    def clean_username(self):
        value = (self.cleaned_data.get("username") or "").strip()
        if not value and not self._signup_fields["username"]["required"]:
            return value
        try:
            return get_adapter().clean_username(value)
        except forms.ValidationError as e:
            translated = []
            for msg in e.messages:
                if "Enter a valid username." in msg or "@/./+/-/_" in msg:
                    translated.append("نام کاربری معتبر نیست. فقط حروف انگلیسی، عدد و کاراکترهای @/./+/-/_ مجاز است.")
                elif "username_taken" in msg or "already in use" in msg or "already exists" in msg or "taken" in msg:
                    translated.append("این نام کاربری قبلاً استفاده شده است.")
                else:
                    translated.append(msg)
            raise forms.ValidationError(translated)

    def clean(self):
        cleaned_data = super(SignupForm, self).clean()

        password = cleaned_data.get("password1")
        min_length = int(getattr(allauth_app_settings, "PASSWORD_MIN_LENGTH", 0) or 0)
        if password and min_length and len(password) < min_length:
            self.add_error("password1", f"رمز عبور خیلی کوتاه است. باید حداقل {min_length} کاراکتر باشد.")

        if (
            "password2" in self._signup_fields
            and cleaned_data.get("password1")
            and cleaned_data.get("password2")
            and cleaned_data["password1"] != cleaned_data["password2"]
        ):
            self.add_error("password2", "رمز عبور و تکرار آن یکسان نیست.")

        return cleaned_data

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data.get("phone", ""))
        if not phone:
            raise ValidationError("شماره موبایل الزامی است.")
        digits = "".join([c for c in phone if c.isdigit()])
        if len(digits) < 10 or len(digits) > 15:
            raise ValidationError("شماره موبایل معتبر نیست.")
        if Profile.objects.filter(phone=phone).exists():
            raise ValidationError("این شماره موبایل قبلاً ثبت شده است.")
        return phone

    def save(self, request):
        user = super().save(request)
        phone = self.cleaned_data["phone"]
        profile = getattr(user, "profile", None)
        if profile is not None:
            profile.phone = phone
            profile.save(update_fields=["phone"])
        return user


class PhoneLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        login = self.fields.get("login")
        if login:
            login.label = "نام کاربری (انگلیسی)"
            _apply_widget_attrs(
                login,
                {"class": _INPUT_CLASS, "placeholder": "user_name", "dir": "ltr", "autocomplete": "username"},
            )

        password = self.fields.get("password")
        if password:
            password.label = "رمز عبور"
            _apply_widget_attrs(password, {"class": _INPUT_CLASS, "dir": "ltr","placeholder": "Password"})

        remember = self.fields.get("remember")
        if remember:
            remember.label = "مرا به خاطر بسپار"
            _apply_widget_attrs(
                remember,
                {"class": "h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"},
            )

    def _login_failed_wait_seconds(self, credentials: dict) -> float:
        if not self.request:
            return 0
        rates = rl_impl.parse_rates(allauth_app_settings.RATE_LIMITS.get("login_failed"))
        if not rates:
            return 0
        adapter = get_adapter(self.request)
        key = adapter._get_login_attempts_cache_key(self.request, **credentials)
        now = time.time()
        max_remaining = 0
        for rate in rates:
            cache_key = rl_impl.get_cache_key(
                self.request,
                action="login_failed",
                rate=rate,
                key=key,
            )
            history = cache.get(cache_key, [])
            if not history:
                continue
            while history and history[-1] <= now - rate.duration:
                history.pop()
            if len(history) < rate.amount:
                continue
            remaining = (history[-1] + rate.duration) - now
            if remaining > max_remaining:
                max_remaining = remaining
        return max_remaining

    def _clean_with_password(self, credentials: dict):
        try:
            return super()._clean_with_password(credentials)
        except ValidationError as e:
            if getattr(e, "code", None) == "too_many_login_attempts":
                remaining = self._login_failed_wait_seconds(credentials)
                if remaining > 0:
                    minutes = max(1, math.ceil(remaining / 60))
                    raise ValidationError(
                        f"تعداد تلاش‌های ناموفق بیش از حد مجاز شده است. لطفا {minutes} دقیقه دیگر تلاش کنید.",
                        code="too_many_login_attempts",
                    )
            raise
