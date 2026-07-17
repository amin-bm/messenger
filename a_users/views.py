from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from allauth.account.utils import send_email_confirmation
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib.auth.models import User
from django.contrib.auth.views import redirect_to_login
from django.contrib import messages
from django.core.management import call_command
from django.conf import settings
from django.db import connection
from django.http import HttpResponse, JsonResponse, FileResponse, Http404
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Count
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import io
import json
import sys
import time
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
import datetime as dt_module
from pathlib import Path
from .forms import *
from .models import Profile, ContactCategory

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

def _user_can_open_manager_panel(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        return True
    profile = getattr(user, "profile", None)
    return bool(getattr(profile, "is_manager", False))


@login_required
def manager_panel_view(request):
    current_profile = getattr(request.user, "profile", None)
    can_approve = bool(
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
        or getattr(current_profile, "is_manager", False)
    )
    can_manage_managers = can_approve

    if not can_approve:
        messages.warning(request, "شما به این بخش دسترسی ندارید.")
        return redirect("profile")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_contact_category":
            name = (request.POST.get("category_name") or "").strip()
            if not name:
                messages.warning(request, "نام دسته‌بندی خالی است.")
                return redirect("profile-manager")
            ContactCategory.objects.get_or_create(name=name)
            messages.success(request, "دسته‌بندی ایجاد شد.")
            return redirect("profile-manager")

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
    cq = (request.GET.get("cq") or "").strip()
    pending_profiles = (
        Profile.objects.select_related("user")
        .filter(approved=False)
        .order_by("user__date_joined", "user__id")
    )
    manager_profiles = Profile.objects.select_related("user").order_by("user__username")
    if q:
        manager_profiles = manager_profiles.filter(user__username__icontains=q)

    contact_profiles = Profile.objects.select_related("user").order_by("user__username")
    if cq:
        contact_profiles = contact_profiles.filter(user__username__icontains=cq)
    contact_profiles = contact_profiles[:50]

    gq = (request.GET.get("gq") or "").strip()
    contact_categories = ContactCategory.objects.annotate(member_count=Count("members")).order_by("name")
    if gq:
        contact_categories = contact_categories.filter(name__icontains=gq)

    return render(
        request,
        "a_users/manager.html",
        {
            "pending_profiles": pending_profiles,
            "manager_profiles": manager_profiles,
            "can_manage_managers": can_manage_managers,
            "q": q,
            "contact_profiles": contact_profiles,
            "cq": cq,
            "contact_categories": contact_categories,
            "gq": gq,
            "backups": _list_backups(),
            "schedule": _load_schedule(),
        },
    )


@login_required
def manager_contact_visibility_view(request, user_id: int):
    current_profile = getattr(request.user, "profile", None)
    can_approve = bool(
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
        or getattr(current_profile, "is_manager", False)
    )
    if not can_approve:
        messages.warning(request, "شما به این بخش دسترسی ندارید.")
        return redirect("profile")

    target_user = get_object_or_404(User, id=user_id)
    target_profile = getattr(target_user, "profile", None)
    if not target_profile:
        target_profile = Profile.objects.create(user=target_user)

    viewer_users = (
        User.objects
        .select_related("profile")
        .filter(
            Q(is_staff=True)
            | Q(is_superuser=True)
            | Q(profile__is_manager=True)
            | Q(profile__approved=True)
        )
        .distinct()
        .order_by("username")
    )
    categories = ContactCategory.objects.order_by("name")

    if request.method == "POST":
        mode = (request.POST.get("visibility_mode") or "").strip().lower()
        if mode not in (Profile.CONTACT_VISIBILITY_ALL, Profile.CONTACT_VISIBILITY_SELECTED):
            mode = Profile.CONTACT_VISIBILITY_ALL

        allowed_ids = []
        for raw in request.POST.getlist("visible_to"):
            if str(raw).isdigit():
                allowed_ids.append(int(raw))

        allowed_category_ids = []
        for raw in request.POST.getlist("visible_categories"):
            if str(raw).isdigit():
                allowed_category_ids.append(int(raw))

        target_profile.contact_visibility_mode = mode
        target_profile.save(update_fields=["contact_visibility_mode"])
        if mode == Profile.CONTACT_VISIBILITY_SELECTED:
            target_profile.contact_visible_to.set(User.objects.filter(id__in=allowed_ids))
            target_profile.contact_visible_categories.set(ContactCategory.objects.filter(id__in=allowed_category_ids))
        else:
            target_profile.contact_visible_to.clear()
            target_profile.contact_visible_categories.clear()

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)("online-status", {"type": "online_status_handler"})

        messages.success(request, f"نمایش مخاطب @{target_user.username} به‌روزرسانی شد.")
        return redirect("profile-manager-contact-visibility", user_id=target_user.id)

    selected_ids = set(target_profile.contact_visible_to.values_list("id", flat=True))
    selected_category_ids = set(target_profile.contact_visible_categories.values_list("id", flat=True))
    return render(
        request,
        "a_users/manager.html",
        {
            "target_profile": target_profile,
            "viewer_users": viewer_users,
            "categories": categories,
            "selected_ids": selected_ids,
            "selected_category_ids": selected_category_ids,
        },
    )


