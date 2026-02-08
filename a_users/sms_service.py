import json
import os
import urllib.request

from django.conf import settings


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
    except Exception:
        return None


def send_sms_ir(phone_number: str, message: str) -> bool:
    if bool(getattr(settings, "OFFLINE_MODE", False)):
        return False

    api_key = _settings_or_env("SMSIR_API_KEY")
    line_number = _settings_or_env("SMSIR_LINE_NUMBER")

    if not api_key or not line_number:
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
    if bool(getattr(settings, "OFFLINE_MODE", False)):
        print(f"OTP for {phone_number}: {otp}")
        return True

    api_key = _settings_or_env("SMSIR_API_KEY")
    template_id = _settings_or_env("SMSIR_TEMPLATE_ID")

    if not api_key or not template_id:
        if getattr(settings, "DEBUG", False):
            print(f"OTP for {phone_number}: {otp}")
            return True
        return False

    url = "https://api.sms.ir/v1/send/verify"
    payload = {
        "mobile": phone_number,
        "templateId": template_id,
        "parameters": [{"name": "CODE", "value": str(otp)}],
    }
    result = _smsir_request(url, api_key, payload)
    return bool(result) and int(result.get("status") or 0) == 1
