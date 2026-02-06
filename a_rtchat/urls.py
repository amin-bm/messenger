from django.urls import path
from .views import *
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', chat_view, name='home'),
    path('chat/new-groupchat/', create_groupchat, name='new-groupchat'),
    path('chat/room/<chatroom_identifier>', chat_view, name='chatroom'),
    path('chat/<username>', get_or_create_chatroom, name='start-chat'),
    path('chat/message/<int:message_id>/edit', chat_message_edit, name='chat-message-edit'),
    path('chat/message/<int:message_id>/delete', chat_message_delete, name='chat-message-delete'),
    path('chat/edit/<chatroom_name>', chatroom_edit_view, name='edit-chatroom'),
    path('chat/delete/<chatroom_name>', chatroom_delete_view, name='chatroom-delete'),
    path('chat/leave/<chatroom_name>', chatroom_leave_view, name='chatroom-leave'),
    path('chat/fileload/<chatroom_name>', chat_file_upload, name='chat-file-upload'),
    path('chat/pin/<chatroom_name>', toggle_pin, name='chat-pin'),
    path('chat/mute/<chatroom_name>', toggle_mute, name='chat-mute'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
