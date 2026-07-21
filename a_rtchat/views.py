import io
import re
import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from os import remove
from functools import wraps
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.http import HttpResponse, JsonResponse, FileResponse, StreamingHttpResponse
from django.http import Http404
from django.template import context
from django.utils import timezone
from django.core.files.base import File, ContentFile
from django.core.files.storage import default_storage
from django.utils.text import slugify
from django.db.models import Q
from django.urls import reverse
from django.utils.html import escape, format_html
from django.db import transaction
from django.conf import settings
from PIL import Image, ImageOps
from .models import *
from .forms import *
from .consumers import _send_push_notifications_for_message


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


def _public_chat_visible_to_user(user: User, public_chat: ChatGroup) -> bool:
    if bool(getattr(settings, "CHAT_PUBLIC_CHAT_VISIBLE_TO_ALL", False)):
        return True
    if _user_is_manager_or_staff(user):
        return True
    return public_chat.members.filter(id=user.id).exists()

def _assert_user_can_access_chat_group(request, chat_group: ChatGroup) -> None:
    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404
    if getattr(chat_group, "is_private", False):
        if not chat_group.members.filter(id=request.user.id).exists():
            raise Http404
        return
    if getattr(chat_group, "groupchat_name", None) and chat_group.group_name != "public_chat":
        if _user_is_chat_group_admin(request.user, chat_group) and not chat_group.members.filter(id=request.user.id).exists():
            chat_group.members.add(request.user)
        if not chat_group.members.filter(id=request.user.id).exists():
            raise Http404


def _user_is_chat_group_admin(user: User, chat_group: ChatGroup) -> bool:
    try:
        return chat_group.is_admin(user)
    except Exception:
        return False


def messenger_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if _user_can_access_messenger(request.user):
            return view_func(request, *args, **kwargs)
        messages.warning(request, "پروفایل شما هنوز توسط مدیر تایید نشده است.")
        if getattr(request, "htmx", False):
            return HttpResponse(status=403)
        return redirect("profile")

    return _wrapped


def get_chat_group_by_identifier(chatroom_identifier):
    return get_object_or_404(
        ChatGroup,
        Q(group_slug=chatroom_identifier) | Q(group_name=chatroom_identifier),
    )

def _chat_title_for_user(chat_group: ChatGroup, user: User):
    if getattr(chat_group, "is_private", False):
        other = chat_group.members.exclude(id=user.id).select_related("profile").first()
        if other:
            return getattr(other.profile, "name", None) or other.username, f"@{other.username}"
        return chat_group.group_slug or chat_group.group_name, ""

    if chat_group.group_name == "public_chat":
        return chat_group.groupchat_name or "Public Chat", "General"

    if chat_group.groupchat_name:
        return chat_group.groupchat_name, "Group"

    return chat_group.group_slug or chat_group.group_name, ""


