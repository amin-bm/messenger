from django.forms import ModelForm
from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from .models import Profile
from .allauth_forms import normalize_phone

class ProfileForm(ModelForm):
    phone = forms.CharField(max_length=32, required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        phone = self.fields.get("phone")
        if phone:
            phone.label = "Mobile Number"
            phone.widget = forms.TextInput(
                attrs={
                    "placeholder": "09xxxxxxxxx",
                    "dir": "ltr",
                    "inputmode": "tel",
                    "autocomplete": "tel",
                }
            )

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data.get("phone", ""))
        if not phone:
            raise ValidationError("شماره موبایل الزامی است.")
        digits = "".join([c for c in phone if c.isdigit()])
        if len(digits) < 10 or len(digits) > 15:
            raise ValidationError("شماره موبایل معتبر نیست.")
        qs = Profile.objects.filter(phone=phone)
        if getattr(self.instance, "pk", None):
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("این شماره موبایل قبلاً ثبت شده است.")
        return phone

    class Meta:
        model = Profile
        fields = ['image', 'displayname', 'phone', 'info' ]
        widgets = {
            'image': forms.FileInput(),
            'displayname' : forms.TextInput(attrs={'placeholder': 'Add display name'}),
            'info' : forms.Textarea(attrs={'rows':3, 'placeholder': 'Add information'})
        }
        
        
class EmailForm(ModelForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ['email']


class UsernameForm(ModelForm):
    class Meta:
        model = User
        fields = ['username']
