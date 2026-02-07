from django.db import migrations, models


def approve_existing_profiles(apps, schema_editor):
    Profile = apps.get_model("a_users", "Profile")
    Profile.objects.filter(approved=False).update(approved=True)


class Migration(migrations.Migration):
    dependencies = [
        ("a_users", "0002_profile_phone"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="approved",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(approve_existing_profiles, migrations.RunPython.noop),
    ]
