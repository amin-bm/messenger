from enum import member
from tokenize import group
import unittest
from venv import create
from django.db import models
from django.contrib.auth.models import User
import shortuuid
import os
import uuid
from django.core.files.storage import FileSystemStorage
from PIL import Image
from django.utils import timezone
import datetime
import mimetypes
from django.utils.functional import cached_property
from django.utils.text import slugify


def generate_group_name():
    return shortuuid.uuid()


class ChatState(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_states')
    group = models.ForeignKey('ChatGroup', on_delete=models.CASCADE, related_name='states')

    last_read = models.DateTimeField(default=timezone.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc))
    is_pinned = models.BooleanField(default=False)
    is_muted = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'group')

    def __str__(self):
        return f"{self.user.username} - {self.group.group_name}"

        
class ChatGroup(models.Model):
   group_name = models.CharField(max_length=128, unique=True, default=generate_group_name)
   group_slug = models.SlugField(max_length=160, unique=True, null=True, blank=True, allow_unicode=True)
   groupchat_name = models.CharField(max_length=128, null=True, blank=True)
   avatar = models.ImageField(upload_to='group_avatars/', null=True, blank=True)
   admin = models.ForeignKey(User, related_name='groupchats', null=True, blank=True, on_delete=models.SET_NULL)
   admins = models.ManyToManyField(User, related_name="admin_in_chat_groups", blank=True)
   users_online = models.ManyToManyField(User, related_name='online_in_groups', blank=True)
   members = models.ManyToManyField(User, related_name='chat_groups', blank=True)
   is_private = models.BooleanField(default=False)

   def save(self, *args, **kwargs):
      if not self.group_slug:
         if self.group_name in {"public_chat", "online-status"}:
            base = self.group_name
         elif self.groupchat_name:
            base = slugify(self.groupchat_name, allow_unicode=True)
         else:
            base = self.group_name

         base = (base or "").strip() or self.group_name
         base = base[:160]

         Model = type(self)
         candidate = base
         while Model.objects.filter(group_slug=candidate).exclude(pk=self.pk).exists():
            suffix = shortuuid.uuid()[:8]
            cut = 160 - (len(suffix) + 1)
            candidate = f"{base[:cut]}-{suffix}"

         self.group_slug = candidate

      return super().save(*args, **kwargs)

   def __str__(self):
      return self.groupchat_name or self.group_slug or self.group_name

   def is_admin(self, user: User) -> bool:
      if not getattr(user, "is_authenticated", False):
         return False
      if user.id and user.id == getattr(self, "admin_id", None):
         return True
      if not user.id:
         return False
      try:
         return self.admins.filter(id=user.id).exists()
      except Exception:
         return False

class KeepOriginalNameStorage(FileSystemStorage):
   """نام فایل را همان‌طور که کاربر فرستاده نگه می‌دارد و فقط مسیر را امن می‌کند.
   (بر خلاف رفتار پیش‌فرض که فاصله را به آندرلاین تبدیل می‌کند)."""
   def get_valid_name(self, name):
      return os.path.basename(name)


chat_file_storage = KeepOriginalNameStorage()


def chat_file_upload_to(instance, filename):
   # هر فایل داخل یک پوشه‌ی یکتا ذخیره می‌شود تا هیچ‌وقت تداخل نام رخ ندهد
   # و Django مجبور نشود پسوند تصادفی (مثل _EMIjPm3) به نام اضافه کند.
   # شاردینگ بر اساس تاریخ + دو کاراکتر اول UUID تا هیچ پوشه‌ای بیش از حد شلوغ نشود.
   u = uuid.uuid4().hex
   return f"files/{timezone.now():%Y/%m/%d}/{u[:2]}/{u}/{filename}"


class GroupMessage(models.Model):
   group = models.ForeignKey(ChatGroup, related_name='chat_messages', on_delete=models.CASCADE)
   author = models.ForeignKey(User, on_delete=models.CASCADE)
   reply_to = models.ForeignKey('self', null=True, blank=True, related_name='replies', on_delete=models.SET_NULL)
   forwarded_from = models.ForeignKey(User, null=True, blank=True, related_name='forwarded_messages', on_delete=models.SET_NULL)
   body = models.CharField(max_length=2000, null=True, blank=True)
   file = models.FileField(null=True, blank=True, storage=chat_file_storage, upload_to=chat_file_upload_to)
   created = models.DateTimeField(auto_now_add=True)
   edited = models.DateTimeField(null=True, blank=True)
   is_pinned = models.BooleanField(default=False)
   pinned_at = models.DateTimeField(null=True, blank=True)
   pinned_by = models.ForeignKey(User, null=True, blank=True, related_name='pinned_messages', on_delete=models.SET_NULL)

   IMAGE_EXTENSIONS = frozenset({
      ".jpg", ".jpeg", ".png", ".gif",
      ".webp", ".bmp", ".tiff", ".svg",
   })

   
   @property
   def pin_preview(self):
      if self.body:
         return self.body
      if self.file:
         if self.is_audio:
            return "🎤 پیام صوتی"
         if self.is_image:
            return "🖼️ تصویر"
         if self.is_video:
            return "🎬 ویدیو"
         return "📎 " + (self.filename or "")
      return ""

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


   @cached_property
   def is_image(self):
      if not self.file:
         return False
      ext = os.path.splitext(self.file.name or "")[1].lower()
      return ext in self.IMAGE_EXTENSIONS
   
   VIDEO_EXTENSIONS = frozenset({
      ".mp4", ".webm", ".ogv", ".mov", ".m4v"
   })
   
   @cached_property
   def is_video(self):
      if not self.file:
         return False
      if self.is_audio:
         return False
      mime = self.mime_type or ""
      if mime.startswith("video/"):
         return True
      ext = os.path.splitext(self.file.name or "")[1].lower()
      return ext in self.VIDEO_EXTENSIONS

   def reaction_summary(self, user=None):
      """لیست ری‌اکشن‌ها به‌صورت گروه‌بندی‌شده بر اساس ایموجی.

      خروجی: [{"emoji": "👍", "count": 3, "reacted": True}, ...]
      اگر reactions از قبل prefetch شده باشد کوئری اضافه‌ای زده نمی‌شود.
      """
      summary = []
      index = {}
      uid = getattr(user, "id", None)
      for r in self.reactions.all():
         item = index.get(r.emoji)
         if item is None:
            item = {"emoji": r.emoji, "count": 0, "reacted": False, "users": []}
            index[r.emoji] = item
            summary.append(item)
         item["count"] += 1
         is_me = uid is not None and r.user_id == uid
         if is_me:
            item["reacted"] = True
         prof = getattr(r.user, "profile", None)
         display_name = getattr(prof, "name", None) or r.user.username
         if is_me:
            item["users"].insert(0, "شما")
         else:
            item["users"].append(display_name)
      for item in summary:
         item["users_label"] = "، ".join(item["users"])
      return summary
      

   
    


class MessageReaction(models.Model):
   message = models.ForeignKey(GroupMessage, related_name='reactions', on_delete=models.CASCADE)
   user = models.ForeignKey(User, related_name='message_reactions', on_delete=models.CASCADE)
   emoji = models.CharField(max_length=8)
   created = models.DateTimeField(auto_now_add=True)

   class Meta:
      unique_together = ('message', 'user', 'emoji')
      ordering = ['created']
      indexes = [
         models.Index(fields=['message', 'emoji']),
      ]

   def __str__(self):
      return f'{self.user.username} {self.emoji} -> msg#{self.message_id}'
