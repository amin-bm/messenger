from django.urls import path
from a_users.views import *

urlpatterns = [
    path('', profile_view, name="profile"),
    path('edit/', profile_edit_view, name="profile-edit"),
    path('onboarding/', profile_edit_view, name="profile-onboarding"),
    path('settings/', profile_settings_view, name="profile-settings"),
    path('manager/', manager_panel_view, name="profile-manager"),
    path('manager/contacts/<int:user_id>/', manager_contact_visibility_view, name="profile-manager-contact-visibility"),
    path('manager/contact-categories/<int:category_id>/', manager_contact_category_view, name="profile-manager-contact-category"),
    path('manager/backup/', manager_backup_view, name="profile-manager-backup"),
    path('manager/restore/', manager_restore_view, name="profile-manager-restore"),
    path('manager/backup/start/', manager_backup_start_view, name="profile-manager-backup-start"),
    path('manager/backup/progress/<str:job_id>/', manager_backup_progress_view, name="profile-manager-backup-progress"),
    path('manager/backup/download/<str:name>/', manager_backup_download_view, name="profile-manager-backup-download"),
    path('manager/backup/delete/', manager_backup_delete_view, name="profile-manager-backup-delete"),
    path('manager/backup/schedule/', manager_backup_schedule_view, name="profile-manager-backup-schedule"),
    path('emailchange/', profile_emailchange, name="profile-emailchange"),
    path('usernamechange/', profile_usernamechange, name="profile-usernamechange"),
    path('emailverify/', profile_emailverify, name="profile-emailverify"),
    path('delete/', profile_delete_view, name="profile-delete"),
]
