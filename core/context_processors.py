from .models import UserProfile
from django.db.models import Count, Max, Q


def user_role(request):
    if not request.user.is_authenticated:
        return {"user_role": "", "unread_notification_count": 0}
    try:
        role = request.user.profile.role
    except UserProfile.DoesNotExist:
        role = ""
    unread_count = 0
    cursor = 0
    if role in {"seeker", "recruiter"}:
        notification_stats = request.user.notifications.aggregate(
            unread_count=Count("pk", filter=Q(read_at__isnull=True)),
            max_id=Max("pk"),
        )
        unread_count = notification_stats["unread_count"]
        cursor = notification_stats["max_id"] or 0
    return {
        "user_role": role,
        "unread_notification_count": unread_count,
        "notification_cursor": cursor,
    }
