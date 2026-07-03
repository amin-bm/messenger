from dataclasses import field
from pyexpat import model
from django.forms import ModelForm, widgets
from django import forms
from .models import *

class ChatmessageCreateForm(ModelForm):
    class Meta:
        model = GroupMessage
        fields = ('body',)
        widgets = {
            'body': forms.Textarea(attrs={'placeholder': 'Write a message...', 'class': 'w-full !bg-transparent !px-2 !rounded-none text-base text-gray-900 placeholder-gray-400 outline-none resize-none max-h-40 overflow-y-auto dark:text-slate-100 dark:placeholder-slate-400', 'style': 'unicode-bidi: plaintext; text-align: start; box-sizing: border-box; vertical-align: middle; line-height: inhert; margin-top: 3px; margin-bottom: 3px; padding-top: 3px; padding-bottom: 3px;', 'dir': 'auto', 'maxlength': '300', 'rows': 1, 'autofocus': True, 'autocomplete': 'off'}),
        }


class NewGroupForm(ModelForm):
    class Meta:
        model = ChatGroup
        fields = ('groupchat_name', 'avatar')
        widgets = {
            'groupchat_name': forms.TextInput(attrs={
                'placeholder': 'Add name...',
                'class': 'p-4 rounded-xl bg-white/70 text-slate-900 backdrop-blur dark:bg-white/10 dark:text-slate-100',
                'maxlength': '300',
                'autofocus': True,
                }),
            'avatar': forms.ClearableFileInput(attrs={
                'accept': 'image/*',
                'class': 'sr-only',
                'id': 'id_group_avatar',
                }),
        }


class ChatRoomEditFrom(ModelForm):
    class Meta:
        model = ChatGroup
        fields = ['groupchat_name', 'avatar']
        widgets = {
            'groupchat_name' : forms.TextInput(attrs={
                'class': 'p-4 text-xl font-bold mb-4 rounded-xl bg-white/70 text-slate-900 backdrop-blur dark:bg-white/10 dark:text-slate-100',
                'maxlength' : '300',
                }),
            'avatar': forms.ClearableFileInput(attrs={
                'accept': 'image/*',
                'class': 'sr-only',
                'id': 'id_group_avatar',
                }),
        }
