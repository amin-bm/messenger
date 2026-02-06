from django.contrib import admin
from .models import ChatGroup, GroupMessage


@admin.register(ChatGroup)
class ChatGroupAdmin(admin.ModelAdmin):
    list_display = ("groupchat_name", "group_slug", "group_name", "is_private", "admin")
    search_fields = ("groupchat_name", "group_slug", "group_name")
    list_filter = ("is_private",)


@admin.register(GroupMessage)
class GroupMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "author", "created", "edited")
    search_fields = ("body", "group__groupchat_name", "group__group_slug", "group__group_name", "author__username")
    list_filter = ("created", "edited")
