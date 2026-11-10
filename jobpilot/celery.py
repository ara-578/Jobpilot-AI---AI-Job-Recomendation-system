import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jobpilot.settings")

app = Celery("jobpilot")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
