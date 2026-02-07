from django.contrib import admin
from .models import Profile

@admin.action(description="Approve selected profiles")
def approve_profiles(modeladmin, request, queryset):
    queryset.update(approved=True)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "approved", "phone", "displayname")
    list_filter = ("approved",)
    search_fields = ("user__username", "phone", "displayname")
    actions = (approve_profiles,)
