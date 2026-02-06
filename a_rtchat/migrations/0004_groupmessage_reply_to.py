from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("a_rtchat", "0003_alter_chatgroup_group_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="groupmessage",
            name="reply_to",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replies",
                to="a_rtchat.groupmessage",
            ),
        ),
    ]