def _build_highlight_snippet(text: str, q: str, radius: int = 40):
    s = (text or "").strip()
    q = (q or "").strip()
    if not s or not q:
        return escape(s[:120])

    low = s.lower()
    qlow = q.lower()
    idx = low.find(qlow)
    if idx < 0:
        return escape(s[:120])

    start = max(0, idx - radius)
    end = min(len(s), idx + len(q) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(s) else ""

    before = escape(s[start:idx])
    match = escape(s[idx:idx + len(q)])
    after = escape(s[idx + len(q):end])

    return format_html("{}{}<span class=\"sidebar-search-highlight\">{}</span>{}{}", prefix, before, match, after, suffix)


def _maybe_transcode_audio_message_to_mp3(message: GroupMessage) -> tuple[bool, str]:
    if not message or not getattr(message, "file", None):
        return False, "no_file"
    name = getattr(message.file, "name", "") or ""
    ext = os.path.splitext(name)[1].lower()
    if ext not in {".webm", ".ogg"}:
        return True, "noop"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg_missing"

    try:
        input_path = message.file.path
    except Exception:
        return False, "no_path"
    tmp_path = ""
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = tmp.name
        tmp.close()

        subprocess.run(
            [ffmpeg, "-y", "-i", input_path, "-vn", "-ac", "1", "-ar", "48000", "-b:a", "64k", tmp_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        base = os.path.splitext(os.path.basename(name))[0]
        folder = os.path.dirname(name)
        target_name = os.path.join(folder, f"{base}.mp3").replace("\\", "/")
        with open(tmp_path, "rb") as f:
            message.file.save(target_name, File(f), save=False)
        message.save(update_fields=["file"])
        try:
            message.file.storage.delete(name)
        except Exception:
            pass
    except Exception:
        return False, "transcode_failed"
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    return True, "converted"



@messenger_required
def chat_view(request, chatroom_identifier='public_chat'):
    if request.path == '/':
        # صفحه‌ی خانه فقط پوسته‌ی تلگرام/لیست چت‌ها را نشان می‌دهد و به‌طور
        # خودکار وارد آخرین چت نمی‌شود؛ این از برگشت اشتباه به یک چت در موبایل
        # جلوگیری می‌کند. باز کردن خودکار آخرین چت در دسکتاپ سمت JS انجام می‌شود.
        return render(request, 'layouts/telegram.html')
    else:
        chat_group = get_chat_group_by_identifier(chatroom_identifier)

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404

    chatroom_identifier = chat_group.group_slug or chat_group.group_name
    _MSG_RELATED = (
        "author", "author__profile",
        "reply_to", "reply_to__author", "reply_to__author__profile",
        "forwarded_from", "forwarded_from__profile",
    )
    focus_raw = (request.GET.get("focus") or "").strip()
    focus_message = None
    if focus_raw.isdigit():
        focus_message = (
            GroupMessage.objects
            .filter(group=chat_group, id=int(focus_raw))
            .select_related("group")
            .first()
        )

    if focus_message:
        older = list(
            chat_group.chat_messages
            .filter(created__lt=focus_message.created)
            .select_related(*_MSG_RELATED)
            .prefetch_related("reactions__user__profile")
            .order_by("-created")[:15]
        )
        newer = list(
            chat_group.chat_messages
            .filter(created__gte=focus_message.created)
            .select_related(*_MSG_RELATED)
            .prefetch_related("reactions__user__profile")
            .order_by("created")[:15]
        )
        chat_messages = older[::-1] + newer
    else:
        chat_messages = list(
            chat_group.chat_messages
            .select_related(*_MSG_RELATED)
            .prefetch_related("reactions__user__profile")
            .order_by("-created")[:30]
        )[::-1]
    form = ChatmessageCreateForm()

    other_user = None;
    if chat_group.is_private:
        if request.user not in chat_group.members.all():
            raise Http404
        for member in chat_group.members.all():
            if member != request.user:
                other_user = member
                break
    private_other_last_read = None
    if chat_group.is_private and other_user:
        other_state = (
            ChatState.objects
            .filter(user=other_user, group=chat_group)
            .only("last_read")
            .first()
        )
        private_other_last_read = other_state.last_read if other_state else None

    if chat_group.groupchat_name and chat_group.group_name != 'public_chat':
        if _user_is_chat_group_admin(request.user, chat_group) and request.user not in chat_group.members.all():
            chat_group.members.add(request.user)
        if request.user not in chat_group.members.all():
            raise Http404


    if request.htmx and request.method == "POST":
        form = ChatmessageCreateForm(request.POST)
        if form.is_valid():
            message = form.save(commit=False)
            message.group = chat_group
            message.author = request.user
            reply_to_id = (request.POST.get('reply_to') or '').strip()
            if reply_to_id.isdigit():
                message.reply_to = (
                    GroupMessage.objects
                    .filter(group=chat_group, id=int(reply_to_id))
                    .first()
                )
            message.save()
            transaction.on_commit(lambda: _send_push_notifications_for_message(message.id))

            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                chat_group.group_name,
                {"type": "message_handler", "message_id": message.id},
            )

            target_user_ids = list(chat_group.members.values_list("id", flat=True))
            if (
                not target_user_ids
                and chat_group.group_name == "public_chat"
                and bool(getattr(settings, "CHAT_PUBLIC_CHAT_VISIBLE_TO_ALL", False))
            ):
                refresh_event = {"type": "online_status_handler"}
            else:
                refresh_event = {"type": "online_status_handler", "target_user_ids": (target_user_ids or [request.user.id])}
            async_to_sync(channel_layer.group_send)("online-status", refresh_event)

            context = {
                'message': message,
                'user' : request.user,
                'chat_group': chat_group,
                'is_group_admin': _user_is_chat_group_admin(request.user, chat_group),
                'private_other_last_read': private_other_last_read,
            }
            return render(request, 'a_rtchat/partials/chat_message_p.html', context)

    context = {
        'chat_messages': chat_messages,
        'form': form,
        'other_user': other_user,
        'chatroom_identifier': chatroom_identifier,
        'chatroom_ws_name': chat_group.group_name,
        'chat_group': chat_group,
        'is_group_admin': _user_is_chat_group_admin(request.user, chat_group),
        'private_other_last_read': private_other_last_read,
        'chat_group_admin_ids': list(
            set(chat_group.admins.values_list("id", flat=True))
            | ({int(chat_group.admin_id)} if chat_group.admin_id else set())
        ),
        'pinned_messages': list(
            chat_group.chat_messages
            .filter(is_pinned=True)
            .select_related("author", "author__profile")
            .order_by("pinned_at", "id")
        ),
        'can_pin_messages': _user_can_pin_in_group(request.user, chat_group),
    }

    is_chat_nav = request.headers.get("X-Chat-Nav") == "1"
    if not is_chat_nav:
        hx_request = (request.headers.get("HX-Request") or "").lower() == "true"
        hx_target = (request.headers.get("HX-Target") or "")
        if hx_request and hx_target == "tg-chat-content":
            is_chat_nav = True

    if is_chat_nav:
        context["chat_base_template"] = "a_rtchat/chat_base_empty.html"

    return render(request, 'a_rtchat/chat.html', context)

@messenger_required
def chat_messages_older(request, chatroom_identifier):
    if request.method != "GET":
        return HttpResponse(status=405)

    chat_group = get_chat_group_by_identifier(chatroom_identifier)
    _assert_user_can_access_chat_group(request, chat_group)

    chatroom_identifier = chat_group.group_slug or chat_group.group_name
    private_other_last_read = None
    if chat_group.is_private:
        other_user = chat_group.members.exclude(id=request.user.id).first()
        if other_user:
            other_state = (
                ChatState.objects
                .filter(user=other_user, group=chat_group)
                .only("last_read")
                .first()
            )
            private_other_last_read = other_state.last_read if other_state else None
    _MSG_RELATED = (
        "author", "author__profile",
        "reply_to", "reply_to__author", "reply_to__author__profile",
        "forwarded_from", "forwarded_from__profile",
    )
    before_raw = (request.GET.get("before") or "").strip()
    if not before_raw.isdigit():
        return HttpResponse(status=400)

    before_msg = (
        GroupMessage.objects
        .filter(group=chat_group, id=int(before_raw))
        .only("id", "created")
        .first()
    )
    if not before_msg:
        raise Http404

    before_created = before_msg.created
    before_id = before_msg.id

    page_size = 30
    qs = (
        chat_group.chat_messages
        .filter(Q(created__lt=before_created) | Q(created=before_created, id__lt=before_id))
        .select_related(*_MSG_RELATED)
            .prefetch_related("reactions__user__profile")
        .order_by("-created", "-id")[: page_size + 1]
    )
    raw = list(qs)
    has_more = len(raw) > page_size
    if has_more:
        raw = raw[:page_size]
    chat_messages = raw[::-1]

    next_before = (chat_messages[0].id if (has_more and chat_messages) else "")

    context = {
        "chat_messages": chat_messages,
        "chat_group": chat_group,
        "chatroom_identifier": chatroom_identifier,
        "user": request.user,
        "has_more": has_more,
        "next_before": next_before,
        "is_group_admin": _user_is_chat_group_admin(request.user, chat_group),
        "private_other_last_read": private_other_last_read,
    }
    return render(request, "a_rtchat/partials/chat_messages_older.html", context)


@messenger_required
def sidebar_search(request):
    q = (request.GET.get("q") or "").strip()
    if not q:
        return render(request, "a_rtchat/partials/sidebar_search_results.html", {"q": "", "results": []})

    include_public = bool(getattr(settings, "CHAT_PUBLIC_CHAT_VISIBLE_TO_ALL", False)) or _user_is_manager_or_staff(request.user)
    accessible_groups = (
        ChatGroup.objects
        .filter(Q(members=request.user) | (Q(group_name="public_chat") if include_public else Q()))
        .exclude(group_name="online-status")
        .distinct()
    )

    group_matches = (
        accessible_groups
        .filter(Q(groupchat_name__icontains=q) | Q(group_slug__icontains=q) | Q(group_name__icontains=q))
        .order_by("groupchat_name", "group_slug")[:10]
    )

    message_qs = (
        GroupMessage.objects
        .filter(group__in=accessible_groups)
        .filter(body__icontains=q)
        .select_related("group", "author", "author__profile")
        .order_by("-created")[:50]
    )

    results = []

    for g in group_matches:
        title, subtitle = _chat_title_for_user(g, request.user)
        identifier = g.group_slug or g.group_name
        results.append({
            "kind": "chat",
            "title": title,
            "subtitle": subtitle,
            "url": reverse("chatroom", args=[identifier]),
        })

    for m in message_qs:
        g = m.group
        title, _ = _chat_title_for_user(g, request.user)
        identifier = g.group_slug or g.group_name
        author_name = getattr(getattr(m.author, "profile", None), "name", None) or m.author.username
        results.append({
            "kind": "message",
            "title": title,
            "author": author_name,
            "created": m.created,
            "url": f"{reverse('chatroom', args=[identifier])}?focus={m.id}",
            "snippet_html": _build_highlight_snippet(m.body or "", q),
        })

    return render(request, "a_rtchat/partials/sidebar_search_results.html", {"q": q, "results": results})


@messenger_required
def chat_search(request, chatroom_identifier):
    """
    Search messages within a specific chatroom.
    Returns highlighted message snippets with jump-to-message functionality.
    """
    q = (request.GET.get("q") or "").strip()
    if not q:
        return render(request, "a_rtchat/partials/chat_search_results.html", {"q": "", "results": [], "chatroom_identifier": chatroom_identifier})

    chat_group = get_chat_group_by_identifier(chatroom_identifier)
    _assert_user_can_access_chat_group(request, chat_group)

    chatroom_identifier = chat_group.group_slug or chat_group.group_name

    message_qs = (
        chat_group.chat_messages
        .filter(body__icontains=q)
        .select_related("author", "author__profile")
        .order_by("-created")[:500]
    )

    results = []
    for m in message_qs:
        author_name = getattr(getattr(m.author, "profile", None), "name", None) or m.author.username
        results.append({
            "message_id": m.id,
            "author": author_name,
            "author_is_me": m.author_id == request.user.id,
            "created": m.created,
            "body": m.body,
            "snippet_html": _build_highlight_snippet(m.body or "", q),
        })

    return render(request, "a_rtchat/partials/chat_search_results.html", {
        "q": q,
        "results": results,
        "chatroom_identifier": chatroom_identifier,
    })


@messenger_required
def get_or_create_chatroom(request, username):
    if request.user.username == username:
        return redirect('home')
    
    other_user = User.objects.get(username=username)
    my_chatrooms = request.user.chat_groups.filter(is_private=True)

    if my_chatrooms.exists():
        for chatroom in my_chatrooms:
            if other_user in chatroom.members.all():
                chatroom = chatroom
                break
        else:
            chatroom = ChatGroup.objects.create(is_private=True)
            chatroom.members.add(other_user, request.user)
    else:
        chatroom = ChatGroup.objects.create(is_private=True)
        chatroom.members.add(other_user, request.user)
   
    if not chatroom.group_slug or chatroom.group_slug == chatroom.group_name:
        usernames = sorted([request.user.username, other_user.username])
        base = slugify(f"dm-{'-'.join(usernames)}")
        base = (base or "").strip() or chatroom.group_name
        base = base[:160]

        candidate = base
        while ChatGroup.objects.filter(group_slug=candidate).exclude(pk=chatroom.pk).exists():
            suffix = shortuuid.uuid()[:8]
            cut = 160 - (len(suffix) + 1)
            candidate = f"{base[:cut]}-{suffix}"

        chatroom.group_slug = candidate
        chatroom.save(update_fields=["group_slug"])

    return redirect('chatroom', chatroom.group_slug or chatroom.group_name)


@messenger_required
def create_groupchat(request):
    form = NewGroupForm()

    if request.method == 'POST':
        form = NewGroupForm(request.POST, request.FILES)
        if form.is_valid():
            new_groupchat = form.save(commit=False)
            new_groupchat.admin = request.user
            new_groupchat.is_private = False
            new_groupchat.save()
            new_groupchat.members.add(request.user)
            new_groupchat.admins.add(request.user)
            return redirect('chatroom', new_groupchat.group_slug or new_groupchat.group_name)

    context = {
        'form': form,
    }
    return render(request, 'a_rtchat/create_groupchat.html', context)


@messenger_required
def chatroom_edit_view(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if not _user_is_chat_group_admin(request.user, chat_group):
        raise Http404()
    
    form = ChatRoomEditFrom(instance=chat_group)

    if request.method == 'POST':
        form = ChatRoomEditFrom(request.POST, request.FILES, instance=chat_group)
        if form.is_valid():
            form.save()

            remove_members = request.POST.getlist('remove_members')
            for member_id in remove_members:
                member = User.objects.get(id=member_id)
                if chat_group.admin_id and member.id == chat_group.admin_id:
                    continue
                chat_group.members.remove(member)
                chat_group.admins.remove(member)

            add_members = request.POST.getlist('add_members')
            for member_id in add_members:
                member = User.objects.get(id=member_id)
                chat_group.members.add(member)

            selected_admin_ids = set()
            for raw in request.POST.getlist("group_admins"):
                if str(raw).isdigit():
                    selected_admin_ids.add(int(raw))
            if chat_group.admin_id:
                selected_admin_ids.discard(int(chat_group.admin_id))
            member_ids = set(chat_group.members.values_list("id", flat=True))
            selected_admin_ids = selected_admin_ids & member_ids
            chat_group.admins.set(User.objects.filter(id__in=selected_admin_ids))

            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "online-status",
                {"type": "online_status_handler"}
            )

            return redirect('chatroom', chatroom_name)

    member_ids = set(chat_group.members.values_list('id', flat=True))
    add_candidates = (
        User.objects
        .exclude(id=request.user.id)
        .exclude(id__in=member_ids)
        .select_related('profile')
        .order_by('username')
    )

    context = {
        'form' : form,
        'chat_group' : chat_group,
        'add_candidates': add_candidates,
        'selected_admin_ids': (
            set(chat_group.admins.values_list("id", flat=True))
            | ({int(chat_group.admin_id)} if chat_group.admin_id else set())
        ),
    }
    return render(request, 'a_rtchat/chatroom_edit.html', context)


@messenger_required
def chatroom_delete_view(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if not _user_is_chat_group_admin(request.user, chat_group):
        raise Http404()
    
    if request.method == 'POST':
        chat_group.delete()
        messages.success(request, 'Chatroom deleted')
        return redirect('home')
    
    return render(request, 'a_rtchat/chatroom_delete.html', {'chat_group':chat_group})


@messenger_required
def chatroom_leave_view(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if request.user not in chat_group.members.all():
        raise Http404()
    
    if request.method != "POST":
        return HttpResponse(status=405)

    is_group_chat = bool(chat_group.groupchat_name) and (not chat_group.is_private) and chat_group.group_name != "public_chat"
    if is_group_chat and chat_group.is_admin(request.user):
        member_ids = set(chat_group.members.values_list("id", flat=True))
        admin_ids = set(chat_group.admins.values_list("id", flat=True))
        if chat_group.admin_id:
            admin_ids.add(int(chat_group.admin_id))
        admin_member_ids = admin_ids & member_ids
        if request.user.id in admin_member_ids and len(admin_member_ids) == 1:
            messages.warning(request, "شما تنها ادمین این گروه هستید. قبل از خروج، یکی از اعضای گروه را ادمین کنید.")
            return redirect("chatroom", chat_group.group_slug or chat_group.group_name)

    if chat_group.admin_id and request.user.id == int(chat_group.admin_id):
        member_ids = set(chat_group.members.values_list("id", flat=True))
        admin_ids = set(chat_group.admins.values_list("id", flat=True))
        admin_ids.add(int(chat_group.admin_id))
        admin_member_ids = admin_ids & member_ids
        candidate_ids = list(admin_member_ids - {request.user.id})
        if candidate_ids:
            chat_group.admin_id = sorted(candidate_ids)[0]
            chat_group.save(update_fields=["admin"])
        else:
            chat_group.admin = None
            chat_group.save(update_fields=["admin"])

    chat_group.members.remove(request.user)
    chat_group.admins.remove(request.user)
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "online-status",
        {"type": "online_status_handler"},
    )
    messages.success(request, "شما از چت خارج شدید.")
    return redirect("home")
    
    
@messenger_required
def chat_file_upload(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    _assert_user_can_access_chat_group(request, chat_group)
    
    if request.htmx and request.FILES:
        file = request.FILES['file']
        reply_to_id = (request.POST.get('reply_to') or '').strip()
        reply_to = None
        if reply_to_id.isdigit():
            reply_to = (
                GroupMessage.objects
                .filter(group=chat_group, id=int(reply_to_id))
                .first()
            )
        body = (request.POST.get('body') or '').strip()[:2000] or None
        message = GroupMessage.objects.create(
            file = file,
            body = body,
            group = chat_group,
            author = request.user,
            reply_to=reply_to,
        )
        _maybe_transcode_audio_message_to_mp3(message)
        transaction.on_commit(lambda: _send_push_notifications_for_message(message.id))
        channel_layer = get_channel_layer()
        event_type = "message_handler"
        event = {
            'type': event_type,
            'message_id' : message.id,
        }
        async_to_sync(channel_layer.group_send)(
            chatroom_name, event
            )

        target_user_ids = list(chat_group.members.values_list("id", flat=True))
        if (
            not target_user_ids
            and chat_group.group_name == "public_chat"
            and bool(getattr(settings, "CHAT_PUBLIC_CHAT_VISIBLE_TO_ALL", False))
        ):
            refresh_event = {"type": "online_status_handler"}
        else:
            refresh_event = {"type": "online_status_handler", "target_user_ids": (target_user_ids or [request.user.id])}
        async_to_sync(channel_layer.group_send)("online-status", refresh_event)

    
        return HttpResponse()

@messenger_required
def chat_file_upload_chunk(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    _assert_user_can_access_chat_group(request, chat_group)

    if request.method != "POST" or "file" not in request.FILES:
        return JsonResponse({"ok": False, "error": "bad_request"}, status=400)

    upload_id_raw = (request.POST.get("upload_id") or "").strip()
    try:
        upload_uuid = uuid.UUID(upload_id_raw)
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid_upload_id"}, status=400)

    try:
        chunk_index = int(request.POST.get("chunk_index") or 0)
        total_chunks = int(request.POST.get("total_chunks") or 0)
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid_chunk_meta"}, status=400)

    if total_chunks < 1 or total_chunks > 20000:
        return JsonResponse({"ok": False, "error": "invalid_total_chunks"}, status=400)
    if chunk_index < 0 or chunk_index >= total_chunks:
        return JsonResponse({"ok": False, "error": "invalid_chunk_index"}, status=400)

    original_name = os.path.basename((request.POST.get("file_name") or "").strip() or "file")
    if len(original_name) > 160:
        original_name = original_name[:160]

    reply_to_id = (request.POST.get("reply_to") or "").strip()
    reply_to = None
    if reply_to_id.isdigit():
        reply_to = (
            GroupMessage.objects
            .filter(group=chat_group, id=int(reply_to_id))
            .first()
        )

    caption = (request.POST.get("body") or "").strip()[:2000]

    base_dir = os.path.join(settings.MEDIA_ROOT, "chunk_uploads")
    os.makedirs(base_dir, exist_ok=True)
    upload_dir = os.path.join(base_dir, str(upload_uuid))
    os.makedirs(upload_dir, exist_ok=True)

    meta_path = os.path.join(upload_dir, "meta.json")
    if os.path.exists(meta_path):
        try:
            import json
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f) or {}
        except Exception:
            meta = {}
        if meta.get("user_id") != request.user.id or meta.get("group_name") != chat_group.group_name:
            return JsonResponse({"ok": False, "error": "upload_owner_mismatch"}, status=403)
        if int(meta.get("total_chunks") or total_chunks) != total_chunks:
            return JsonResponse({"ok": False, "error": "upload_meta_mismatch"}, status=400)
    else:
        try:
            import json
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "user_id": request.user.id,
                        "group_name": chat_group.group_name,
                        "total_chunks": total_chunks,
                        "file_name": original_name,
                        "body": caption,
                    },
                    f,
                )
        except Exception:
            pass

    chunk_file = request.FILES["file"]
    chunk_path = os.path.join(upload_dir, f"{chunk_index:06d}.part")
    with open(chunk_path, "wb") as out:
        for part in chunk_file.chunks():
            out.write(part)

    received = 0
    try:
        for entry in os.scandir(upload_dir):
            if entry.is_file() and entry.name.endswith(".part") and len(entry.name) == 11:
                received += 1
    except Exception:
        received = 0

    if received < total_chunks:
        return JsonResponse({"ok": True, "done": False, "received": received, "total": total_chunks})

    lock_path = os.path.join(upload_dir, "assembling.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except Exception:
        return JsonResponse({"ok": True, "done": False, "received": received, "total": total_chunks})

    assembled_path = os.path.join(upload_dir, "assembled.bin")
    cleanup_dir = False
    try:
        with open(assembled_path, "wb") as out:
            for i in range(total_chunks):
                p = os.path.join(upload_dir, f"{i:06d}.part")
                if not os.path.exists(p):
                    try:
                        os.remove(lock_path)
                    except Exception:
                        pass
                    return JsonResponse({"ok": True, "done": False, "received": received, "total": total_chunks})
                with open(p, "rb") as inp:
                    shutil.copyfileobj(inp, out, length=1024 * 1024)

        caption_final = caption
        try:
            import json
            with open(meta_path, "r", encoding="utf-8") as mf:
                _meta_final = json.load(mf) or {}
            _mb = (_meta_final.get("body") or "").strip()[:2000]
            if _mb:
                caption_final = _mb
        except Exception:
            pass
        message = GroupMessage(group=chat_group, author=request.user, reply_to=reply_to, body=(caption_final or None))
        with open(assembled_path, "rb") as f:
            message.file.save(original_name, File(f), save=False)
        message.save()
        cleanup_dir = True
        _maybe_transcode_audio_message_to_mp3(message)
        transaction.on_commit(lambda: _send_push_notifications_for_message(message.id))

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            chatroom_name,
            {"type": "message_handler", "message_id": message.id},
        )

        target_user_ids = list(chat_group.members.values_list("id", flat=True))
        if (
            not target_user_ids
            and chat_group.group_name == "public_chat"
            and bool(getattr(settings, "CHAT_PUBLIC_CHAT_VISIBLE_TO_ALL", False))
        ):
            refresh_event = {"type": "online_status_handler"}
        else:
            refresh_event = {"type": "online_status_handler", "target_user_ids": (target_user_ids or [request.user.id])}
        async_to_sync(channel_layer.group_send)("online-status", refresh_event)

        return JsonResponse({"ok": True, "done": True, "message_id": message.id})
    finally:
        try:
            os.remove(lock_path)
        except Exception:
            pass
        if cleanup_dir:
            try:
                shutil.rmtree(upload_dir, ignore_errors=True)
            except Exception:
                pass


@messenger_required
def office_preview_pdf(request, message_id):
    if request.method != "GET":
        return HttpResponse(status=405)

    message = get_object_or_404(GroupMessage.objects.select_related("group"), id=message_id)
    chat_group = message.group
    _assert_user_can_access_chat_group(request, chat_group)

    if not getattr(message, "file", None):
        return JsonResponse({"ok": False, "reason": "no_file"}, status=404)

    src_path = getattr(message.file, "path", "") or ""
    if not src_path or not os.path.exists(src_path):
        return JsonResponse({"ok": False, "reason": "missing_file"}, status=404)

    ext = os.path.splitext(src_path)[1].lower()
    if ext not in [".docx", ".doc", ".xlsx", ".xls"]:
        return JsonResponse({"ok": False, "reason": "not_office"}, status=400)

    out_dir = os.path.join(str(getattr(settings, "MEDIA_ROOT", "")), "office_previews")
    os.makedirs(out_dir, exist_ok=True)
    out_pdf_name = f"msg-{message.id}.pdf"
    out_pdf_path = os.path.join(out_dir, out_pdf_name)

    try:
        if os.path.exists(out_pdf_path) and os.path.getmtime(out_pdf_path) >= os.path.getmtime(src_path):
            return JsonResponse({"ok": True, "url": f"{settings.MEDIA_URL}office_previews/{out_pdf_name}"})
    except Exception:
        pass

    soffice_bin = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice_bin:
        return JsonResponse({"ok": False, "reason": "soffice_not_found"}, status=500)

    profile_dir = tempfile.mkdtemp(prefix="lo-profile-")
    tmp_out_dir = tempfile.mkdtemp(prefix=f"msg-{message.id}-", dir=out_dir)
    try:
        if os.name == "nt":
            profile_uri = "file:///" + profile_dir.replace("\\", "/")
        else:
            profile_uri = "file://" + profile_dir

        cmd = [
            soffice_bin,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(tmp_out_dir),
            str(src_path),
        ]

        subprocess.run(cmd, check=True, timeout=90)

        produced = None
        for entry in os.scandir(tmp_out_dir):
            if not entry.is_file():
                continue
            if not entry.name.lower().endswith(".pdf"):
                continue
            if produced is None or entry.stat().st_mtime > produced.stat().st_mtime:
                produced = entry

        if not produced:
            return JsonResponse({"ok": False, "reason": "convert_failed"}, status=500)

        try:
            os.replace(produced.path, out_pdf_path)
        except Exception:
            try:
                if os.path.exists(out_pdf_path):
                    os.remove(out_pdf_path)
            except Exception:
                pass
            shutil.move(produced.path, out_pdf_path)

        return JsonResponse({"ok": True, "url": f"{settings.MEDIA_URL}office_previews/{out_pdf_name}"})
    except subprocess.TimeoutExpired:
        return JsonResponse({"ok": False, "reason": "timeout"}, status=504)
    except Exception as e:
        return JsonResponse({"ok": False, "reason": "error", "detail": str(e)}, status=500)
    finally:
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            pass
        try:
            shutil.rmtree(tmp_out_dir, ignore_errors=True)
        except Exception:
            pass

@messenger_required
def chat_message_transcode(request, message_id):
    message = get_object_or_404(GroupMessage.objects.select_related("group"), id=message_id)
    chat_group = message.group

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404

    if getattr(chat_group, "is_private", False):
        if not chat_group.members.filter(id=request.user.id).exists():
            raise Http404
    elif chat_group.groupchat_name and chat_group.group_name != "public_chat":
        if _user_is_chat_group_admin(request.user, chat_group) and not chat_group.members.filter(id=request.user.id).exists():
            chat_group.members.add(request.user)
        if not chat_group.members.filter(id=request.user.id).exists():
            raise Http404

    ok, reason = _maybe_transcode_audio_message_to_mp3(message)
    return JsonResponse(
        {
            "ok": bool(ok),
            "reason": reason,
            "url": (message.file.url if getattr(message, "file", None) else ""),
        }
    )


@messenger_required
def chat_message_audio(request, message_id):
    """Serve an audio message with HTTP Range support so the player can seek to
    an arbitrary position instead of restarting from the beginning."""
    message = get_object_or_404(GroupMessage.objects.select_related("group"), id=message_id)
    chat_group = message.group

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404

    if getattr(chat_group, "is_private", False):
        if not chat_group.members.filter(id=request.user.id).exists():
            raise Http404
    elif chat_group.groupchat_name and chat_group.group_name != "public_chat":
        if _user_is_chat_group_admin(request.user, chat_group) and not chat_group.members.filter(id=request.user.id).exists():
            chat_group.members.add(request.user)
        if not chat_group.members.filter(id=request.user.id).exists():
            raise Http404

    if not getattr(message, "file", None) or not message.is_audio:
        raise Http404

    # Storage backends without a local filesystem path (e.g. S3) usually serve
    # ranges themselves, so fall back to the storage URL.
    try:
        file_path = message.file.path
    except (NotImplementedError, ValueError, AttributeError):
        return redirect(message.file.url)

    if not os.path.exists(file_path):
        raise Http404

    file_size = os.path.getsize(file_path)
    content_type = message.mime_type or "application/octet-stream"
    range_header = request.META.get("HTTP_RANGE", "").strip()
    range_match = re.match(r"bytes=(\d+)-(\d*)$", range_header)

    if range_match:
        start = int(range_match.group(1))
        end_raw = range_match.group(2)
        end = int(end_raw) if end_raw else file_size - 1
        end = min(end, file_size - 1)
        if start > end or start >= file_size:
            resp = HttpResponse(status=416)
            resp["Content-Range"] = f"bytes */{file_size}"
            resp["Accept-Ranges"] = "bytes"
            return resp
        length = end - start + 1

        def _stream(path=file_path, offset=start, remaining=length, block=8192):
            with open(path, "rb") as fh:
                fh.seek(offset)
                while remaining > 0:
                    chunk = fh.read(min(block, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        resp = StreamingHttpResponse(_stream(), status=206, content_type=content_type)
        resp["Content-Length"] = str(length)
        resp["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    else:
        resp = FileResponse(open(file_path, "rb"), content_type=content_type)
        resp["Content-Length"] = str(file_size)

    resp["Accept-Ranges"] = "bytes"
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


@messenger_required
def chat_media_gallery(request, chatroom_identifier):
    """Return media gallery (images and videos) for a chat group."""
    if request.method != "GET":
        return HttpResponse(status=405)

    chat_group = get_chat_group_by_identifier(chatroom_identifier)
    _assert_user_can_access_chat_group(request, chat_group)

    chatroom_identifier = chat_group.group_slug or chat_group.group_name

    # Get messages with any attached file (images, videos, documents)
    file_messages = (
        chat_group.chat_messages
        .filter(file__isnull=False)
        .select_related("author", "author__profile")
        .order_by("-created")[:200]
    )

    media_items = []
    file_items = []
    for msg in file_messages:
        if not msg.file:
            continue
        if msg.is_image or msg.is_video:
            media_items.append({
                "message_id": msg.id,
                "url": msg.file.url,
                "thumb_url": reverse('chat-message-thumb', args=[msg.id]) if msg.is_image else msg.file.url,
                "filename": msg.filename,
                "mime_type": msg.mime_type,
                "is_image": msg.is_image,
                "is_video": msg.is_video,
                "created": msg.created,
                "author_name": getattr(msg.author.profile, 'name', None) or msg.author.username,
            })
        elif not msg.is_audio:
            # Documents: PDF, Word, Excel, ZIP, RAR, EXE, etc. (skip audio)
            size_bytes = _file_size(msg.file)
            file_items.append({
                "message_id": msg.id,
                "url": msg.file.url,
                "filename": msg.filename,
                "mime_type": msg.mime_type,
                "extension": _file_extension(msg.filename),
                "size_bytes": size_bytes,
                "size_display": _format_file_size(size_bytes),
                "created": msg.created,
                "author_name": getattr(msg.author.profile, 'name', None) or msg.author.username,
            })

    is_group = bool(chat_group.groupchat_name) and not chat_group.is_private
    chat_group_admin_ids = list(
        set(chat_group.admins.values_list("id", flat=True))
        | ({int(chat_group.admin_id)} if chat_group.admin_id else set())
    )

    # Handle tab parameter (media, files, or members)
    active_tab = request.GET.get('tab', 'media')
    if active_tab not in ('media', 'files', 'members'):
        active_tab = 'media'

    context = {
        "chat_group": chat_group,
        "chatroom_identifier": chatroom_identifier,
        "media_items": media_items,
        "file_items": file_items,
        "is_group": is_group,
        "chat_group_admin_ids": chat_group_admin_ids,
        "active_tab": active_tab,
    }
    return render(request, "a_rtchat/partials/media_gallery.html", context)


def _file_extension(filename):
    """Return the lowercase file extension (without dot), e.g. 'pdf'."""
    if not filename:
        return ""
    return os.path.splitext(filename)[1].lstrip(".").lower()


def _file_size(file_field):
    """Return the size in bytes of a FileField, or 0 if unavailable."""
    try:
        file_field.seek(0, os.SEEK_END)
        size = file_field.tell()
        file_field.seek(0)
        return size
    except Exception:
        try:
            return file_field.size
        except Exception:
            return 0


def _format_file_size(size_bytes):
    """Human-readable file size, e.g. '1.2 MB'."""
    try:
        size = float(size_bytes)
    except (TypeError, ValueError):
        return ""
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024.0
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} PB"

@messenger_required
def chat_message_image_thumb(request, message_id):
    if request.method != "GET":
        return HttpResponse(status=405)

    message = get_object_or_404(GroupMessage.objects.select_related("group"), id=message_id)
    chat_group = message.group
    _assert_user_can_access_chat_group(request, chat_group)

    if not getattr(message, "file", None) or not message.is_image:
        raise Http404

    name = getattr(message.file, "name", "") or ""
    size = int(getattr(message.file, "size", 0) or 0)
    digest = hashlib.sha256(f"{message_id}:{name}:{size}:thumb-v1".encode("utf-8")).hexdigest()[:16]
    etag = f'"{digest}"'

    if request.META.get("HTTP_IF_NONE_MATCH", "").strip() == etag:
        r = HttpResponse(status=304)
        r["ETag"] = etag
        r["Cache-Control"] = "private, max-age=31536000, immutable"
        return r

    thumb_webp_rel_path = f"files/thumbs/{message_id}_{digest}.webp"
    thumb_jpg_rel_path = f"files/thumbs/{message_id}_{digest}.jpg"

    if default_storage.exists(thumb_webp_rel_path):
        with default_storage.open(thumb_webp_rel_path, "rb") as f:
            data = f.read()
        res = HttpResponse(data, content_type="image/webp")
        res["ETag"] = etag
        res["Cache-Control"] = "private, max-age=31536000, immutable"
        return res
    if default_storage.exists(thumb_jpg_rel_path):
        with default_storage.open(thumb_jpg_rel_path, "rb") as f:
            data = f.read()
        res = HttpResponse(data, content_type="image/jpeg")
        res["ETag"] = etag
        res["Cache-Control"] = "private, max-age=31536000, immutable"
        return res

    max_px = 480
    try:
        message.file.open("rb")
        try:
            img = Image.open(message.file)
        except Exception:
            raise Http404
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)

        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        try:
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=70, method=6)
            data = buf.getvalue()
            if not default_storage.exists(thumb_webp_rel_path):
                default_storage.save(thumb_webp_rel_path, ContentFile(data))
            res = HttpResponse(data, content_type="image/webp")
            res["ETag"] = etag
            res["Cache-Control"] = "private, max-age=31536000, immutable"
            return res
        except Exception:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75, optimize=True, progressive=True)
            data = buf.getvalue()
            if not default_storage.exists(thumb_jpg_rel_path):
                default_storage.save(thumb_jpg_rel_path, ContentFile(data))
            res = HttpResponse(data, content_type="image/jpeg")
            res["ETag"] = etag
            res["Cache-Control"] = "private, max-age=31536000, immutable"
            return res
    finally:
        try:
            message.file.close()
        except Exception:
            pass

@messenger_required
def chat_message_edit(request, message_id):
    message = get_object_or_404(GroupMessage, id=message_id)
    chat_group = message.group

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404

    if message.author_id != request.user.id:
        raise Http404

    if chat_group.is_private and request.user not in chat_group.members.all():
        raise Http404

    if chat_group.groupchat_name and chat_group.group_name != 'public_chat':
        if _user_is_chat_group_admin(request.user, chat_group) and request.user not in chat_group.members.all():
            chat_group.members.add(request.user)
        if request.user not in chat_group.members.all():
            raise Http404

    if request.method != "POST":
        return HttpResponse(status=405)

    body = (request.POST.get("body") or "").strip()[:2000]
    if not body and not message.file:
        return HttpResponse(status=400)

    new_body = body or None
    if new_body != (message.body or None):
        message.body = new_body
        message.edited = timezone.now()
        message.save(update_fields=["body", "edited"])

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            chat_group.group_name,
            {"type": "message_edited_handler", "message_id": message.id},
        )
        async_to_sync(channel_layer.group_send)(
            "online-status",
            {"type": "online_status_handler"},
        )

    context = {
        "message": message,
        "user": request.user,
        "chat_group": chat_group,
        "is_group_admin": _user_is_chat_group_admin(request.user, chat_group),
    }
    return render(request, "a_rtchat/chat_message.html", context)


