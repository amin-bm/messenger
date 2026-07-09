from django import template

register = template.Library()


@register.filter
def reactions_for(message, user=None):
    """فیلتر تمپلیت: خلاصه‌ی ری‌اکشن‌های یک پیام را برمی‌گرداند.

    استفاده در تمپلیت:
        {% raw %} message|reactions_for:user {% endraw %}
    خروجی: [{"emoji": "👍", "count": 2, "reacted": True}, ...]
    """
    try:
        return message.reaction_summary(user)
    except Exception:
        return []
