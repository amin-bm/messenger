from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from .models import *
from .forms import ChatmessageCreateForm

@login_required
def chat_view(request):
    chat_group = get_object_or_404(ChatGroup, group_name="public_chat")
    chat_messages = chat_group.chat_messages.all()[:30]
    form = ChatmessageCreateForm()

    if request.method == 'POST':
        form = ChatmessageCreateForm(request.POST)
        if form.is_valid():
            message = form.save(commit=False)
            message.group = chat_group
            message.author = request.user
            message.save()
            return redirect('home')
            
    return render(request, 'a_rtchat/chat.html', {'chat_messages': chat_messages, 'form': form})