@login_required
def manager_contact_category_view(request, category_id: int):
    current_profile = getattr(request.user, "profile", None)
    can_approve = bool(
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
        or getattr(current_profile, "is_manager", False)
    )
    if not can_approve:
        messages.warning(request, "شما به این بخش دسترسی ندارید.")
        return redirect("profile")

    category = get_object_or_404(ContactCategory, id=category_id)

    viewer_users = (
        User.objects
        .select_related("profile")
        .filter(
            Q(is_staff=True)
            | Q(is_superuser=True)
            | Q(profile__is_manager=True)
            | Q(profile__approved=True)
        )
        .distinct()
        .order_by("username")
    )

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        if action == "delete_category":
            category.delete()
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)("online-status", {"type": "online_status_handler"})
            messages.success(request, "دسته‌بندی حذف شد.")
            return redirect("profile-manager")

        name = (request.POST.get("category_name") or "").strip()
        if name and name != category.name:
            category.name = name
            category.save(update_fields=["name"])

        member_ids = []
        for raw in request.POST.getlist("members"):
            if str(raw).isdigit():
                member_ids.append(int(raw))
        category.members.set(User.objects.filter(id__in=member_ids))

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)("online-status", {"type": "online_status_handler"})

        messages.success(request, "اعضای دسته‌بندی به‌روزرسانی شد.")
        return redirect("profile-manager-contact-category", category_id=category.id)

    selected_member_ids = set(category.members.values_list("id", flat=True))
    return render(
        request,
        "a_users/manager.html",
        {
            "contact_category": category,
            "viewer_users": viewer_users,
            "selected_member_ids": selected_member_ids,
        },
    )

# ===== پشتیبان‌گیری: ذخیره روی سرور + نوار پیشرفت + بکاپ خودکار =====

BACKUP_JOBS = {}
BACKUP_JOBS_LOCK = threading.Lock()

# وقتی بازگردانی در حال اجرا است، زمان‌بند خودکار متوقف می‌ماند
RESTORE_IN_PROGRESS = False

_SCHED_STARTED = False
_SCHED_LOCK = threading.Lock()

AUTO_BACKUP_PREFIX = "messenger-autobackup"
MANUAL_BACKUP_PREFIX = "messenger-backup"


def _backup_root():
    root = Path(settings.BASE_DIR) / "backups"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _human_size(num):
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def _safe_backup_path(name):
    base = os.path.basename(name or "")
    if not base or base != (name or "") or base.startswith("."):
        return None
    if not (base.endswith(".json") or base.endswith(".zip")):
        return None
    root = _backup_root()
    path = (root / base).resolve()
    if path.parent != root.resolve():
        return None
    if not path.is_file():
        return None
    return path


