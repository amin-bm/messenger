from django.urls import path

from .auth_views import (
    otp_login_reset,
    otp_login_start,
    otp_login_verify,
    otp_password_reset_reset,
    otp_password_reset_set,
    otp_password_reset_start,
    otp_password_reset_verify,
)


urlpatterns = [
    path("login/start/", otp_login_start, name="otp-login-start"),
    path("login/verify/", otp_login_verify, name="otp-login-verify"),
    path("login/reset/", otp_login_reset, name="otp-login-reset"),
    path("password-reset/start/", otp_password_reset_start, name="otp-password-reset-start"),
    path("password-reset/verify/", otp_password_reset_verify, name="otp-password-reset-verify"),
    path("password-reset/set/", otp_password_reset_set, name="otp-password-reset-set"),
    path("password-reset/reset/", otp_password_reset_reset, name="otp-password-reset-reset"),
]

