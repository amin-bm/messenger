import datetime

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("a_rtchat", "0011_groupmessage_forwarded_from"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatstate",
            name="last_read",
            field=models.DateTimeField(default=datetime.datetime(1970, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)),
        ),
    ]

