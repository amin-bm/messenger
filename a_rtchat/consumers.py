from typing import Any
from collections import defaultdict
from channels.generic.websocket import WebsocketConsumer
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from asgiref.sync import async_to_sync
import json

from django.db.models import OuterRef, Subquery, Count, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from django.contrib.auth.models import User
from .models import ChatGroup, GroupMessage, ChatState


def _user_can_access_messenger(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        return True
    profile = getattr(user, "profile", None)
    if bool(getattr(profile, "is_manager", False)):
        return True
    return bool(getattr(profile, "approved", False))


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

        if self.chatroom.groupchat_name and self.chatroom.group_name != 'public_chat':
            if self.user == self.chatroom.admin and self.user not in self.chatroom.members.all():
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

        self.accept()

        # mark as read when user opens room
        state, _ = ChatState.objects.get_or_create(user=self.user, group=self.chatroom)
        state.last_read = timezone.now()
        state.save(update_fields=['last_read'])

        # ✅ refresh sidebar so unread becomes 0 immediately
        async_to_sync(self.channel_layer.group_send)(
            "online-status",
            {"type": "online_status_handler"}
        )

    def disconnect(self, close_code):
        async_to_sync(self.channel_layer.group_discard)(
            self.chatroom_name,
            self.channel_name
        )

        if self.user in self.chatroom.users_online.all():
            self.chatroom.users_online.remove(self.user)
            self.update_online_count()

    def receive(self, text_data):
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

        async_to_sync(self.channel_layer.group_send)(
            self.chatroom_name,
            {"type": "message_handler", "message_id": message.id}
        )

        # ✅ refresh sidebar (last message + unread)
        async_to_sync(self.channel_layer.group_send)(
            "online-status",
            {"type": "online_status_handler"}
        )

    def message_handler(self, event):
        message = GroupMessage.objects.get(id=event['message_id'])

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
            {"type": "online_status_handler"}
        )

    def message_edited_handler(self, event):
        message = GroupMessage.objects.get(id=event['message_id'])

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
        online_count = self.chatroom.users_online.count() - 1
        async_to_sync(self.channel_layer.group_send)(
            self.chatroom_name,
            {"type": "online_count_handler", "online_count": online_count}
        )

    def online_count_handler(self, event):
        online_count = event['online_count']

        chat_messages = ChatGroup.objects.get(group_name=self.chatroom_name).chat_messages.all()[:30]
        author_ids = set([m.author_id for m in chat_messages])
        users = User.objects.filter(id__in=author_ids)

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

        if self.user not in self.group.members.all():
            self.group.users_online.add(self.user)

        async_to_sync(self.channel_layer.group_add)(
            self.group_name,
            self.channel_name
        )

        self.accept()
        self.online_status()

    def disconnect(self, close_code):
        if self.user in self.group.users_online.all():
            self.group.users_online.remove(self.user)

        async_to_sync(self.channel_layer.group_discard)(
            self.group_name,
            self.channel_name
        )
        self.online_status()

    def online_status(self):
        event = {'type': 'online_status_handler'}
        async_to_sync(self.channel_layer.group_send)(self.group_name, event)

    def online_status_handler(self, event):
        # online users (global, except me)
        online_users = self.group.users_online.exclude(id=self.user.id)

        # public chat
        public_chat = ChatGroup.objects.get(group_name='public_chat')
        public_chat_online = public_chat.users_online.exclude(id=self.user.id).exists()
        public_last = public_chat.chat_messages.order_by('-created').first()

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
        min_dt = timezone.make_aware(timezone.datetime.min)

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
            # online = someone else online
            is_online = chatroom.users_online.exclude(id=self.user.id).exists()

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

                sidebar_items.append({
                    "kind": "private",
                    "title": getattr(other.profile, "name", other.username),
                    "subtitle": f"@{other.username}",
                    "url": f"/chat/{other.username}",
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
        min_time = timezone.make_aware(timezone.datetime.min)
        public_items = [i for i in sidebar_items if i["kind"] == "public"]
        chat_items = [i for i in sidebar_items if i["kind"] != "public"]

        def sort_key(item):
            t = item["last_time"] or min_time
            return (not item["is_pinned"], -t.timestamp())

        chat_items.sort(key=sort_key)
        sidebar_items = public_items + chat_items

        online_in_chats = any(i["is_online"] for i in sidebar_items)

        # ---------- Contacts (all users except me) ----------
        contact_users = (
            User.objects
            .exclude(id=self.user.id)
            .select_related('profile')
        )

        global_online_ids = set(self.group.users_online.values_list('id', flat=True))

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


        


       
