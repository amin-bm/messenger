from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    image = models.ImageField(upload_to='avatars/', null=True, blank=True)
    displayname = models.CharField(max_length=20, null=True, blank=True)
    phone = models.CharField(max_length=32, null=True, blank=True, unique=True)
    info = models.TextField(null=True, blank=True) 
    approved = models.BooleanField(default=False)
    is_manager = models.BooleanField(default=False)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    
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
