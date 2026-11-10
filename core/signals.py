from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Resume
from .resume_parser import parse_resume, persist_parsing_result

@receiver(post_save, sender=Resume)
def parse_resume_on_save(sender, instance, created, **kwargs):
    """Parse the resume whenever a new Resume object is created.
    If parsing fails, the exception is stored as a warning in the associated
    ``ResumeParsingResult`` so the UI can display a clear error.
    """
    if not created:
        return
    try:
        parsed = parse_resume(instance)
    except Exception as exc:
        from .models import ResumeParsingResult
        ResumeParsingResult.objects.update_or_create(
            resume=instance,
            defaults={"parsing_warnings": [str(exc)]},
        )
    else:
        persist_parsing_result(instance, parsed)