@messenger_required
def chat_message_delete(request, message_id):
    message = get_object_or_404(GroupMessage, id=message_id)
    chat_group = message.group

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404

    if chat_group.is_private:
        if request.user not in chat_group.members.all():
            raise Http404
    else:
        if message.author_id != request.user.id and not _user_is_chat_group_admin(request.user, chat_group):
            raise Http404

        if chat_group.groupchat_name and chat_group.group_name != 'public_chat':
            if _user_is_chat_group_admin(request.user, chat_group) and request.user not in chat_group.members.all():
                chat_group.members.add(request.user)
            if request.user not in chat_group.members.all():
                raise Http404

    if request.method != "POST":
        return HttpResponse(status=405)

    reply_ids = list(message.replies.values_list("id", flat=True))
    if message.file:
        message.file.delete(save=False)

    deleted_message_id = message.id
    message.delete()

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        chat_group.group_name,
        {"type": "message_deleted_handler", "message_id": deleted_message_id},
    )
    for reply_id in reply_ids:
        async_to_sync(channel_layer.group_send)(
            chat_group.group_name,
            {"type": "message_edited_handler", "message_id": reply_id},
        )
    async_to_sync(channel_layer.group_send)(
        "online-status",
        {"type": "online_status_handler"},
    )

    return HttpResponse(status=204)

