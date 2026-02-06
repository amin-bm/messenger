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
            'body': forms.Textarea(attrs={'placeholder': 'Write a message...', 'class': 'w-full !bg-transparent !px-2 !py-2 !rounded-none text-sm text-gray-900 placeholder-gray-400 outline-none resize-none leading-6 max-h-40 overflow-y-auto', 'style': 'unicode-bidi: plaintext; text-align: start;', 'dir': 'auto', 'maxlength': '300', 'rows': 1, 'autofocus': True, 'autocomplete': 'off'}),
        }


class NewGroupForm(ModelForm):
    class Meta:
        model = ChatGroup
        fields = ('groupchat_name', 'is_private')
        widgets = {
            'groupchat_name': forms.TextInput(attrs={
                'placeholder': 'Add name...',
                'class': 'p-4 text-black',
                'maxlength': '300',
                'autofocus': True,
                }),
        }


class ChatRoomEditFrom(ModelForm):
    class Meta:
        model = ChatGroup
        fields = ['groupchat_name']
        widgets = {
            'groupchat_name' : forms.TextInput(attrs={
                'class': 'p-4 text-xl font-bold mb-4',
                'maxlength' : '300',
                }),
        }
