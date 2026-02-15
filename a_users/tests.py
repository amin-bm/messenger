from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class ManagerPanelPermissionsTests(TestCase):
    def test_manager_can_promote_user_to_manager(self):
        manager = User.objects.create_user(username="manager", password="pass")
        manager.profile.is_manager = True
        manager.profile.approved = True
        manager.profile.save(update_fields=["is_manager", "approved"])

        target = User.objects.create_user(username="target", password="pass")
        self.assertFalse(target.profile.is_manager)

        self.client.login(username="manager", password="pass")
        res = self.client.post(
            reverse("profile-manager"),
            data={"action": "toggle_manager", "user_id": str(target.id)},
        )
        self.assertEqual(res.status_code, 302)

        target.profile.refresh_from_db()
        self.assertTrue(target.profile.is_manager)

    def test_regular_user_cannot_promote_user_to_manager(self):
        regular = User.objects.create_user(username="regular", password="pass")
        target = User.objects.create_user(username="target", password="pass")

        self.client.login(username="regular", password="pass")
        res = self.client.post(
            reverse("profile-manager"),
            data={"action": "toggle_manager", "user_id": str(target.id)},
        )
        self.assertEqual(res.status_code, 302)

        target.profile.refresh_from_db()
        self.assertFalse(target.profile.is_manager)
