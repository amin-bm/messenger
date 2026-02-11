from django.test import TestCase
from django.contrib.auth.models import User

from a_users.models import Profile, ContactCategory
from a_rtchat.consumers import _contact_users_for_user


class ContactVisibilityTests(TestCase):
    def test_filtered_user_sees_only_users_who_can_see_them(self):
        viewer = User.objects.create_user(username="viewer", password="pass")
        user_a = User.objects.create_user(username="a", password="pass")
        user_b = User.objects.create_user(username="b", password="pass")
        manager = User.objects.create_user(username="manager", password="pass")
        manager.profile.is_manager = True
        manager.profile.approved = True
        manager.profile.save(update_fields=["is_manager", "approved"])

        viewer.profile.contact_visibility_mode = Profile.CONTACT_VISIBILITY_SELECTED
        viewer.profile.save(update_fields=["contact_visibility_mode"])
        viewer.profile.contact_visible_to.set([user_a])
        cat = ContactCategory.objects.create(name="employees")
        cat.members.add(user_b)
        viewer.profile.contact_visible_categories.add(cat)

        qs = _contact_users_for_user(viewer)
        ids = set(qs.values_list("id", flat=True))
        self.assertEqual(ids, {user_a.id, user_b.id, manager.id})

    def test_normal_user_sees_only_users_who_allow_visibility(self):
        viewer = User.objects.create_user(username="viewer", password="pass")
        user_all = User.objects.create_user(username="all", password="pass")
        user_selected_yes = User.objects.create_user(username="sy", password="pass")
        user_selected_no = User.objects.create_user(username="sn", password="pass")
        user_selected_cat = User.objects.create_user(username="sc", password="pass")

        user_selected_yes.profile.contact_visibility_mode = Profile.CONTACT_VISIBILITY_SELECTED
        user_selected_yes.profile.save(update_fields=["contact_visibility_mode"])
        user_selected_yes.profile.contact_visible_to.set([viewer])

        user_selected_no.profile.contact_visibility_mode = Profile.CONTACT_VISIBILITY_SELECTED
        user_selected_no.profile.save(update_fields=["contact_visibility_mode"])

        cat = ContactCategory.objects.create(name="warehouse")
        cat.members.add(viewer)
        user_selected_cat.profile.contact_visibility_mode = Profile.CONTACT_VISIBILITY_SELECTED
        user_selected_cat.profile.save(update_fields=["contact_visibility_mode"])
        user_selected_cat.profile.contact_visible_categories.add(cat)

        qs = _contact_users_for_user(viewer)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(user_all.id, ids)
        self.assertIn(user_selected_yes.id, ids)
        self.assertNotIn(user_selected_no.id, ids)
        self.assertIn(user_selected_cat.id, ids)

    def test_manager_sees_everyone(self):
        manager = User.objects.create_user(username="manager", password="pass")
        manager.profile.is_manager = True
        manager.profile.approved = True
        manager.profile.contact_visibility_mode = Profile.CONTACT_VISIBILITY_SELECTED
        manager.profile.save(update_fields=["is_manager", "approved", "contact_visibility_mode"])

        u1 = User.objects.create_user(username="u1", password="pass")
        u2 = User.objects.create_user(username="u2", password="pass")
        u2.profile.contact_visibility_mode = Profile.CONTACT_VISIBILITY_SELECTED
        u2.profile.save(update_fields=["contact_visibility_mode"])

        qs = _contact_users_for_user(manager)
        ids = set(qs.values_list("id", flat=True))
        self.assertEqual(ids, {u1.id, u2.id})