def _list_backups():
    root = _backup_root()
    items = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return items
    for entry in entries:
        if not entry.is_file():
            continue
        if not (entry.name.endswith(".json") or entry.name.endswith(".zip")):
            continue
        if entry.name == "schedule.json":
            continue
        try:
            st = entry.stat()
        except OSError:
            continue
        created = dt_module.datetime.fromtimestamp(
            st.st_mtime, tz=timezone.get_current_timezone()
        )
        items.append(
            {
                "name": entry.name,
                "size": _human_size(st.st_size),
                "size_bytes": st.st_size,
                "created": created,
                "is_zip": entry.name.endswith(".zip"),
                "is_auto": entry.name.startswith(AUTO_BACKUP_PREFIX),
            }
        )
    items.sort(key=lambda x: x["created"], reverse=True)
    return items


def _build_backup(include_media, prefix, on_progress=None):
    """ساخت فایل بکاپ روی سرور و بازگرداندن (نام فایل، حجم)."""
    def progress(p):
        if on_progress:
            on_progress(p)

    out = io.StringIO()
    call_command(
        "dumpdata",
        stdout=out,
        indent=2,
        use_natural_foreign_keys=True,
        use_natural_primary_keys=True,
        exclude=["sessions", "admin.logentry"],
    )
    data_bytes = out.getvalue().encode("utf-8")
    root = _backup_root()
    ts = timezone.localtime().strftime("%Y%m%d-%H%M%S")

    if not include_media:
        filename = f"{prefix}-{ts}.json"
        tmp_path = root / (filename + ".part")
        with open(tmp_path, "wb") as f:
            f.write(data_bytes)
        os.replace(tmp_path, root / filename)
        progress(100)
        return filename, (root / filename).stat().st_size

    filename = f"{prefix}-{ts}.zip"
    tmp_path = root / (filename + ".part")

    media_root = getattr(settings, "MEDIA_ROOT", "")
    files_list = []
    total_bytes = 0
    if media_root and os.path.isdir(media_root):
        for dirpath, _dirs, files in os.walk(media_root):
            for name in files:
                fp = os.path.join(dirpath, name)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue
                files_list.append((fp, sz))
                total_bytes += sz

    grand_total = (total_bytes + len(data_bytes)) or 1
    written = 0
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("data.json", data_bytes)
            written += len(data_bytes)
            progress(max(1, int(written * 100 / grand_total)))

            for fp, sz in files_list:
                rel = os.path.relpath(fp, media_root)
                arc = os.path.join("media", rel)
                try:
                    zf.write(fp, arc)
                except (OSError, ValueError):
                    pass
                written += sz
                progress(min(99, int(written * 100 / grand_total)))
        os.replace(tmp_path, root / filename)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise

    progress(100)
    return filename, (root / filename).stat().st_size


def _apply_retention(keep, prefix=AUTO_BACKUP_PREFIX):
    """فقط بکاپ‌های خودکار را نگه می‌دارد؛ قدیمی‌ترها حذف می‌شوند."""
    try:
        keep = int(keep)
    except (TypeError, ValueError):
        return
    if keep <= 0:
        return
    root = _backup_root()
    autos = []
    try:
        for entry in root.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.startswith(prefix):
                continue
            if not (entry.name.endswith(".json") or entry.name.endswith(".zip")):
                continue
            try:
                autos.append((entry, entry.stat().st_mtime))
            except OSError:
                pass
    except OSError:
        return
    autos.sort(key=lambda x: x[1], reverse=True)
    for entry, _mtime in autos[keep:]:
        try:
            os.remove(entry)
        except Exception:
            pass


def _run_backup_job(job_id, include_media):
    def set_progress(**kw):
        with BACKUP_JOBS_LOCK:
            job = BACKUP_JOBS.get(job_id, {})
            job.update(kw)
            BACKUP_JOBS[job_id] = job

    try:
        set_progress(status="running", percent=1)
        filename, size = _build_backup(
            include_media,
            MANUAL_BACKUP_PREFIX,
            on_progress=lambda p: set_progress(percent=p),
        )
        set_progress(status="done", percent=100, filename=filename, size=_human_size(size))
    except Exception:
        set_progress(status="error", error="ساخت بکاپ ناموفق بود.")
    finally:
        try:
            connection.close()
        except Exception:
            pass


# ---------- بکاپ خودکار زمان‌بندی‌شده ----------