@messenger_required
def chat_message_forward(request, message_id):
    message = get_object_or_404(GroupMessage, id=message_id)
    chat_group = message.group

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404


    if request.method != "POST":
        return HttpResponse(status=405)

    if chat_group.is_private and request.user not in chat_group.members.all():
        raise Http404

    if chat_group.groupchat_name and chat_group.group_name != 'public_chat':
        if _user_is_chat_group_admin(request.user, chat_group) and request.user not in chat_group.members.all():
            chat_group.members.add(request.user)
        if request.user not in chat_group.members.all():
            raise Http404

    target_identifier = (request.POST.get("target") or "").strip()
    if not target_identifier:
        return HttpResponse(status=400)

    try:
        target_group = get_chat_group_by_identifier(target_identifier)
    except Http404:
        other_user = User.objects.filter(username=target_identifier).first()
        if not other_user or other_user.id == request.user.id:
            raise

        target_group = (
            ChatGroup.objects.filter(is_private=True)
            .filter(members=request.user)
            .filter(members=other_user)
            .distinct()
            .first()
        )

        if not target_group:
            target_group = ChatGroup.objects.create(is_private=True)
            target_group.members.add(other_user, request.user)

        if not target_group.group_slug or target_group.group_slug == target_group.group_name:
            usernames = sorted([request.user.username, other_user.username])
            base = slugify(f"dm-{'-'.join(usernames)}")
            base = (base or "").strip() or target_group.group_name
            base = base[:160]

            candidate = base
            while ChatGroup.objects.filter(group_slug=candidate).exclude(pk=target_group.pk).exists():
                suffix = shortuuid.uuid()[:8]
                cut = 160 - (len(suffix) + 1)
                candidate = f"{base[:cut]}-{suffix}"

            target_group.group_slug = candidate
            target_group.save(update_fields=["group_slug"])

    if target_group.is_private and request.user not in target_group.members.all():
        raise Http404

    if target_group.groupchat_name and target_group.group_name != 'public_chat':
        if _user_is_chat_group_admin(request.user, target_group) and request.user not in target_group.members.all():
            target_group.members.add(request.user)
        if request.user not in target_group.members.all():
            raise Http404

    forwarded = GroupMessage.objects.create(
        group=target_group,
        author=request.user,
        body=message.body,
        forwarded_from=message.author,
    )
    transaction.on_commit(lambda: _send_push_notifications_for_message(forwarded.id))

    if message.file:
        try:
            message.file.open("rb")
            try:
                message.file.seek(0)
            except Exception:
                pass
            forwarded.file.save(message.filename or "file", File(message.file), save=True)
        finally:
            try:
                message.file.close()
            except Exception:
                pass

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        target_group.group_name,
        {"type": "message_handler", "message_id": forwarded.id},
    )
    target_user_ids = list(target_group.members.values_list("id", flat=True))
    if (
        not target_user_ids
        and target_group.group_name == "public_chat"
        and bool(getattr(settings, "CHAT_PUBLIC_CHAT_VISIBLE_TO_ALL", False))
    ):
        refresh_event = {"type": "online_status_handler"}
    else:
        refresh_event = {"type": "online_status_handler", "target_user_ids": (target_user_ids or [request.user.id])}
    async_to_sync(channel_layer.group_send)("online-status", refresh_event)

    return HttpResponse(status=204)


