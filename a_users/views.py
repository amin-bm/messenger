from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from allauth.account.utils import send_email_confirmation
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib.auth.models import User
from django.contrib.auth.views import redirect_to_login
from django.contrib import messages
from .forms import *
from .models import Profile

def profile_view(request, username=None):
    if username:
        profile = get_object_or_404(User, username=username).profile
    else:
        try:
            profile = request.user.profile
        except:
            return redirect_to_login(request.get_full_path())
    return render(request, 'a_users/profile.html', {'profile':profile})


@login_required
def profile_edit_view(request):
    onboarding = request.path == reverse('profile-onboarding')
    form = ProfileForm(instance=request.user.profile)  
    
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=request.user.profile)
        if form.is_valid():
            form.save()
            if onboarding:
                profile = getattr(request.user, "profile", None)
                approved = bool(
                    getattr(request.user, "is_staff", False)
                    or getattr(request.user, "is_superuser", False)
                    or getattr(profile, "is_manager", False)
                    or getattr(profile, "approved", False)
                )
                if approved:
                    return redirect('home')
                messages.info(request, 'پروفایل شما ثبت شد و در انتظار تایید مدیر است.')
                return redirect('profile')
            return redirect('profile')
      
    return render(request, 'a_users/profile_edit.html', { 'form':form, 'onboarding':onboarding })


@login_required
def profile_settings_view(request):
    return render(request, 'a_users/profile_settings.html')


@login_required
def manager_panel_view(request):
    current_profile = getattr(request.user, "profile", None)
    can_approve = bool(
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
        or getattr(current_profile, "is_manager", False)
    )
    can_manage_managers = bool(getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False))

    if not can_approve:
        messages.warning(request, "شما به این بخش دسترسی ندارید.")
        return redirect("profile")

    if request.method == "POST":
        action = request.POST.get("action")
        user_id = request.POST.get("user_id")
        if not user_id:
            messages.warning(request, "کاربر انتخاب نشده است.")
            return redirect("profile-manager")
        target_user = get_object_or_404(User, id=user_id)
        target_profile = getattr(target_user, "profile", None)
        if not target_profile:
            target_profile = Profile.objects.create(user=target_user)

        if action == "approve":
            if not target_profile.approved:
                target_profile.approved = True
                target_profile.save(update_fields=["approved"])
                messages.success(request, f"کاربر {target_user.username} تایید شد.")
            return redirect("profile-manager")

        if action == "toggle_manager":
            if not can_manage_managers:
                messages.warning(request, "شما اجازه تبدیل کاربر به مدیر را ندارید.")
                return redirect("profile-manager")

            target_profile.is_manager = not bool(getattr(target_profile, "is_manager", False))
            if target_profile.is_manager:
                target_profile.approved = True
            target_profile.save(update_fields=["is_manager", "approved"])
            messages.success(request, f"سطح دسترسی {target_user.username} به‌روزرسانی شد.")
            return redirect("profile-manager")

        messages.warning(request, "عملیات نامعتبر است.")
        return redirect("profile-manager")

    q = (request.GET.get("q") or "").strip()
    pending_profiles = (
        Profile.objects.select_related("user")
        .filter(approved=False)
        .order_by("user__date_joined", "user__id")
    )
    manager_profiles = Profile.objects.select_related("user").order_by("user__username")
    if q:
        manager_profiles = manager_profiles.filter(user__username__icontains=q)

    return render(
        request,
        "a_users/manager.html",
        {
            "pending_profiles": pending_profiles,
            "manager_profiles": manager_profiles,
            "can_manage_managers": can_manage_managers,
            "q": q,
        },
    )


@login_required
def profile_emailchange(request):
    
    if request.htmx:
        form = EmailForm(instance=request.user)
        return render(request, 'partials/email_form.html', {'form':form})
    
    if request.method == 'POST':
        form = EmailForm(request.POST, instance=request.user)

        if form.is_valid():
            
            # Check if the email already exists
            email = form.cleaned_data['email']
            if User.objects.filter(email=email).exclude(id=request.user.id).exists():
                messages.warning(request, 'این ایمیل قبلاً استفاده شده است.')
                return redirect('profile-settings')
            
            form.save() 
            
            # Then Signal updates emailaddress and set verified to False
            
            # Then send confirmation email 
            # send_email_confirmation() will be deprecated soon!
            send_email_confirmation(request, request.user)
            
            return redirect('profile-settings')
        else:
            messages.warning(request, 'ایمیل معتبر نیست یا قبلاً استفاده شده است.')
            return redirect('profile-settings')
        
    return redirect('profile-settings')


@login_required
def profile_usernamechange(request):
    if request.htmx:
        form = UsernameForm(instance=request.user)
        return render(request, 'partials/username_form.html', {'form':form})
    
    if request.method == 'POST':
        form = UsernameForm(request.POST, instance=request.user)
        
        if form.is_valid():
            form.save()
            messages.success(request, 'نام کاربری با موفقیت به‌روزرسانی شد.')
            return redirect('profile-settings')
        else:
            messages.warning(request, 'نام کاربری معتبر نیست یا قبلاً استفاده شده است.')
            return redirect('profile-settings')
    
    return redirect('profile-settings')    


@login_required
def profile_emailverify(request):
    send_email_confirmation(request, request.user)
    return redirect('profile-settings')


@login_required
def profile_delete_view(request):
    user = request.user
    if request.method == "POST":
        logout(request)
        user.delete()
        messages.success(request, 'حساب کاربری حذف شد.')
        return redirect('home')
    
    return render(request, 'a_users/profile_delete.html')
