from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("a_users", "0006_pushsubscription"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="contact_visibility_mode",
            field=models.CharField(
                choices=[("all", "All"), ("selected", "Selected")],
                default="all",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="contact_visible_to",
            field=models.ManyToManyField(blank=True, related_name="contact_visible_to_profiles", to="auth.user"),
        ),
    ]

