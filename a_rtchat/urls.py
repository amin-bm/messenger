from django.urls import path
from .views import *
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', chat_view, name='home'),
    path('chat/new-groupchat/', create_groupchat, name='new-groupchat'),
    path('chat/room/<chatroom_identifier>/older', chat_messages_older, name='chat-messages-older'),
    path('chat/room/<chatroom_identifier>/search', chat_search, name='chat-search'),
    path('chat/room/<chatroom_identifier>', chat_view, name='chatroom'),
    path('chat/search/', sidebar_search, name='sidebar-search'),
    path('chat/<username>', get_or_create_chatroom, name='start-chat'),
    path('chat/message/<int:message_id>/edit', chat_message_edit, name='chat-message-edit'),
    path('chat/message/<int:message_id>/forward', chat_message_forward, name='chat-message-forward'),
    path('chat/message/<int:message_id>/delete', chat_message_delete, name='chat-message-delete'),
    path('chat/message/<int:message_id>/pin', toggle_pin_message, name='chat-message-pin'),
    path('chat/message/<int:message_id>/thumb', chat_message_image_thumb, name='chat-message-thumb'),
    path('chat/message/<int:message_id>/transcode', chat_message_transcode, name='chat-message-transcode'),
    path("chat/message/<int:message_id>/office-preview-pdf/", office_preview_pdf, name="office-preview-pdf"),
    path('chat/media/<chatroom_identifier>', chat_media_gallery, name='chat-media-gallery'),
    path('chat/edit/<chatroom_name>', chatroom_edit_view, name='edit-chatroom'),
    path('chat/delete/<chatroom_name>', chatroom_delete_view, name='chatroom-delete'),
    path('chat/leave/<chatroom_name>', chatroom_leave_view, name='chatroom-leave'),
    path('chat/fileload/<chatroom_name>', chat_file_upload, name='chat-file-upload'),
    path('chat/fileload-chunk/<chatroom_name>', chat_file_upload_chunk, name='chat-file-upload-chunk'),
    path('chat/pin/<chatroom_name>', toggle_pin, name='chat-pin'),
    path('chat/mute/<chatroom_name>', toggle_mute, name='chat-mute'),
    path("chat/message/<message_id>/audio", chat_message_audio, name="chat_message_audio"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
