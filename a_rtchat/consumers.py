from typing import Any
from collections import defaultdict
from channels.generic.websocket import WebsocketConsumer
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from asgiref.sync import async_to_sync
import base64
import json

from django.db.models import OuterRef, Subquery, Count, Value, Q
from django.db.models.functions import Coalesce
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

from django.contrib.auth.models import User
from a_users.models import Profile, PushSubscription
from .models import ChatGroup, GroupMessage, ChatState


PRESENCE_TIMEOUT = timedelta(seconds=90)

def _b64decode_any(value: str) -> bytes | None:
    raw = (value or "").strip()
    if not raw:
        return None
    raw = "".join(raw.split())
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    candidate = (raw + pad).encode("ascii", "ignore")
    try:
        if "-" in raw or "_" in raw:
            return base64.urlsafe_b64decode(candidate)
        return base64.b64decode(candidate, validate=True)
    except Exception:
        try:
            return base64.urlsafe_b64decode(candidate)
        except Exception:
            return None


def _normalize_vapid_private_key_for_pywebpush(raw_value: str) -> str:
    raw = (raw_value or "").strip()
    if not raw:
        return ""
    if "BEGIN PRIVATE KEY" in raw or "BEGIN EC PRIVATE KEY" in raw:
        return raw

    decoded = _b64decode_any(raw)
    if not decoded:
        return raw

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import load_der_private_key
    except Exception:
        return raw

    der = None
    try:
        load_der_private_key(decoded, password=None)
        der = decoded
    except Exception:
        if len(decoded) != 32:
            return raw
        try:
            key = ec.derive_private_key(int.from_bytes(decoded, "big"), ec.SECP256R1())
        except Exception:
            return raw
        try:
            der = key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        except Exception:
            return raw

    try:
        return base64.urlsafe_b64encode(der).decode("ascii").rstrip("=")
    except Exception:
        return raw


def _send_push_notifications_for_message(message_id: int) -> None:
    if bool(getattr(settings, "OFFLINE_MODE", False)):
        return

    private_key_raw = (getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "") or "").strip()
    private_key = _normalize_vapid_private_key_for_pywebpush(private_key_raw)
    public_key = (getattr(settings, "WEBPUSH_VAPID_PUBLIC_KEY", "") or "").strip()
    if not private_key or not public_key:
        return

    try:
        from pywebpush import webpush, WebPushException
    except Exception:
        return

    message = (
        GroupMessage.objects
        .select_related("group", "author", "author__profile")
        .get(id=message_id)
    )
    group = message.group

    member_ids = list(group.members.exclude(id=message.author_id).values_list("id", flat=True))
    if not member_ids:
        return

    muted_ids = set(
        ChatState.objects
        .filter(group=group, user_id__in=member_ids, is_muted=True)
        .values_list("user_id", flat=True)
    )
    target_ids = [uid for uid in member_ids if uid not in muted_ids]
    if not target_ids:
        return

    cutoff = _presence_cutoff()
    online_in_room_ids = set(
        group.users_online
        .filter(id__in=target_ids, profile__last_seen__gte=cutoff)
        .values_list("id", flat=True)
    )
    target_ids = [uid for uid in target_ids if uid not in online_in_room_ids]
    if not target_ids:
        return

    body = ""
    if message.body:
        body = message.body
    elif message.file:
        body = f"📎 {message.filename or 'File'}"

    identifier = group.group_slug or group.group_name
    url = f"/chat/room/{identifier}"

    claims_sub = (getattr(settings, "WEBPUSH_VAPID_CLAIMS_SUB", "") or "").strip() or "mailto:admin@localhost"
    vapid_claims = {"sub": claims_sub}

    author_name = getattr(getattr(message.author, "profile", None), "name", None) or message.author.username

    subs = PushSubscription.objects.filter(user_id__in=target_ids)
    for sub in subs:
        title = "پیام جدید"
        if group.is_private:
            title = author_name
        elif group.group_name == "public_chat":
            title = group.groupchat_name or "Public Chat"
        else:
            title = group.groupchat_name or identifier

        payload = {"title": title, "body": body, "url": url}
        sub_info = sub.subscription or {"endpoint": sub.endpoint, "keys": sub.keys}

        try:
            webpush(
                subscription_info=sub_info,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=private_key,
                vapid_claims=vapid_claims,
            )
        except WebPushException as e:
            resp = getattr(e, "response", None)
            status = getattr(resp, "status_code", None)
            if status in (404, 410):
                PushSubscription.objects.filter(id=sub.id).delete()
        except Exception:
            continue