MAX_PINNED_MESSAGES = 3


def _user_can_pin_in_group(user, chat_group):
    if getattr(chat_group, "is_private", False):
        return True
    return _user_is_chat_group_admin(user, chat_group)


def _pinned_messages_payload(chat_group):
    pinned = (
        chat_group.chat_messages
        .filter(is_pinned=True)
        .select_related("author", "author__profile")
        .order_by("pinned_at", "id")
    )
    items = []
    for m in pinned:
        author_name = getattr(getattr(m.author, "profile", None), "name", None) or m.author.username
        items.append({"id": m.id, "author": author_name, "preview": m.pin_preview})
    return items


def _broadcast_pinned_update(chat_group):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        chat_group.group_name,
        {"type": "pinned_updated_handler"},
    )


@messenger_required
def toggle_pin_message(request, message_id):
    if request.method != "POST":
        return HttpResponse(status=405)

    message = get_object_or_404(GroupMessage, id=message_id)
    chat_group = message.group

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404

    if chat_group.is_private:
        if request.user not in chat_group.members.all():
            raise Http404
    else:
        if chat_group.groupchat_name and chat_group.group_name != 'public_chat':
            if _user_is_chat_group_admin(request.user, chat_group) and request.user not in chat_group.members.all():
                chat_group.members.add(request.user)
            if request.user not in chat_group.members.all():
                raise Http404

    if not _user_can_pin_in_group(request.user, chat_group):
        return JsonResponse({"error": "forbidden"}, status=403)

    if message.is_pinned:
        message.is_pinned = False
        message.pinned_at = None
        message.pinned_by = None
        message.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])
        _broadcast_pinned_update(chat_group)
        return HttpResponse(status=204)

    pinned_qs = chat_group.chat_messages.filter(is_pinned=True)
    replace_raw = (request.POST.get("replace") or "").strip()

    if pinned_qs.count() >= MAX_PINNED_MESSAGES:
        victim = None
        if replace_raw.isdigit():
            victim = pinned_qs.filter(id=int(replace_raw)).first()
        if not victim:
            return JsonResponse(
                {"error": "limit", "max": MAX_PINNED_MESSAGES, "pinned": _pinned_messages_payload(chat_group)},
                status=409,
            )
        victim.is_pinned = False
        victim.pinned_at = None
        victim.pinned_by = None
        victim.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])

    message.is_pinned = True
    message.pinned_at = timezone.now()
    message.pinned_by = request.user
    message.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])
    _broadcast_pinned_update(chat_group)
    return HttpResponse(status=204)


@messenger_required
def toggle_pin(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404

    if chat_group.is_private and request.user not in chat_group.members.all():
        raise Http404

    state, _ = ChatState.objects.get_or_create(user=request.user, group=chat_group)
    state.is_pinned = not state.is_pinned
    state.save(update_fields=['is_pinned'])

    # 🔥 Force sidebar refresh via WS
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "online-status",
        {"type": "online_status_handler"}
    )

    return HttpResponse(status=204)


@messenger_required
def toggle_mute(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404

    if chat_group.is_private and request.user not in chat_group.members.all():
        raise Http404

    state, _ = ChatState.objects.get_or_create(user=request.user, group=chat_group)
    state.is_muted = not state.is_muted
    state.save(update_fields=['is_muted'])

    # 🔥 Force sidebar refresh via WS
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "online-status",
        {"type": "online_status_handler"}
    )

    return HttpResponse(status=204)
