# This migration was created to ensure the benefits column exists.
# It has been disabled because the column is already present.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0016_job_benefits_alter_job_qualifications_and_more'),
    ]

    operations = []

