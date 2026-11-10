from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ('core', '0007_add_sample_it_jobs_india'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='job',
            options={
                'indexes': [
                    models.Index(fields=['title'], name='idx_job_title'),
                    models.Index(fields=['category'], name='idx_job_category'),
                    models.Index(fields=['is_active'], name='idx_job_active'),
                ],
                'unique_together': {('title', 'company', 'location')},
            },
        ),
    ]

