from nt import remove
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.http import HttpResponse
from django.http import Http404
from django.template import context
from django.utils import timezone
from django.utils.text import slugify
from django.db.models import Q
from .models import *
from .forms import *


def get_chat_group_by_identifier(chatroom_identifier):
    return get_object_or_404(
        ChatGroup,
        Q(group_slug=chatroom_identifier) | Q(group_name=chatroom_identifier),
    )


@login_required
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

    chatroom_identifier = chat_group.group_slug or chat_group.group_name
    chat_messages = chat_group.chat_messages.all()[:30]
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


@login_required
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


@login_required
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


@login_required
def chatroom_delete_view(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if request.user != chat_group.admin:
        raise Http404()
    
    if request.method == 'POST':
        chat_group.delete()
        messages.success(request, 'Chatroom deleted')
        return redirect('home')
    
    return render(request, 'a_rtchat/chatroom_delete.html', {'chat_group':chat_group})


@login_required
def chatroom_leave_view(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if request.user not in chat_group.members.all():
        raise Http404()
    
    if request.method == 'POST':
        chat_group.members.remove(request.user)
        messages.success(request, 'You left the chatroom')
        return redirect('home')
    
    
@login_required
def chat_file_upload(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    
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
        channel_layer = get_channel_layer()
        event_type = "message_handler"
        event = {
            'type': event_type,
            'message_id' : message.id,
        }
        async_to_sync(channel_layer.group_send)(
            chatroom_name, event
            )

        async_to_sync(channel_layer.group_send)(
            "online-status",
            {"type": "online_status_handler"}
        )

    
        return HttpResponse()

@login_required
def chat_message_edit(request, message_id):
    message = get_object_or_404(GroupMessage, id=message_id)
    chat_group = message.group

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


@login_required
def chat_message_delete(request, message_id):
    message = get_object_or_404(GroupMessage, id=message_id)
    chat_group = message.group

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



@login_required
def toggle_pin(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

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


@login_required
def toggle_mute(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

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
