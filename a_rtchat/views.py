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
from django.http import HttpResponse, JsonResponse
from django.http import Http404
from django.template import context
from django.utils import timezone
from django.core.files.base import File
from django.utils.text import slugify
from django.db.models import Q
from django.urls import reverse
from django.utils.html import escape, format_html
from django.db import transaction
from django.conf import settings
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
        if request.user.id == getattr(chat_group, "admin_id", None) and not chat_group.members.filter(id=request.user.id).exists():
            chat_group.members.add(request.user)
        if not chat_group.members.filter(id=request.user.id).exists():
            raise Http404


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

    return format_html(
        "{}{}<span class=\"font-semibold text-blue-700\">{}</span>{}{}",
        prefix,
        before,
        match,
        after,
        suffix,
    )


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
        my_groups_qs = request.user.chat_groups.all()
        if not my_groups_qs.exists():
            return render(request, 'layouts/telegram.html')
        last_state = (
            ChatState.objects
            .filter(user=request.user, group__in=my_groups_qs)
            .select_related('group')
            .order_by('-last_read')
            .first()
        )
        if last_state and last_state.group_id:
            chat_group = last_state.group
        else:
            last_group = my_groups_qs.order_by('-id').first()
            if last_group:
                chat_group = last_group
            else:
                chat_group = get_chat_group_by_identifier('public_chat')
    else:
        chat_group = get_chat_group_by_identifier(chatroom_identifier)

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404

    chatroom_identifier = chat_group.group_slug or chat_group.group_name
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
            .order_by("-created")[:15]
        )
        newer = list(
            chat_group.chat_messages
            .filter(created__gte=focus_message.created)
            .order_by("created")[:15]
        )
        chat_messages = older[::-1] + newer
    else:
        chat_messages = list(chat_group.chat_messages.order_by("-created")[:30])[::-1]
    form = ChatmessageCreateForm()

    other_user = None;
    if chat_group.is_private:
        if request.user not in chat_group.members.all():
            raise Http404
        for member in chat_group.members.all():
            if member != request.user:
                other_user = member
                break

    if chat_group.groupchat_name and chat_group.group_name != 'public_chat':
        if request.user == chat_group.admin and request.user not in chat_group.members.all():
            chat_group.members.add(request.user)
        if request.user not in chat_group.members.all():
            raise Http404


    if request.htmx:
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
            }
            return render(request, 'a_rtchat/partials/chat_message_p.html', context)

    context = {
        'chat_messages': chat_messages,
        'form': form,
        'other_user': other_user,
        'chatroom_identifier': chatroom_identifier,
        'chatroom_ws_name': chat_group.group_name,
        'chat_group': chat_group,
    }

    return render(request, 'a_rtchat/chat.html', context)


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
        form = NewGroupForm(request.POST)
        if form.is_valid():
            new_groupchat = form.save(commit=False)
            new_groupchat.admin = request.user
            new_groupchat.save()
            new_groupchat.members.add(request.user)
            return redirect('chatroom', new_groupchat.group_slug or new_groupchat.group_name)

    context = {
        'form': form,
    }
    return render(request, 'a_rtchat/create_groupchat.html', context)


@messenger_required
def chatroom_edit_view(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if request.user != chat_group.admin:
        raise Http404()
    
    form = ChatRoomEditFrom(instance=chat_group)

    if request.method == 'POST':
        form = ChatRoomEditFrom(request.POST, instance=chat_group)
        if form.is_valid():
            form.save()

            remove_members = request.POST.getlist('remove_members')
            for member_id in remove_members:
                member = User.objects.get(id=member_id)
                if chat_group.admin_id and member.id == chat_group.admin_id:
                    continue
                chat_group.members.remove(member)

            add_members = request.POST.getlist('add_members')
            for member_id in add_members:
                member = User.objects.get(id=member_id)
                chat_group.members.add(member)

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
    }
    return render(request, 'a_rtchat/chatroom_edit.html', context)


@messenger_required
def chatroom_delete_view(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if request.user != chat_group.admin:
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
    
    if request.method == 'POST':
        chat_group.members.remove(request.user)
        messages.success(request, 'You left the chatroom')
        return redirect('home')
    
    
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
        message = GroupMessage.objects.create(
            file = file,
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

        message = GroupMessage(group=chat_group, author=request.user, reply_to=reply_to)
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
def chat_message_transcode(request, message_id):
    message = get_object_or_404(GroupMessage.objects.select_related("group"), id=message_id)
    chat_group = message.group

    if chat_group.group_name == "public_chat" and not _public_chat_visible_to_user(request.user, chat_group):
        raise Http404

    if getattr(chat_group, "is_private", False):
        if not chat_group.members.filter(id=request.user.id).exists():
            raise Http404
    elif chat_group.groupchat_name and chat_group.group_name != "public_chat":
        if request.user == chat_group.admin and not chat_group.members.filter(id=request.user.id).exists():
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
        if request.user == chat_group.admin and request.user not in chat_group.members.all():
            chat_group.members.add(request.user)
        if request.user not in chat_group.members.all():
            raise Http404

    if request.method != "POST":
        return HttpResponse(status=405)

    if message.file:
        return HttpResponse(status=400)

    body = (request.POST.get("body") or "").strip()
    if not body:
        return HttpResponse(status=400)

    if body != (message.body or ""):
        message.body = body
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
        if message.author_id != request.user.id and chat_group.admin_id != request.user.id:
            raise Http404

        if chat_group.groupchat_name and chat_group.group_name != 'public_chat':
            if request.user == chat_group.admin and request.user not in chat_group.members.all():
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
        if request.user == chat_group.admin and request.user not in chat_group.members.all():
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
        if request.user == target_group.admin and request.user not in target_group.members.all():
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
