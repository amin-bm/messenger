from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone


class ContactCategory(models.Model):
    name = models.CharField(max_length=64, unique=True)
    members = models.ManyToManyField(
        User,
        blank=True,
        related_name="contact_categories",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Profile(models.Model):
    CONTACT_VISIBILITY_ALL = "all"
    CONTACT_VISIBILITY_SELECTED = "selected"
    CONTACT_VISIBILITY_CHOICES = [
        (CONTACT_VISIBILITY_ALL, "All"),
        (CONTACT_VISIBILITY_SELECTED, "Selected"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    image = models.ImageField(upload_to='avatars/', null=True, blank=True)
    displayname = models.CharField(max_length=20, null=True, blank=True)
    phone = models.CharField(max_length=32, null=True, blank=True, unique=True)
    info = models.TextField(null=True, blank=True) 
    approved = models.BooleanField(default=False)
    is_manager = models.BooleanField(default=False)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    contact_visibility_mode = models.CharField(
        max_length=16,
        choices=CONTACT_VISIBILITY_CHOICES,
        default=CONTACT_VISIBILITY_ALL,
    )
    contact_visible_to = models.ManyToManyField(
        User,
        blank=True,
        related_name="contact_visible_to_profiles",
    )
    contact_visible_categories = models.ManyToManyField(
        ContactCategory,
        blank=True,
        related_name="visible_profiles",
    )
    
    def __str__(self):
        return str(self.user)
    
    @property
    def name(self):
        if self.displayname:
            return self.displayname
        return self.user.username 
    
    @property
    def avatar(self):
        if self.image:
            return self.image.url
        return f'{settings.STATIC_URL}images/avatar.svg'


class PushSubscription(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(unique=True, max_length=1000)
    keys = models.JSONField(default=dict, blank=True)
    subscription = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.endpoint}"