def _schedule_path():
    return _backup_root() / "schedule.json"


def _default_schedule():
    return {
        "enabled": False,
        "frequency": "daily",
        "hour": 3,
        "minute": 0,
        "weekday": 5,
        "day_of_month": 1,
        "include_media": False,
        "keep": 7,
        "last_run": None,
    }


def _load_schedule():
    cfg = _default_schedule()
    path = _schedule_path()
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in cfg:
                    if k in data:
                        cfg[k] = data[k]
        except Exception:
            pass
    return cfg


def _save_schedule(cfg):
    path = _schedule_path()
    tmp_path = str(path) + ".part"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _parse_schedule_dt(value):
    if not value:
        return None
    try:
        parsed = dt_module.datetime.fromisoformat(value)
    except Exception:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _recent_occurrence(cfg, now):
    """آخرین زمان مقرر که کوچکتر یا مساوی now است، یا None."""
    try:
        hour = int(cfg.get("hour", 3))
        minute = int(cfg.get("minute", 0))
    except (TypeError, ValueError):
        hour, minute = 3, 0
    freq = cfg.get("frequency", "daily")
    base = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if freq == "daily":
        cand = base
        if cand > now:
            cand -= dt_module.timedelta(days=1)
        return cand

    if freq == "weekly":
        try:
            target = int(cfg.get("weekday", 5))
        except (TypeError, ValueError):
            target = 5
        target = max(0, min(6, target))
        delta = (now.weekday() - target) % 7
        cand = base - dt_module.timedelta(days=delta)
        if cand > now:
            cand -= dt_module.timedelta(days=7)
        return cand

    if freq == "monthly":
        try:
            dom = int(cfg.get("day_of_month", 1))
        except (TypeError, ValueError):
            dom = 1
        dom = max(1, min(28, dom))
        cand = base.replace(day=dom)
        if cand > now:
            first_of_month = base.replace(day=1)
            last_of_prev = first_of_month - dt_module.timedelta(days=1)
            cand = last_of_prev.replace(
                day=dom, hour=hour, minute=minute, second=0, microsecond=0
            )
        return cand

    return None


def _scheduler_loop():
    while True:
        try:
            if not RESTORE_IN_PROGRESS:
                cfg = _load_schedule()
                if cfg.get("enabled"):
                    now = timezone.localtime()
                    cand = _recent_occurrence(cfg, now)
                    if cand is not None:
                        last_dt = _parse_schedule_dt(cfg.get("last_run"))
                        if last_dt is None:
                            # اولین بار که زمان‌بند این تنظیم را می‌بیند: فقط از این لحظه به بعد را حساب کن، بکاپ گذشته نگیر
                            cfg["last_run"] = now.isoformat()
                            try:
                                _save_schedule(cfg)
                            except Exception:
                                pass
                        elif last_dt < cand:
                            # قبل از شروع ساخت، نوبت را ثبت کن تا اگر ساخت طول کشید بکاپ تکراری ساخته نشود
                            cfg["last_run"] = cand.isoformat()
                            try:
                                _save_schedule(cfg)
                            except Exception:
                                pass
                            try:
                                _build_backup(
                                    bool(cfg.get("include_media")), AUTO_BACKUP_PREFIX
                                )
                                _apply_retention(cfg.get("keep", 7))
                            except Exception:
                                pass
                            finally:
                                try:
                                    connection.close()
                                except Exception:
                                    pass
        except Exception:
            pass
        time.sleep(45)


def _ensure_scheduler():
    global _SCHED_STARTED
    with _SCHED_LOCK:
        if _SCHED_STARTED:
            return
        _SCHED_STARTED = True
        thread = threading.Thread(target=_scheduler_loop, daemon=True)
        thread.start()


@login_required
@require_http_methods(["POST"])
def manager_backup_start_view(request):
    if not _user_can_open_manager_panel(request.user):
        return JsonResponse({"ok": False, "error": "شما به این بخش دسترسی ندارید."}, status=403)

    include_media = request.POST.get("include_media") == "1"
    job_id = uuid.uuid4().hex
    with BACKUP_JOBS_LOCK:
        BACKUP_JOBS[job_id] = {"status": "queued", "percent": 0}

    thread = threading.Thread(target=_run_backup_job, args=(job_id, include_media), daemon=True)
    thread.start()
    return JsonResponse({"ok": True, "job_id": job_id})


