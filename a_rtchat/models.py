from enum import member
from tokenize import group
import unittest
from venv import create
from django.db import models
from django.contrib.auth.models import User
import shortuuid
import os
from PIL import Image
from django.utils import timezone
import mimetypes
from django.utils.functional import cached_property


class ChatState(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_states')
    group = models.ForeignKey('ChatGroup', on_delete=models.CASCADE, related_name='states')

    last_read = models.DateTimeField(default=timezone.make_aware(timezone.datetime.min))
    is_pinned = models.BooleanField(default=False)
    is_muted = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'group')

    def __str__(self):
        return f"{self.user.username} - {self.group.group_name}"

        
class ChatGroup(models.Model):
   group_name = models.CharField(max_length=128, unique=True, default=shortuuid.uuid)
   groupchat_name = models.CharField(max_length=128, null=True, blank=True)
   admin = models.ForeignKey(User, related_name='groupchats', null=True, blank=True, on_delete=models.SET_NULL)
   users_online = models.ManyToManyField(User, related_name='online_in_groups', blank=True)
   members = models.ManyToManyField(User, related_name='chat_groups', blank=True)
   is_private = models.BooleanField(default=False)

   def __str__(self):
      return self.group_name

class GroupMessage(models.Model):
   group = models.ForeignKey(ChatGroup, related_name='chat_messages', on_delete=models.CASCADE)
   author = models.ForeignKey(User, on_delete=models.CASCADE)
   reply_to = models.ForeignKey('self', null=True, blank=True, related_name='replies', on_delete=models.SET_NULL)
   body = models.CharField(max_length=2000, null=True, blank=True)
   file = models.FileField(null=True, blank=True, upload_to='files/')
   created = models.DateTimeField(auto_now_add=True)

   
   @property
   def filename(self):
      if self.file:
         return os.path.basename(self.file.name)
      else:
         return None

   @cached_property
   def mime_type(self):
      if not self.file:
         return None
      mime, _ = mimetypes.guess_type(self.file.name)
      return mime or "application/octet-stream"

   @property
   def is_audio(self):
      if not self.file:
         return False
      mime = self.mime_type
      if mime and mime.startswith("audio/"):
         return True
      if mime in {"video/webm", "application/ogg"}:
         return True
      ext = os.path.splitext(self.file.name or "")[1].lower()
      return ext in {".webm", ".ogg", ".wav", ".mp3", ".m4a", ".aac"}
      
   def __str__(self):
      if self.body:
         return f'{self.author.username} : {self.body}'
      elif self.file:
         return f'{self.author.username} : {self.filename}'

   class Meta:
      ordering = ['-created']


   @property
   def is_image(self):
      if not self.file:
         return False
      try:
         self.file.open('rb')
         img = Image.open(self.file)
         img.verify()
         return True
      except Exception:
         return False
      finally:
         try:
               self.file.close()
         except Exception:
               pass
         

   
    
