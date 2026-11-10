from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import (
    UserProfile, Resume, Job, Application, SavedJob, ApplicationStatusHistory,
    JobRecommendationFeedback, ResumeParsingResult, ResumeBuilder,
    InterviewSchedule, Notification, RecentlyViewedJob,
)
from .application_workflow import RECRUITER_STATUS_TRANSITIONS
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user","role","location","company_name")
    list_filter = ("role",)
    search_fields = ("user__username","user__email","company_name")
@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ("user","predicted_category","ats_score","uploaded_at")
    search_fields = ("user__username","predicted_category")
@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title","company","category","location","status","is_active","created_at")
    list_filter = ("status","is_active","category","employment_type","experience_level")
    search_fields = ("title","company","required_skills","preferred_skills","description")
    readonly_fields = ("posted_date","updated_at","is_active")
    fieldsets = (
        (None, {"fields": ("recruiter", ("title", "company"), "description")}),
        ("Skills and qualifications", {"fields": (
            "required_skills", "preferred_skills", "category", "experience_level",
            "required_experience_years", "max_experience_years", "education_requirements",
            "qualifications", "responsibilities", "benefits",
        )}),
        ("Location and compensation", {"fields": (
            "location", "industry", "employment_type", "work_mode", "salary",
        )}),
        ("Application and availability", {"fields": (
            "company_logo", "application_url", "deadline", "status", "is_active",
        )}),
        ("Timestamps", {"fields": ("posted_date", "updated_at")}),
    )

    @admin.display(description="Posted date")
    def posted_date(self, obj):
        return obj.created_at

    def save_model(self, request, obj, form, change):
        obj.is_active = obj.status == "open"
        super().save_model(request, obj, form, change)
@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("display_title","display_company","applicant","source","match_score","status","created_at")
    list_filter = ("source","status")
    search_fields = ("job_title_snapshot","job_company_snapshot","job__title","applicant__username")

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is None:
            readonly.append("status")
        return tuple(readonly)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj is not None and "status" in form.base_fields:
            allowed = RECRUITER_STATUS_TRANSITIONS.get(obj.status, set()) | {
                obj.status
            }
            form.base_fields["status"].choices = [
                (value, label)
                for value, label in Application.STATUS_CHOICES
                if value in allowed
            ]
        return form

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            previous = (
                Application.objects.select_for_update().get(pk=obj.pk)
                if change else None
            )
            previous_status = previous.status if previous else ""
            if previous and obj.status != previous_status:
                allowed = RECRUITER_STATUS_TRANSITIONS.get(previous_status, set())
                if obj.status not in allowed:
                    raise ValidationError(
                        f"Cannot change application from {previous_status} "
                        f"to {obj.status}."
                    )
            elif not previous and obj.status != "submitted":
                raise ValidationError(
                    "New applications must start in the Applied status."
                )
            if obj.status == "interview_scheduled" and (
                not obj.interview_at or not obj.interview_mode
            ):
                raise ValidationError(
                    "An interview date and mode are required when scheduling."
                )
            if (
                obj.status == "interview_scheduled"
                and obj.interview_mode == "video"
                and not obj.interview_link
            ):
                raise ValidationError(
                    "A meeting link is required for video interviews."
                )

            super().save_model(request, obj, form, change)
            if previous is None or obj.status != previous_status:
                ApplicationStatusHistory.objects.create(
                    application=obj,
                    status=obj.status,
                    previous_status=previous_status,
                    changed_by=request.user,
                    description=(
                        "Application created by an administrator."
                        if previous is None
                        else (
                            f"Administrator changed status from "
                            f"{dict(Application.STATUS_CHOICES).get(previous_status)} "
                            f"to {obj.get_status_display()}."
                        )
                    ),
                    recruiter_remarks="",
                )
                if previous is not None:
                    if obj.status == "interview_scheduled":
                        InterviewSchedule.objects.create(
                            application=obj,
                            scheduled_by=request.user,
                            starts_at=obj.interview_at,
                            mode=obj.interview_mode,
                            meeting_link=obj.interview_link,
                            details=obj.interview_details,
                        )
                    Notification.objects.create(
                        recipient=obj.applicant,
                        application=obj,
                        title=f"Application update: {obj.display_title}",
                        message=f"Your application status is now {obj.get_status_display()}.",
                    )
@admin.register(SavedJob)
class SavedJobAdmin(admin.ModelAdmin):
    list_display = ("user","job","created_at")
    search_fields = ("user__username","job__title","job__company")


@admin.register(JobRecommendationFeedback)
class JobRecommendationFeedbackAdmin(admin.ModelAdmin):
    list_display = ("user", "source", "source_key", "is_relevant", "updated_at")
    list_filter = ("source", "is_relevant")
    search_fields = ("user__username", "source_key", "job__title", "job__company")
    readonly_fields = ("updated_at",)


@admin.register(ApplicationStatusHistory)
class ApplicationStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("application","status","changed_by","changed_at")
    list_filter = ("status",)
    search_fields = ("application__reference","application__applicant__username")
    readonly_fields = (
        "application", "previous_status", "status", "changed_by", "changed_at",
        "description", "recruiter_remarks",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ResumeParsingResult)
class ResumeParsingResultAdmin(admin.ModelAdmin):
    list_display = ("resume", "parsed_at")
    search_fields = ("resume__user__username", "resume__user__email")
    readonly_fields = ("parsed_at",)


@admin.register(ResumeBuilder)
class ResumeBuilderAdmin(admin.ModelAdmin):
    list_display = ("full_name", "user", "target_job_title", "updated_at")
    search_fields = ("full_name", "email", "user__username", "target_job_title")
    readonly_fields = ("created_at", "updated_at")


@admin.register(InterviewSchedule)
class InterviewScheduleAdmin(admin.ModelAdmin):
    list_display = ("application", "starts_at", "mode", "scheduled_by")
    list_filter = ("mode",)
    search_fields = ("application__reference", "application__applicant__username")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "recipient", "application", "job", "created_at", "read_at")
    list_filter = ("read_at", "created_at")
    search_fields = ("title", "message", "recipient__username")


@admin.register(RecentlyViewedJob)
class RecentlyViewedJobAdmin(admin.ModelAdmin):
    list_display = ("user", "job", "viewed_at")
    list_select_related = ("user", "job")
    readonly_fields = ("user", "job", "viewed_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