@login_required
@require_http_methods(["GET"])
def manager_backup_progress_view(request, job_id):
    if not _user_can_open_manager_panel(request.user):
        return JsonResponse({"ok": False, "error": "شما به این بخش دسترسی ندارید."}, status=403)

    with BACKUP_JOBS_LOCK:
        job = dict(BACKUP_JOBS.get(job_id) or {})

    if not job:
        return JsonResponse({"ok": False, "error": "یافت نشد."}, status=404)

    payload = {"ok": True}
    payload.update(job)
    return JsonResponse(payload)


@login_required
@require_http_methods(["GET"])
def manager_backup_download_view(request, name):
    if not _user_can_open_manager_panel(request.user):
        messages.warning(request, "شما به این بخش دسترسی ندارید.")
        return redirect("profile")

    path = _safe_backup_path(name)
    if not path:
        raise Http404("فایل بکاپ یافت نشد.")

    ctype = "application/zip" if path.name.endswith(".zip") else "application/json; charset=utf-8"
    response = FileResponse(open(path, "rb"), content_type=ctype)
    response["Content-Disposition"] = f'attachment; filename="{path.name}"'
    return response


@login_required
@require_http_methods(["POST"])
def manager_backup_delete_view(request):
    if not _user_can_open_manager_panel(request.user):
        messages.warning(request, "شما به این بخش دسترسی ندارید.")
        return redirect("profile")

    path = _safe_backup_path(request.POST.get("name"))
    if not path:
        messages.warning(request, "فایل بکاپ یافت نشد.")
        return redirect("profile-manager")
    try:
        os.remove(path)
        messages.success(request, "فایل بکاپ حذف شد.")
    except Exception:
        messages.error(request, "حذف فایل بکاپ ناموفق بود.")
    return redirect("profile-manager")


@login_required
@require_http_methods(["POST"])
def manager_backup_schedule_view(request):
    if not _user_can_open_manager_panel(request.user):
        messages.warning(request, "شما به این بخش دسترسی ندارید.")
        return redirect("profile")

    def clamp(value, lo, hi, default):
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, n))

    cfg = _load_schedule()
    cfg["enabled"] = request.POST.get("enabled") == "on"
    freq = request.POST.get("frequency", "daily")
    if freq not in ("daily", "weekly", "monthly"):
        freq = "daily"
    cfg["frequency"] = freq
    cfg["hour"] = clamp(request.POST.get("hour"), 0, 23, 3)
    cfg["minute"] = clamp(request.POST.get("minute"), 0, 59, 0)
    cfg["weekday"] = clamp(request.POST.get("weekday"), 0, 6, 5)
    cfg["day_of_month"] = clamp(request.POST.get("day_of_month"), 1, 28, 1)
    cfg["include_media"] = request.POST.get("include_media") == "on"
    cfg["keep"] = clamp(request.POST.get("keep"), 1, 100, 7)
    # با ذخیره تنظیمات، مبدأ زمان را روی الان قرار می‌دهیم تا بکاپ زودهنگام گرفته نشود
    cfg["last_run"] = timezone.localtime().isoformat()
    _save_schedule(cfg)
    _ensure_scheduler()
    messages.success(request, "تنظیمات بکاپ خودکار ذخیره شد.")
    return redirect("profile-manager")


