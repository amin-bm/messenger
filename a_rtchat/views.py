from nt import remove
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.http import HttpResponse
from django.http import Http404
from django.template import context
from .models import *
from .forms import *

@login_required
def chat_view(request, chatroom_name='public_chat'):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
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

    if chat_group.groupchat_name:
        if request.user not in chat_group.members.all():
            chat_group.members.add(request.user)


    if request.htmx:
        form = ChatmessageCreateForm(request.POST)
        if form.is_valid():
            message = form.save(commit=False)
            message.group = chat_group
            message.author = request.user
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
        'chatroom_name': chatroom_name,
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
   
    return redirect('chatroom', chatroom.group_name)


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
            return redirect('chatroom', new_groupchat.group_name)

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
                chat_group.members.remove(member)

            return redirect('chatroom', chatroom_name)

    context = {
        'form' : form,
        'chat_group' : chat_group
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
        message = GroupMessage.objects.create(
            file = file,
            group = chat_group,
            author = request.user
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
