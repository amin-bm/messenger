import json
import logging
import os
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def _settings_or_env(name: str) -> str:
    value = str(getattr(settings, name, "") or "").strip()
    if value:
        return value
    return os.environ.get(name, "").strip()


def _smsir_request(url: str, api_key: str, payload: dict) -> dict | None:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "x-api-key": api_key},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
            return json.loads(body or "{}")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        logger.warning("SMSIR HTTPError %s for %s: %s", exc.code, url, body[:800])
        try:
            return json.loads(body or "{}")
        except Exception:
            return None
    except Exception:
        logger.exception("SMSIR request failed for %s", url)
        return None


def send_sms_ir(phone_number: str, message: str) -> bool:
    if bool(getattr(settings, "SMS_DISABLED", False)):
        return False

    api_key = _settings_or_env("SMSIR_API_KEY")
    line_number = _settings_or_env("SMSIR_LINE_NUMBER")

    if not api_key or not line_number:
        logger.warning("SMSIR bulk send disabled: missing API key or line number")
        return False

    url = "https://api.sms.ir/v1/send/bulk"
    payload = {
        "lineNumber": line_number,
        "mobiles": [phone_number],
        "messageText": message,
        "sendDateTime": None,
    }
    result = _smsir_request(url, api_key, payload)
    return bool(result) and int(result.get("status") or 0) == 1


def send_otp_sms_ir(phone_number: str, otp: str) -> bool:
    if bool(getattr(settings, "SMS_DISABLED", False)):
        print(f"OTP for {phone_number}: {otp}")
        return True

    api_key = _settings_or_env("SMSIR_API_KEY")
    template_id = _settings_or_env("SMSIR_TEMPLATE_ID")

    if not api_key or not template_id:
        logger.warning("SMSIR OTP disabled: missing API key or template id")
        if getattr(settings, "DEBUG", False):
            print(f"OTP for {phone_number}: {otp}")
            return True
        return False

    url = "https://api.sms.ir/v1/send/verify"
    try:
        template_id_value: int | str = int(template_id)
    except Exception:
        template_id_value = template_id
    payload = {
        "mobile": phone_number,
        "templateId": template_id_value,
        "parameters": [{"name": "CODE", "value": str(otp)}],
    }
    result = _smsir_request(url, api_key, payload)
    return bool(result) and int(result.get("status") or 0) == 1


def send_notify_sms_ir(phone_number: str, title: str = "") -> bool:
    """
    ارسال پیامک اعلان با استفاده از یک قالب مجزا (SMSIR_NOTIFY_TEMPLATE_ID).
    متن پیام در پنل sms.ir تعریف می‌شود و یک متغیر #NAME# دارد که با نام گیرنده پر می‌شود.
    این تابع مستقل از OFFLINE_MODE است و فقط با SMS_DISABLED کنترل می‌شود.
    """
    if bool(getattr(settings, "SMS_DISABLED", False)):
        return False

    api_key = _settings_or_env("SMSIR_API_KEY")
    template_id = _settings_or_env("SMSIR_NOTIFY_TEMPLATE_ID")

    if not api_key or not template_id:
        logger.warning("SMSIR notify disabled: missing API key or notify template id")
        return False

    url = "https://api.sms.ir/v1/send/verify"
    try:
        template_id_value: int | str = int(template_id)
    except Exception:
        template_id_value = template_id
    # قالب sms.ir حداقل یک متغیر لازم دارد. متغیر #NAME# با نام گیرنده پر می‌شود.
    name_value = (title or "").strip() or "کاربر"
    payload = {
        "mobile": phone_number,
        "templateId": template_id_value,
        "parameters": [{"name": "NAME", "value": name_value}],
    }
    result = _smsir_request(url, api_key, payload)
    return bool(result) and int(result.get("status") or 0) == 1
