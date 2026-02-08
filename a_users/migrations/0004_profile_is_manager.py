from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("a_users", "0003_profile_approved"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="is_manager",
            field=models.BooleanField(default=False),
        ),
    ]