@login_required
@require_http_methods(["GET"])
def manager_backup_view(request):
    if not _user_can_open_manager_panel(request.user):
        messages.warning(request, "شما به این بخش دسترسی ندارید.")
        return redirect("profile")

    out = io.StringIO()
    call_command(
        "dumpdata",
        stdout=out,
        indent=2,
        use_natural_foreign_keys=True,
        use_natural_primary_keys=True,
        exclude=["sessions", "admin.logentry"],
    )
    data_json = out.getvalue()

    ts = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    filename = f"messenger-backup-{ts}.json"
    response = HttpResponse(data_json, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class _RestoreError(Exception):
    pass


def _restore_from_path(work_path, temp_json_path):
    if zipfile.is_zipfile(work_path):
        with zipfile.ZipFile(work_path, "r") as zf:
            names = zf.namelist()
            if "data.json" not in names:
                raise _RestoreError("فایل بکاپ معتبر نیست (data.json یافت نشد).")

            with open(temp_json_path, "wb") as jf:
                jf.write(zf.read("data.json"))

            call_command("flush", interactive=False, verbosity=0)
            call_command("loaddata", temp_json_path, verbosity=0)

            media_root = getattr(settings, "MEDIA_ROOT", "")
            if media_root:
                media_abs = os.path.abspath(media_root)
                for member in names:
                    if not member.startswith("media/") or member.endswith("/"):
                        continue
                    rel_path = member[len("media/"):]
                    if not rel_path:
                        continue
                    dest_abs = os.path.abspath(os.path.join(media_root, rel_path))
                    if dest_abs != media_abs and not dest_abs.startswith(media_abs + os.sep):
                        continue
                    os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
                    with zf.open(member) as src_f, open(dest_abs, "wb") as dst_f:
                        shutil.copyfileobj(src_f, dst_f)
    else:
        shutil.copyfile(work_path, temp_json_path)
        call_command("flush", interactive=False, verbosity=0)
        call_command("loaddata", temp_json_path, verbosity=0)


@login_required
@require_http_methods(["POST"])
def manager_restore_view(request):
    global RESTORE_IN_PROGRESS
    if not _user_can_open_manager_panel(request.user):
        messages.warning(request, "شما به این بخش دسترسی ندارید.")
        return redirect("profile")

    confirm_restore = request.POST.get("confirm_restore")
    server_name = (request.POST.get("server_name") or "").strip()
    uploaded = request.FILES.get("backup_file")

    if confirm_restore != "on":
        messages.warning(request, "برای لود بکاپ باید تایید را فعال کنید.")
        return redirect("profile-manager")

    cleanup_paths = []
    RESTORE_IN_PROGRESS = True
    try:
        if server_name:
            src_path = _safe_backup_path(server_name)
            if not src_path:
                messages.warning(request, "فایل بکاپ روی سرور یافت نشد.")
                return redirect("profile-manager")
            work_path = str(src_path)
        elif uploaded:
            temp_dir = tempfile.gettempdir()
            base = f"messenger-restore-{timezone.now().timestamp()}"
            upload_path = os.path.join(temp_dir, f"{base}.upload")
            cleanup_paths.append(upload_path)
            with open(upload_path, "wb") as f:
                for chunk in uploaded.chunks():
                    f.write(chunk)
            work_path = upload_path
        else:
            messages.warning(request, "فایل بکاپ انتخاب نشده است.")
            return redirect("profile-manager")

        temp_dir = tempfile.gettempdir()
        base = f"messenger-restore-{timezone.now().timestamp()}"
        temp_json_path = os.path.join(temp_dir, f"{base}.json")
        cleanup_paths.append(temp_json_path)

        _restore_from_path(work_path, temp_json_path)

        messages.success(request, "بکاپ با موفقیت لود شد. ممکن است نیاز باشد دوباره وارد شوید.")
        return redirect("home")
    except _RestoreError as exc:
        messages.error(request, str(exc))
        return redirect("profile-manager")
    except Exception:
        messages.error(request, "لود بکاپ ناموفق بود.")
        return redirect("profile-manager")
    finally:
        RESTORE_IN_PROGRESS = False
        for p in cleanup_paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


# راه‌اندازی زمان‌بند در زمان بالا آمدن سرور (نه در دستورات مدیریتی)
_MGMT_COMMANDS = (
    "migrate", "makemigrations", "collectstatic", "shell", "dbshell",
    "dumpdata", "loaddata", "flush", "test", "createsuperuser", "showmigrations",
)
if not any(cmd in sys.argv for cmd in _MGMT_COMMANDS):
    try:
        _ensure_scheduler()
    except Exception:
        pass


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
