"""
URL configuration for a_core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings
from a_users.views import profile_view
from a_home.views import *
from a_home.pwa_debug_views import pwa_log

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/otp/', include('a_users.otp_urls')),
    path('accounts/', include('allauth.urls')),
    path('', include('a_rtchat.urls')),
    path('profile/', include('a_users.urls')),
    path('@<username>/', profile_view, name="profile"),
    path('manifest.webmanifest', pwa_manifest, name='pwa-manifest'),
    path('sw.js', pwa_service_worker, name='pwa-service-worker'),
    path('pwa/version', pwa_version, name='pwa-version'),
    path('pwa/vapid-public-key', vapid_public_key, name='pwa-vapid-public-key'),
    path('pwa/subscribe', pwa_subscribe, name='pwa-subscribe'),
    path('pwa/unsubscribe', pwa_unsubscribe, name='pwa-unsubscribe'),
    path('pwa/log', pwa_log, name='pwa-log'),
]

# Only used when DEBUG=True, whitenoise can serve files when DEBUG=False
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += [
        path("__reload__/", include("django_browser_reload.urls")),
    ]
