from django.test import TestCase
from django.contrib.auth.models import User

from a_users.models import Profile, ContactCategory
from a_rtchat.consumers import _contact_users_for_user
from a_rtchat.models import ChatGroup, GroupMessage
from django.urls import reverse
from django.utils import timezone
import datetime


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
        self.assertEqual(ids, {user_a.id, user_b.id})

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

    def test_admin_hidden_unless_selected(self):
        viewer = User.objects.create_user(username="viewer", password="pass")
        admin_user = User.objects.create_user(username="admin_user", password="pass")
        admin_user.is_staff = True
        admin_user.save(update_fields=["is_staff"])

        qs = _contact_users_for_user(viewer)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(admin_user.id, ids)

        viewer.profile.contact_visibility_mode = Profile.CONTACT_VISIBILITY_SELECTED
        viewer.profile.save(update_fields=["contact_visibility_mode"])

        qs = _contact_users_for_user(viewer)
        ids = set(qs.values_list("id", flat=True))
        self.assertNotIn(admin_user.id, ids)

        viewer.profile.contact_visible_to.add(admin_user)
        qs2 = _contact_users_for_user(viewer)
        ids2 = set(qs2.values_list("id", flat=True))
        self.assertIn(admin_user.id, ids2)

    def test_manager_sees_everyone(self):
        manager = User.objects.create_user(username="manager", password="pass")
        manager.profile.is_manager = True
        manager.profile.approved = True
        manager.profile.contact_visibility_mode = Profile.CONTACT_VISIBILITY_SELECTED
        manager.profile.contact_visibility_mode = Profile.CONTACT_VISIBILITY_SELECTED
        manager.profile.save(update_fields=["is_manager", "approved", "contact_visibility_mode"])

        u1 = User.objects.create_user(username="u1", password="pass")
        u2 = User.objects.create_user(username="u2", password="pass")
        u2.profile.contact_visibility_mode = Profile.CONTACT_VISIBILITY_SELECTED
        u2.profile.save(update_fields=["contact_visibility_mode"])

        qs = _contact_users_for_user(manager)
        ids = set(qs.values_list("id", flat=True))
        self.assertEqual(ids, {u1.id, u2.id})


class ChatPaginationTests(TestCase):
    def test_chat_loads_older_messages(self):
        u = User.objects.create_user(username="u", password="pass")
        u.profile.approved = True
        u.profile.save(update_fields=["approved"])
        self.client.login(username="u", password="pass")

        g = ChatGroup.objects.create(group_name="g1", group_slug="g1", groupchat_name="G1", admin=u, is_private=False)
        g.members.add(u)

        base = timezone.now() - datetime.timedelta(days=1)
        for i in range(45):
            m = GroupMessage.objects.create(group=g, author=u, body=f"m{i}")
            GroupMessage.objects.filter(id=m.id).update(created=base + datetime.timedelta(seconds=i))

        res = self.client.get(reverse("chatroom", args=["g1"]))
        self.assertEqual(res.status_code, 200)
        msgs = list(res.context["chat_messages"])
        self.assertEqual(len(msgs), 30)
        before_id = msgs[0].id

        older_url = reverse("chat-messages-older", args=["g1"])
        res2 = self.client.get(f"{older_url}?before={before_id}", HTTP_HX_REQUEST="true")
        self.assertEqual(res2.status_code, 200)
        self.assertIn("msg-", res2.content.decode("utf-8"))


