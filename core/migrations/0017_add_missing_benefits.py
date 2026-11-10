# This migration has been disabled because the column 'benefits' already exists.
# No operations are needed.

from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ('core', '0016_job_benefits_alter_job_qualifications_and_more'),
    ]
    operations = []