def _touch_last_seen(user) -> None:
    if not getattr(user, "is_authenticated", False):
        return
    Profile.objects.filter(user=user).update(last_seen=timezone.now())


def _presence_cutoff(now=None):
    now = now or timezone.now()
    return now - PRESENCE_TIMEOUT


def _user_can_access_messenger(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        return True
    profile = getattr(user, "profile", None)
    if bool(getattr(profile, "is_manager", False)):
        return True
    return bool(getattr(profile, "approved", False))


def _user_is_manager_or_staff(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        return True
    profile = getattr(user, "profile", None)
    return bool(getattr(profile, "is_manager", False))


def _contact_users_for_user(user: User):
    contact_users = (
        User.objects
        .exclude(id=user.id)
        .select_related("profile")
    )

    if _user_is_manager_or_staff(user):
        return contact_users

    viewer_profile = getattr(user, "profile", None)
    viewer_mode = getattr(viewer_profile, "contact_visibility_mode", Profile.CONTACT_VISIBILITY_ALL)
    allowed_ids_rel = getattr(viewer_profile, "contact_visible_to", None)
    allowed_ids = allowed_ids_rel.values_list("id", flat=True) if allowed_ids_rel is not None else []
    allowed_categories_rel = getattr(viewer_profile, "contact_visible_categories", None)
    allowed_category_ids = (
        allowed_categories_rel.values_list("id", flat=True) if allowed_categories_rel is not None else []
    )
    allowed_user_ids = (
        User.objects
        .filter(
            Q(id__in=allowed_ids)
            | Q(contact_categories__id__in=allowed_category_ids)
        )
        .values_list("id", flat=True)
        .distinct()
    )

    if viewer_mode == Profile.CONTACT_VISIBILITY_SELECTED:
        return contact_users.filter(id__in=allowed_user_ids).distinct()

    visible_contacts = contact_users.filter(
        Q(profile__contact_visibility_mode=Profile.CONTACT_VISIBILITY_ALL)
        | Q(profile__contact_visible_to=user)
        | Q(profile__contact_visible_categories__members=user)
    ).distinct()
    return visible_contacts



def _public_chat_visible_to_user(user: User, public_chat: ChatGroup) -> bool:
    if bool(getattr(settings, "CHAT_PUBLIC_CHAT_VISIBLE_TO_ALL", False)):
        return True
    if _user_is_manager_or_staff(user):
        return True
    return public_chat.members.filter(id=user.id).exists()


class ChatroomConsumer(WebsocketConsumer):
    def connect(self):
        self.user = self.scope['user']
        self.chatroom_name = self.scope['url_route']['kwargs']['chatroom_name']
        self.chatroom = get_object_or_404(ChatGroup, group_name=self.chatroom_name)

        if not getattr(self.user, "is_authenticated", False):
            self.close()
            return
        if not _user_can_access_messenger(self.user):
            self.close()
            return

        if self.chatroom.group_name == "public_chat" and not _public_chat_visible_to_user(self.user, self.chatroom):
            self.close()
            return

        if self.chatroom.groupchat_name and self.chatroom.group_name != 'public_chat':
            if self.chatroom.is_admin(self.user) and self.user not in self.chatroom.members.all():
                self.chatroom.members.add(self.user)
            if self.user not in self.chatroom.members.all():
                self.close()
                return

        if self.chatroom.is_private and self.user not in self.chatroom.members.all():
            self.close()
            return

        async_to_sync(self.channel_layer.group_add)(
            self.chatroom_name,
            self.channel_name
        )

        # Add user and update online users
        if self.user not in self.chatroom.users_online.all():
            self.chatroom.users_online.add(self.user)
            self.update_online_count()
        _touch_last_seen(self.user)

        self.accept()

        # mark as read when user opens room
        state, _ = ChatState.objects.get_or_create(user=self.user, group=self.chatroom)
        state.last_read = timezone.now()
        state.save(update_fields=['last_read'])

        # ✅ refresh sidebar so unread becomes 0 immediately
        async_to_sync(self.channel_layer.group_send)(
            "online-status",
            {"type": "online_status_handler", "target_user_ids": [self.user.id]}
        )

    def disconnect(self, close_code):
        async_to_sync(self.channel_layer.group_discard)(
            self.chatroom_name,
            self.channel_name
        )

        if self.user in self.chatroom.users_online.all():
            self.chatroom.users_online.remove(self.user)
            self.update_online_count()
        _touch_last_seen(self.user)

    def receive(self, text_data):
        _touch_last_seen(self.user)
        data = json.loads(text_data)
        body = data.get('body', '').strip()
        reply_to_id = str(data.get('reply_to') or '').strip()

        if not body:
            return
        
        reply_to = None
        if reply_to_id.isdigit():
            reply_to = (
                GroupMessage.objects
                .filter(group=self.chatroom, id=int(reply_to_id))
                .first()
            )

        message = GroupMessage.objects.create(
            body=body,
            author=self.user,
            group=self.chatroom,
            reply_to=reply_to,
        )

        transaction.on_commit(lambda: _send_push_notifications_for_message(message.id))

        async_to_sync(self.channel_layer.group_send)(
            self.chatroom_name,
            {"type": "message_handler", "message_id": message.id}
        )

        # ✅ refresh sidebar (last message + unread)
        target_user_ids = list(self.chatroom.members.values_list("id", flat=True))
        if (
            not target_user_ids
            and self.chatroom.group_name == "public_chat"
            and bool(getattr(settings, "CHAT_PUBLIC_CHAT_VISIBLE_TO_ALL", False))
        ):
            event = {"type": "online_status_handler"}
        else:
            event = {"type": "online_status_handler", "target_user_ids": (target_user_ids or [self.user.id])}
        async_to_sync(self.channel_layer.group_send)("online-status", event)

    def message_handler(self, event):
        _touch_last_seen(self.user)
        message = (
            GroupMessage.objects
            .select_related(
                "group",
                "author", "author__profile",
                "reply_to", "reply_to__author", "reply_to__author__profile",
                "forwarded_from", "forwarded_from__profile",
            )
            .get(id=event['message_id'])
        )

        context = {
            'message': message,
            'user': self.user,
            'chat_group': self.chatroom,
        }
        html = render_to_string("a_rtchat/partials/chat_message_p.html", context=context)
        self.send(text_data=html)

        # ✅ If user is viewing this room, mark as read (for incoming msgs too)
        state, _ = ChatState.objects.get_or_create(user=self.user, group=self.chatroom)
        state.last_read = timezone.now()
        state.save(update_fields=['last_read'])

        # ✅ refresh sidebar so unread stays 0 while user is inside
        async_to_sync(self.channel_layer.group_send)(
            "online-status",
            {"type": "online_status_handler", "target_user_ids": [self.user.id]}
        )

    def message_edited_handler(self, event):
        message = (
            GroupMessage.objects
            .select_related(
                "group",
                "author", "author__profile",
                "reply_to", "reply_to__author", "reply_to__author__profile",
                "forwarded_from", "forwarded_from__profile",
            )
            .get(id=event['message_id'])
        )

        context = {
            'message': message,
            'user': self.user,
            'chat_group': self.chatroom,
            'oob': True,
        }
        html = render_to_string("a_rtchat/chat_message.html", context=context)
        self.send(text_data=html)

    def message_deleted_handler(self, event):
        message_id = event.get("message_id")
        if not message_id:
            return
        self.send(text_data=f'<li id="msg-{message_id}" hx-swap-oob="delete"></li>')

    def update_online_count(self):
        cutoff = _presence_cutoff()
        stale = list(self.chatroom.users_online.filter(profile__last_seen__lt=cutoff))
        if stale:
            self.chatroom.users_online.remove(*stale)
        online_count = self.chatroom.users_online.filter(profile__last_seen__gte=cutoff).count() - 1
        async_to_sync(self.channel_layer.group_send)(
            self.chatroom_name,
            {"type": "online_count_handler", "online_count": online_count}
        )

    def online_count_handler(self, event):
        online_count = event['online_count']
        author_ids = set(
            self.chatroom.chat_messages
            .order_by('-created')
            .values_list('author_id', flat=True)[:30]
        )
        users = User.objects.filter(id__in=author_ids).select_related('profile')

        context = {
            'online_count': online_count,
            'chat_group': self.chatroom,
            'users': users,
        }
        html = render_to_string("a_rtchat/partials/online_count.html", context)
        self.send(text_data=html)


class OnlineStatusConsumer(WebsocketConsumer):
    def connect(self):
        self.user = self.scope['user']
        self.group_name = 'online-status'
        self.group = get_object_or_404(ChatGroup, group_name=self.group_name)

        if not _user_can_access_messenger(self.user):
            self.close()
            return

        if self.user not in self.group.users_online.all():
            self.group.users_online.add(self.user)
        _touch_last_seen(self.user)

        async_to_sync(self.channel_layer.group_add)(
            self.group_name,
            self.channel_name
        )

        self.accept()
        self.online_status()

    def disconnect(self, close_code):
        if self.user in self.group.users_online.all():
            self.group.users_online.remove(self.user)
        _touch_last_seen(self.user)

        async_to_sync(self.channel_layer.group_discard)(
            self.group_name,
            self.channel_name
        )
        self.online_status()

    def receive(self, text_data=None, bytes_data=None):
        _touch_last_seen(self.user)
        if text_data:
            try:
                data = json.loads(text_data)
            except Exception:
                data = {}
            if (data or {}).get("type") == "ping":
                self.online_status()

    def online_status(self):
        event = {'type': 'online_status_handler'}
        async_to_sync(self.channel_layer.group_send)(self.group_name, event)

    def online_status_handler(self, event):
        target_user_ids = (event or {}).get("target_user_ids")
        if target_user_ids is not None:
            if isinstance(target_user_ids, (list, tuple, set)):
                if self.user.id not in target_user_ids:
                    return
            else:
                if self.user.id != target_user_ids:
                    return
        now = timezone.now()
        cutoff = _presence_cutoff(now)

        stale_global = list(self.group.users_online.filter(profile__last_seen__lt=cutoff))
        if stale_global:
            self.group.users_online.remove(*stale_global)

        global_online_ids = set(
            self.group.users_online.filter(profile__last_seen__gte=cutoff).values_list('id', flat=True)
        )

        # online users (global, except me)
        online_users = User.objects.filter(id__in=global_online_ids).exclude(id=self.user.id)

        # last message subquery per chat
        last_msg_qs = GroupMessage.objects.filter(group=OuterRef('pk')).order_by('-created')
        # state subquery per chat (for pinned/muted/last_read)
        state_qs = ChatState.objects.filter(user=self.user, group=OuterRef('pk'))

        my_chats = (
            self.user.chat_groups
            .all()
            .prefetch_related('members__profile', 'users_online')
            .annotate(
                last_body=Subquery(last_msg_qs.values('body')[:1]),
                last_created=Subquery(last_msg_qs.values('created')[:1]),
                last_file=Subquery(last_msg_qs.values('file')[:1]),

                last_read=Subquery(state_qs.values('last_read')[:1]),
                is_pinned=Coalesce(Subquery(state_qs.values('is_pinned')[:1]), Value(False)),
                is_muted=Coalesce(Subquery(state_qs.values('is_muted')[:1]), Value(False)),
            )
        )

        chat_ids = [c.id for c in my_chats]

        # states in one query
        states = {s.group_id: s for s in ChatState.objects.filter(user=self.user, group_id__in=chat_ids)}

        # unread counts (bucket by last_read to avoid N+1)
        unread_map = {cid: 0 for cid in chat_ids}
        buckets = defaultdict(list)
        min_dt = timezone.make_aware(timezone.datetime(1970, 1, 1))

        for cid in chat_ids:
            lr = states.get(cid).last_read if cid in states else min_dt
            buckets[lr].append(cid)

        for lr, ids in buckets.items():
            qs = (
                GroupMessage.objects
                .filter(group_id__in=ids, created__gt=lr)
                .exclude(author_id=self.user.id)
                .values('group_id')
                .annotate(c=Count('id'))
            )
            for row in qs:
                unread_map[row['group_id']] = row['c']

        sidebar_items = []

        public_chat = ChatGroup.objects.filter(group_name="public_chat").first()
        if public_chat and _public_chat_visible_to_user(self.user, public_chat):
            public_chat_online = (
                public_chat.users_online
                .filter(profile__last_seen__gte=cutoff)
                .exclude(id=self.user.id)
                .exists()
            )
            public_last = public_chat.chat_messages.order_by('-created').first()
            public_state = states.get(public_chat.id)
            public_unread_count = unread_map.get(public_chat.id, 0)
            sidebar_items.append({
                "kind": "public",
                "title": (public_chat.groupchat_name or "Public Chat"),
                "subtitle": "General",
                "url": "/chat/room/public_chat",
                "chatroom_name": "public_chat",
                "avatar_url": None,
                "avatar_letter": (public_chat.groupchat_name[:1].upper() if public_chat.groupchat_name else "P"),
                "is_online": public_chat_online,
                "is_pinned": bool(getattr(public_state, "is_pinned", False)),
                "is_muted": bool(getattr(public_state, "is_muted", False)),
                "unread_count": public_unread_count,
                "last_text": (
                    public_last.body if public_last and public_last.body
                    else ("📎 File" if public_last and public_last.file else "")
                ),
                "last_time": (public_last.created if public_last else None),
            })

        for chatroom in my_chats:
            stale_room = list(chatroom.users_online.filter(profile__last_seen__lt=cutoff))
            if stale_room:
                chatroom.users_online.remove(*stale_room)

            is_online = (
                chatroom.users_online
                .filter(profile__last_seen__gte=cutoff)
                .exclude(id=self.user.id)
                .exists()
            )

            last_text = ""
            if chatroom.last_body:
                last_text = chatroom.last_body
            elif chatroom.last_file:
                last_text = "📎 File"

            last_time = chatroom.last_created
            unread_count = unread_map.get(chatroom.id, 0)

            is_pinned = bool(getattr(chatroom, "is_pinned", False))
            is_muted = bool(getattr(chatroom, "is_muted", False))

            if chatroom.is_private:
                other = None
                for m in chatroom.members.all():
                    if m.id != self.user.id:
                        other = m
                        break
                if not other:
                    continue
                is_online = other.id in global_online_ids

                sidebar_items.append({
                    "kind": "private",
                    "title": getattr(other.profile, "name", other.username),
                    "subtitle": f"@{other.username}",
                    "url": f"/chat/room/{chatroom.group_name}",
                    "chatroom_name": chatroom.group_name,
                    "avatar_url": getattr(other.profile, "avatar", None),
                    "avatar_letter": other.username[:1].upper(),
                    "is_online": is_online,
                    "is_pinned": is_pinned,
                    "is_muted": is_muted,
                    "unread_count": unread_count,
                    "last_text": last_text,
                    "last_time": last_time,
                })

            elif chatroom.groupchat_name and chatroom.group_name != 'public_chat':
                sidebar_items.append({
                    "kind": "group",
                    "title": chatroom.groupchat_name,
                    "subtitle": "Group",
                    "url": f"/chat/room/{chatroom.group_slug or chatroom.group_name}",
                    "chatroom_name": chatroom.group_name,
                    "avatar_url": None,
                    "avatar_letter": chatroom.groupchat_name[:1].upper(),
                    "is_online": is_online,
                    "is_pinned": is_pinned,
                    "is_muted": is_muted,
                    "unread_count": unread_count,
                    "last_text": last_text,
                    "last_time": last_time,
                })

        # sort: pinned first, then by last_time desc
        min_time = min_dt
        public_items = [i for i in sidebar_items if i["kind"] == "public"]
        chat_items = [i for i in sidebar_items if i["kind"] != "public"]

        def sort_key(item):
            t = item["last_time"] or min_time
            return (not item["is_pinned"], -t.timestamp())

        chat_items.sort(key=sort_key)
        sidebar_items = public_items + chat_items

        online_in_chats = any(i["is_online"] for i in sidebar_items)

        # ---------- Contacts (all users except me) ----------
        contact_users = _contact_users_for_user(self.user)

        contacts = []
        for u in contact_users:
            contacts.append({
                "username": u.username,
                "name": getattr(u.profile, "name", u.username),
                "avatar": getattr(u.profile, "avatar", ""),
                "is_online": (u.id in global_online_ids),
                "url": f"/chat/{u.username}",
            })

        contacts.sort(key=lambda x: (not x["is_online"], x["name"].lower()))

        context = {
            "online_users": online_users,
            "online_in_chats": online_in_chats,
            "user": self.user,
            "sidebar_items": sidebar_items,
            "contacts": contacts,
        }

        html = render_to_string("a_rtchat/partials/online_status.html", context=context)
        self.send(text_data=html)


        


       
