from django.urls import path

from .auth_views import otp_login_reset, otp_login_start, otp_login_verify


urlpatterns = [
    path("login/start/", otp_login_start, name="otp-login-start"),
    path("login/verify/", otp_login_verify, name="otp-login-verify"),
    path("login/reset/", otp_login_reset, name="otp-login-reset"),
]

