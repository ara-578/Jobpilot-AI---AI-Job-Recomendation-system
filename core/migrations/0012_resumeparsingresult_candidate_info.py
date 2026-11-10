from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_resumeparsingresult"),
    ]

    operations = [
        migrations.AddField(
            model_name="resumeparsingresult",
            name="candidate_info",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Extracted candidate name, email, phone",
            ),
        ),
    ]
