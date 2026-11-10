from uuid import uuid4

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .cache_keys import RECOMMENDATION_JOB_VERSION_KEY
from .models import Job


@receiver([post_save, post_delete], sender=Job)
def invalidate_job_recommendations(sender, **kwargs):
    cache.set(RECOMMENDATION_JOB_VERSION_KEY, uuid4().hex, timeout=None)