class ChatGroupAdminsTests(TestCase):
    def test_group_admin_can_promote_another_member_to_admin(self):
        owner = User.objects.create_user(username="owner", password="pass")
        a1 = User.objects.create_user(username="a1", password="pass")
        a2 = User.objects.create_user(username="a2", password="pass")
        owner.profile.approved = True
        owner.profile.save(update_fields=["approved"])
        a1.profile.approved = True
        a1.profile.save(update_fields=["approved"])
        a2.profile.approved = True
        a2.profile.save(update_fields=["approved"])

        g = ChatGroup.objects.create(group_name="g-admins", group_slug="g-admins", groupchat_name="G", admin=owner, is_private=False)
        g.members.add(owner, a1, a2)
        g.admins.add(owner, a1)

        self.client.login(username="a1", password="pass")
        res = self.client.post(
            reverse("edit-chatroom", args=[g.group_name]),
            data={"groupchat_name": "G", "group_admins": [str(a1.id), str(a2.id)]},
        )
        self.assertEqual(res.status_code, 302)

        g.refresh_from_db()
        self.assertTrue(g.admins.filter(id=a2.id).exists())

        self.client.logout()
        self.client.login(username="a2", password="pass")
        res2 = self.client.get(reverse("chatroom", args=[g.group_slug]))
        self.assertEqual(res2.status_code, 200)
        self.assertIn(reverse("edit-chatroom", args=[g.group_name]), res2.content.decode("utf-8"))

    def test_non_admin_cannot_access_edit_page(self):
        owner = User.objects.create_user(username="owner", password="pass")
        u = User.objects.create_user(username="u", password="pass")
        owner.profile.approved = True
        owner.profile.save(update_fields=["approved"])
        u.profile.approved = True
        u.profile.save(update_fields=["approved"])
        g = ChatGroup.objects.create(group_name="g-edit", group_slug="g-edit", groupchat_name="G", admin=owner, is_private=False)
        g.members.add(owner, u)
        g.admins.add(owner)

        self.client.login(username="u", password="pass")
        res = self.client.get(reverse("edit-chatroom", args=[g.group_name]))
        self.assertEqual(res.status_code, 404)

    def test_removing_admin_member_also_removes_admin_role(self):
        owner = User.objects.create_user(username="owner", password="pass")
        a1 = User.objects.create_user(username="a1", password="pass")
        owner.profile.approved = True
        owner.profile.save(update_fields=["approved"])
        a1.profile.approved = True
        a1.profile.save(update_fields=["approved"])
        g = ChatGroup.objects.create(group_name="g-remove", group_slug="g-remove", groupchat_name="G", admin=owner, is_private=False)
        g.members.add(owner, a1)
        g.admins.add(owner, a1)

        self.client.login(username="owner", password="pass")
        res = self.client.post(
            reverse("edit-chatroom", args=[g.group_name]),
            data={"groupchat_name": "G", "remove_members": [str(a1.id)]},
        )
        self.assertEqual(res.status_code, 302)

        g.refresh_from_db()
        self.assertFalse(g.members.filter(id=a1.id).exists())
        self.assertFalse(g.admins.filter(id=a1.id).exists())

    def test_only_admin_cannot_leave_group_chat(self):
        owner = User.objects.create_user(username="owner", password="pass")
        a1 = User.objects.create_user(username="a1", password="pass")
        owner.profile.approved = True
        owner.profile.save(update_fields=["approved"])
        a1.profile.approved = True
        a1.profile.save(update_fields=["approved"])

        g = ChatGroup.objects.create(group_name="g-leave-1", group_slug="g-leave-1", groupchat_name="G", admin=owner, is_private=False)
        g.members.add(owner, a1)
        g.admins.add(owner)

        self.client.login(username="owner", password="pass")
        res = self.client.post(reverse("chatroom-leave", args=[g.group_name]))
        self.assertEqual(res.status_code, 302)

        g.refresh_from_db()
        self.assertTrue(g.members.filter(id=owner.id).exists())
        self.assertEqual(g.admin_id, owner.id)

    def test_owner_can_leave_after_promoting_another_admin(self):
        owner = User.objects.create_user(username="owner", password="pass")
        a1 = User.objects.create_user(username="a1", password="pass")
        owner.profile.approved = True
        owner.profile.save(update_fields=["approved"])
        a1.profile.approved = True
        a1.profile.save(update_fields=["approved"])

        g = ChatGroup.objects.create(group_name="g-leave-2", group_slug="g-leave-2", groupchat_name="G", admin=owner, is_private=False)
        g.members.add(owner, a1)
        g.admins.add(owner, a1)

        self.client.login(username="owner", password="pass")
        res = self.client.post(reverse("chatroom-leave", args=[g.group_name]))
        self.assertEqual(res.status_code, 302)

        g.refresh_from_db()
        self.assertFalse(g.members.filter(id=owner.id).exists())
        self.assertFalse(g.admins.filter(id=owner.id).exists())
        self.assertEqual(g.admin_id, a1.id)
