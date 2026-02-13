from django.conf import settings

def project_title(request):
    return {
        'PROJECT_TITLE': settings.PROJECT_TITLE,
        'APP_VERSION': getattr(settings, "APP_VERSION", "dev"),
        'APP_RESET_REQUIRED': bool(getattr(settings, "APP_RESET_REQUIRED", False)),
        'APP_RESET_MESSAGE': getattr(settings, "APP_RESET_MESSAGE", ""),
    }
