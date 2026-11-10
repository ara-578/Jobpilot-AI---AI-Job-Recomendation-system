import json
import logging

from celery import shared_task
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from .models import Job, RecommendationRun

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="core.generate_recommendations")
def generate_recommendations(self, run_id, input_hash):
    task_id = self.request.id or ""
    claimed = RecommendationRun.objects.filter(
        pk=run_id,
        input_hash=input_hash,
        task_id=task_id,
        status="queued",
    ).update(status="processing", updated_at=timezone.now())
    if not claimed:
        return "superseded"

    try:
        from .views import _dashboard_recommendations

        run = RecommendationRun.objects.select_related("user", "resume").get(
            pk=run_id,
            input_hash=input_hash,
            task_id=task_id,
            status="processing",
        )
        recommendations, profile = _dashboard_recommendations(
            run.resume, run.user
        )
        serialized = []
        for recommendation in recommendations:
            item = dict(recommendation)
            job = item.pop("job", None)
            item["job_id"] = job.pk if isinstance(job, Job) else None
            serialized.append(
                json.loads(
                    json.dumps(
                        item,
                        cls=DjangoJSONEncoder,
                        allow_nan=False,
                    )
                )
            )
        serialized_profile = json.loads(
            json.dumps(profile, cls=DjangoJSONEncoder, allow_nan=False)
        )
        updated = RecommendationRun.objects.filter(
            pk=run_id,
            input_hash=input_hash,
            task_id=task_id,
            status="processing",
        ).update(
            status="ready",
            recommendations=serialized,
            profile=serialized_profile,
            error_message="",
            updated_at=timezone.now(),
        )
        return "ready" if updated else "superseded"
    except Exception:
        logger.exception("Recommendation generation failed for run %s.", run_id)
        RecommendationRun.objects.filter(
            pk=run_id,
            input_hash=input_hash,
            task_id=task_id,
            status="processing",
        ).update(
            status="failed",
            error_message=(
                "We couldn't generate recommendations right now. "
                "Please try again."
            ),
            updated_at=timezone.now(),
        )
        return "failed"
