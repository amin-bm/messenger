from django import forms
from django.core.exceptions import ValidationError

from allauth.account.forms import SignupForm
from allauth.account.forms import LoginForm

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
            username.label = "نام کاربری"
            _apply_widget_attrs(
                username,
                {"class": _INPUT_CLASS, "placeholder": "نام کاربری", "dir": "auto", "autocomplete": "username"},
            )

        phone = self.fields.get("phone")
        if phone:
            phone.label = "شماره موبایل"
            _apply_widget_attrs(
                phone,
                {"class": _INPUT_CLASS, "placeholder": "شماره موبایل", "dir": "ltr", "inputmode": "tel", "autocomplete": "tel"},
            )

        password1 = self.fields.get("password1")
        if password1:
            password1.label = "رمز عبور"
            _apply_widget_attrs(password1, {"class": _INPUT_CLASS, "placeholder": "رمز عبور"})

        password2 = self.fields.get("password2")
        if password2:
            password2.label = "تکرار رمز عبور"
            _apply_widget_attrs(password2, {"class": _INPUT_CLASS, "placeholder": "تکرار رمز عبور"})

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
            login.label = "نام کاربری"
            _apply_widget_attrs(
                login,
                {"class": _INPUT_CLASS, "placeholder": "نام کاربری", "dir": "auto", "autocomplete": "username"},
            )

        password = self.fields.get("password")
        if password:
            password.label = "رمز عبور"
            _apply_widget_attrs(password, {"class": _INPUT_CLASS, "placeholder": "رمز عبور"})

        remember = self.fields.get("remember")
        if remember:
            remember.label = "مرا به خاطر بسپار"
            _apply_widget_attrs(
                remember,
                {"class": "h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"},
            )
