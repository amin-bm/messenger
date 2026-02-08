from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("a_users", "0004_profile_is_manager"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="last_seen",
            field=models.DateTimeField(db_index=True, default=django.utils.timezone.now),
        ),
    ]

