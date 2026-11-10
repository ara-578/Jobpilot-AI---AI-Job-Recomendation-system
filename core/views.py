import logging
import hashlib
import json
import threading
import uuid
from pathlib import Path
from datetime import timedelta
from urllib.parse import urlsplit
from smtplib import SMTPException
from lxml.etree import XMLSyntaxError
from docx.opc.exceptions import OpcError
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.utils import DatabaseError
from django.db.models import Count, Max, Q
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, Http404, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .models import (
    UserProfile, Resume, Job, Application, ApplicationStatusHistory, SavedJob,
    JobRecommendationFeedback, InterviewSchedule, Notification, ResumeBuilder,
    RecommendationRun, RecentlyViewedJob,
)
from .cache_keys import RECOMMENDATION_JOB_VERSION_KEY
from .forms import (
    RegisterForm, ProfileForm, ResumeForm, ResumeReviewForm, JobForm,
    AdminJobForm, ApplicationForm, ContactForm, ApplicationStatusForm,
    ATSResumeBuilderForm,
)
from .ai_engine import read_resume, extract_resume_sections, extract_resume_skills, extract_skills, extract_education, extract_experience, extract_internship_experience, extract_projects, extract_certifications, predict_category, ats_estimate, ats_breakdown, tips, recommend_jobs_for_text, recommend_candidates_for_job, model_status, search_database_jobs, get_dataset_job, explain_job_match, _record_is_available
from .application_workflow import RECRUITER_STATUS_TRANSITIONS

logger = logging.getLogger(__name__)

WITHDRAWABLE_APPLICATION_STATUSES = {
    "submitted", "resume_viewed", "reviewing", "shortlisted", "interview_scheduled",
}
RECOMMENDATION_CACHE_TIMEOUT = 300
JOB_SEARCH_CACHE_TIMEOUT = 30
RECOMMENDATION_STALE_AFTER = timedelta(minutes=5)
LEGACY_RECOMMENDATION_BROKER_ERROR = (
    "The recommendation service is temporarily unavailable. "
    "Please try again shortly."
)

def role_of(user):
    try: return user.profile.role
    except UserProfile.DoesNotExist: return ""


def _safe_external_url(value):
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return parsed.geturl()


def _job_is_available(job, now):
    return (
        job.is_active
        and job.status == "open"
        and (job.deadline is None or job.deadline > now)
        and (job.expires_at is None or job.expires_at > now)
    )


def _job_list_items(value, split_commas=False):
    if not value:
        return []
    items = []
    for line in str(value).replace("\r", "").splitlines():
        parts = line.split(";")
        if split_commas:
            parts = [part for item in parts for part in item.split(",")]
        items.extend(
            item.strip().lstrip("•*- ").strip()
            for item in parts
            if item.strip().lstrip("•*- ").strip()
        )
    return items


def _database_job_search_record(job):
    return {
        "source": "database",
        "source_label": "Database posting",
        "source_key": str(job.pk),
        "job": job,
        "job_title": job.title,
        "company": job.company,
        "description": job.description,
        "required_skills": job.required_skills,
        "preferred_skills": job.preferred_skills,
        "category": job.category,
        "experience_level": job.experience_level,
        "required_experience_years": job.required_experience_years,
        "max_experience_years": job.max_experience_years,
        "location": job.location,
        "employment_type": job.employment_type,
        "work_mode": job.work_mode,
        "salary": job.salary,
        "created_at": job.created_at,
        "status": job.status,
        "is_active": job.is_active,
        "deadline": job.deadline,
        "expires_at": job.expires_at,
    }


def _job_detail_presentation(job, is_dataset=False):
    if is_dataset:
        get_value = job.get
        company_image = ""
        company_description = ""
        company_industry = job.get("industry", "")
        company_location = job.get("company_location", "")
        company_website = _safe_external_url(job.get("company_website"))
        related_jobs = []
    else:
        get_value = lambda name, default="": getattr(job, name, default)
        company_image = job.company_logo.url if job.company_logo else ""
        try:
            recruiter_profile = job.recruiter.profile
        except UserProfile.DoesNotExist:
            recruiter_profile = None
        matching_company_profile = (
            recruiter_profile
            if recruiter_profile
            and recruiter_profile.company_name.strip().casefold()
            == job.company.strip().casefold()
            else None
        )
        company_description = (
            matching_company_profile.company_description
            if matching_company_profile else ""
        )
        company_industry = job.industry or (
            matching_company_profile.company_industry
            if matching_company_profile else ""
        )
        company_location = (
            matching_company_profile.location if matching_company_profile else ""
        )
        company_website = _safe_external_url(
            matching_company_profile.company_website
            if matching_company_profile else ""
        )
        related_query = published_jobs().exclude(pk=job.pk)
        if job.category:
            related_query = related_query.filter(category__iexact=job.category)
        elif job.industry:
            related_query = related_query.filter(industry__iexact=job.industry)
        else:
            related_query = related_query.none()
        related_jobs = list(
            related_query.only(
                "id", "title", "company", "location", "employment_type",
                "salary", "company_logo",
            )[:3]
        )
    work_mode = get_value("work_mode")
    if not is_dataset and work_mode:
        work_mode = job.get_work_mode_display()
    elif work_mode:
        work_mode = dict(Job.WORK_MODE_CHOICES).get(work_mode, work_mode)
    return {
        "title": get_value("job_title", get_value("title")),
        "company": get_value("company"),
        "logo_url": company_image,
        "company_description": company_description,
        "company_industry": company_industry,
        "company_location": company_location,
        "company_website": company_website,
        "location": get_value("location"),
        "employment_type": get_value("employment_type"),
        "experience_level": get_value("experience_level"),
        "required_experience_years": get_value("required_experience_years"),
        "max_experience_years": get_value("max_experience_years"),
        "salary": get_value("salary"),
        "posted_at": get_value("created_at", get_value("posted_at")),
        "department": get_value("category"),
        "industry": company_industry,
        "work_mode": work_mode,
        "openings": get_value("openings"),
        "description": get_value("description"),
        "responsibilities": _job_list_items(get_value("responsibilities")),
        "required_skills": _job_list_items(
            get_value("required_skills"), split_commas=True
        ),
        "preferred_skills": _job_list_items(
            get_value("preferred_skills"), split_commas=True
        ),
        "education_requirements": _job_list_items(
            get_value("education_requirements")
        ),
        "qualifications": _job_list_items(get_value("qualifications")),
        "benefits": _job_list_items(get_value("benefits")),
        "related_jobs": related_jobs,
    }


def _resume_candidate_profile(resume, user):
    profile = getattr(user, "profile", None)
    return {
        "skills": resume.extracted_skills or [],
        "education": resume.extracted_education or [],
        "experience_years": float(resume.experience_years or 0),
        "internship_experience_years": float(
            resume.internship_experience_years or 0
        ),
        "experience_known": bool(
            resume.experience_years
            or "fresher" in (resume.extracted_text or "").casefold()
            or (profile and profile.experience_years)
        ),
        "projects": resume.extracted_projects or [],
        "certifications": resume.extracted_certifications or [],
        "predicted_category": resume.predicted_category,
        "headline": profile.headline if profile else "",
        "preferred_roles": profile.preferred_roles if profile else "",
        "preferred_locations": profile.preferred_locations if profile else "",
        "preferred_category": profile.preferred_category if profile else "",
        "preferred_employment_type": (
            profile.preferred_employment_type if profile else ""
        ),
        "preferred_work_mode": profile.preferred_work_mode if profile else "",
        "preferred_salary_min": (
            profile.preferred_salary_min if profile else None
        ),
        "preferred_salary_max": (
            profile.preferred_salary_max if profile else None
        ),
    }


def _dashboard_recommendations(resume, user):
    candidate_profile = _resume_candidate_profile(resume, user)
    feedback_signature = tuple(
        JobRecommendationFeedback.objects.filter(user=user)
        .order_by("pk")
        .values_list("source", "source_key", "is_relevant", "updated_at")
    )
    signature = {
        "user_id": user.pk,
        "resume_id": resume.pk,
        "resume_uploaded_at": resume.uploaded_at.isoformat(),
        "resume_text": hashlib.sha256(
            (resume.extracted_text or "").encode("utf-8")
        ).hexdigest(),
        "candidate_profile": candidate_profile,
        "feedback": feedback_signature,
        "job_version": _job_catalog_version(),
    }
    key_digest = hashlib.sha256(
        json.dumps(signature, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    cache_key = f"jobpilot:dashboard-recommendations:v1:{key_digest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = recommend_jobs_for_text(
        resume.extracted_text,
        top_n=50,
        user=user,
        candidate_profile=candidate_profile,
    )
    cache.set(cache_key, result, RECOMMENDATION_CACHE_TIMEOUT)
    return result


def _job_catalog_version():
    latest_job = Job.objects.aggregate(
        count=Count("pk"),
        latest_update=Max("updated_at"),
        latest_id=Max("pk"),
    )
    return ":".join(
        (
            str(latest_job["count"]),
            latest_job["latest_update"].isoformat()
            if latest_job["latest_update"]
            else "",
            str(latest_job["latest_id"] or ""),
        )
    )


def _recommendation_input_signature(resume, user):
    profile = _resume_candidate_profile(resume, user)
    feedback = list(
        JobRecommendationFeedback.objects.filter(user=user)
        .order_by("pk")
        .values_list("source", "source_key", "is_relevant", "updated_at")
    )
    job_version = _job_catalog_version()
    signature = {
        "user_id": user.pk,
        "resume_id": resume.pk,
        "resume_uploaded_at": resume.uploaded_at.isoformat(),
        "resume_text": hashlib.sha256(
            (resume.extracted_text or "").encode("utf-8")
        ).hexdigest(),
        "candidate_profile": profile,
        "feedback": feedback,
        "job_version": job_version,
    }
    digest = hashlib.sha256(
        json.dumps(signature, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return digest, job_version


def _dispatch_recommendation_run(run):
    from .tasks import generate_recommendations

    task_id = uuid.uuid4().hex
    run.task_id = task_id
    run.status = "queued"
    run.error_message = ""
    run.save(update_fields=["task_id", "status", "error_message", "updated_at"])
    try:
        generate_recommendations.apply_async(
            args=(run.pk, run.input_hash),
            task_id=task_id,
        )
    except Exception:
        logger.exception(
            "Could not queue recommendation run %s; using the local worker.",
            run.pk,
        )
        try:
            worker = threading.Thread(
                target=_run_recommendations_locally,
                args=(run.pk, run.input_hash, task_id),
                name=f"jobpilot-recommendations-{run.pk}",
                daemon=True,
            )
            worker.start()
        except Exception:
            logger.exception(
                "Could not start the local recommendation worker for run %s.",
                run.pk,
            )
            RecommendationRun.objects.filter(
                pk=run.pk, input_hash=run.input_hash, task_id=task_id,
                status="queued",
            ).update(
                status="failed",
                task_id="",
                error_message=(
                    "Recommendations could not be started. Please retry."
                ),
                updated_at=timezone.now(),
            )
    finally:
        run.refresh_from_db()


def _run_recommendations_locally(run_id, input_hash, task_id):
    from django.db import close_old_connections
    from .tasks import generate_recommendations

    close_old_connections()
    try:
        generate_recommendations.apply(
            args=(run_id, input_hash),
            task_id=task_id,
        )
    except Exception:
        logger.exception(
            "Local recommendation generation failed for run %s.", run_id
        )
        RecommendationRun.objects.filter(
            pk=run_id,
            input_hash=input_hash,
            task_id=task_id,
            status__in=("queued", "processing"),
        ).update(
            status="failed",
            task_id="",
            error_message="Recommendations could not be generated. Please retry.",
            updated_at=timezone.now(),
        )
    finally:
        close_old_connections()


def _ensure_recommendation_run(resume, user, retry=False):
    input_hash, job_version = _recommendation_input_signature(resume, user)
    should_dispatch = False
    with transaction.atomic():
        run, created = RecommendationRun.objects.select_for_update().get_or_create(
            resume=resume,
            defaults={
                "user": user,
                "input_hash": input_hash,
                "job_version": job_version,
                "status": "queued",
            },
        )
        changed = (
            not created
            and (
                run.input_hash != input_hash
                or run.job_version != job_version
                or (
                    run.status == "failed"
                    and (
                        retry
                        or run.error_message == LEGACY_RECOMMENDATION_BROKER_ERROR
                    )
                )
                or (
                    run.status in {"queued", "processing"}
                    and run.updated_at
                    < timezone.now() - RECOMMENDATION_STALE_AFTER
                )
            )
        )
        if changed:
            run.user = user
            run.input_hash = input_hash
            run.job_version = job_version
            run.status = "queued"
            run.task_id = ""
            run.recommendations = []
            run.profile = {}
            run.error_message = ""
            run.save()
        should_dispatch = created or changed
    if should_dispatch:
        _dispatch_recommendation_run(run)
    return run


def _recommendation_context(run, user):
    recommendations = []
    profile = run.profile if run.status == "ready" else {}
    if run.status != "ready":
        return recommendations, profile

    stored_recommendations = run.recommendations
    job_ids = [
        item.get("job_id")
        for item in stored_recommendations
        if item.get("job_id") is not None
    ]
    jobs = Job.objects.in_bulk(job_ids)
    applied_job_ids = set(
        Application.objects.filter(applicant=user).values_list("job_id", flat=True)
    )
    applied_dataset_keys = set(
        Application.objects.filter(
            applicant=user, source="dataset"
        ).values_list("external_job_key", flat=True)
    )
    saved_job_ids = set(
        SavedJob.objects.filter(
            user=user, job__isnull=False
        ).values_list("job_id", flat=True)
    )
    saved_dataset_keys = set(
        SavedJob.objects.filter(
            user=user, job__isnull=True, source="dataset"
        ).values_list("source_key", flat=True)
    )
    now = timezone.now()
    for stored in stored_recommendations:
        recommendation = dict(stored)
        job_id = recommendation.pop("job_id", None)
        recommendation["job"] = jobs.get(job_id)
        if recommendation["job"] is not None and not _job_is_available(
            recommendation["job"], now
        ):
            continue
        if not _record_is_available(recommendation, now):
            continue
        recommendation["application_url"] = _safe_external_url(
            recommendation.get("application_url")
        )
        job = recommendation["job"]
        recommendation["has_applied"] = (
            job.pk in applied_job_ids
            if job is not None
            else recommendation["source_key"] in applied_dataset_keys
        )
        recommendation["is_saved"] = (
            job.pk in saved_job_ids
            if job is not None
            else recommendation["source_key"] in saved_dataset_keys
        )
        recommendations.append(recommendation)
    return recommendations, profile


def _status_notification(application, status):
    messages_by_status = {
        "resume_viewed": "The recruiter opened your resume.",
        "reviewing": "Your application is now under review.",
        "shortlisted": "You have been shortlisted for this role.",
        "interview_scheduled": "An interview has been scheduled for this role.",
        "selected": "You have been selected for this role.",
        "rejected": "The recruiter has declined your application.",
        "withdrawn": "Your application was withdrawn.",
    }
    message = messages_by_status.get(status)
    if message:
        Notification.objects.create(
            recipient=application.applicant,
            application=application,
            title=f"Application update: {application.display_title}",
            message=message,
        )

def published_jobs():
    return Job.objects.filter(is_active=True).filter(
        Q(status="open")
    ).filter(
        Q(deadline__isnull=True) | Q(deadline__gt=timezone.now())
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    )

def home(request):
    jobs=published_jobs()
    return render(request,"core/home.html",{
        "job_count":jobs.count(),
        "seeker_count":UserProfile.objects.filter(role="seeker",user__is_active=True).count(),
    })
def register(request):
    if request.user.is_authenticated: return redirect("dashboard")
    form=RegisterForm(request.POST or None)
    if request.method=="POST" and form.is_valid():
        user=form.save(); login(request,user); messages.success(request,"Your JobPilot AI account is ready."); return redirect("dashboard")
    return render(request,"core/register.html",{"form":form})
def login_view(request):
    if request.user.is_authenticated: return redirect("dashboard")
    if request.method=="POST":
        user=authenticate(request,username=request.POST.get("username",""),password=request.POST.get("password",""))
        if user:
            login(request,user); return redirect(request.GET.get("next") or "dashboard")
        messages.error(request,"Invalid username or password.")
    return render(request,"core/login.html")
@login_required
@require_POST
def logout_view(request):
    logout(request); return redirect("home")
@login_required
def dashboard(request):
    role=role_of(request.user)
    if request.user.is_staff:
        context={"role":"admin","users":User.objects.count(),"seekers":UserProfile.objects.filter(role="seeker").count(),"recruiters":UserProfile.objects.filter(role="recruiter").count(),"jobs":Job.objects.count(),"applications":Application.objects.count(),"recent_jobs":Job.objects.order_by("-created_at")[:5]}
        return render(request,"core/dashboard_main.html",context)
    if not role:
        UserProfile.objects.get_or_create(user=request.user, defaults={"role":"seeker"})
        role=role_of(request.user)
    if role=="recruiter":
        jobs=Job.objects.filter(recruiter=request.user).annotate(
            application_count=Count("applications")
        ).order_by("-created_at")
        recruiter_applications = Application.objects.filter(
            job__recruiter=request.user
        )
        apps=recruiter_applications.select_related("job","applicant").order_by("-created_at")[:8]
        applications_by_status = {
            status: set()
            for status, _ in Application.STATUS_CHOICES
        }
        for status, application_id in ApplicationStatusHistory.objects.filter(
            application__job__recruiter=request.user
        ).values_list("status", "application_id"):
            applications_by_status.setdefault(status, set()).add(application_id)
        for application_id, status in recruiter_applications.values_list(
            "pk", "status"
        ):
            applications_by_status.setdefault(status, set()).add(application_id)
        status_counts = {
            status: len(application_ids)
            for status, application_ids in applications_by_status.items()
        }
        total_applications = recruiter_applications.count()
        return render(request,"core/dashboard_main.html",{
            "role":role,
            "jobs":jobs,
            "applications":apps,
            "job_count":jobs.count(),
            "open_job_count":jobs.filter(is_active=True).filter(
                Q(expires_at__isnull=True)|Q(expires_at__gt=timezone.now())
            ).count(),
            "application_count":total_applications,
            "shortlisted_count":status_counts.get("shortlisted", 0),
            "interview_count":status_counts.get("interview_scheduled", 0),
            "selected_count":status_counts.get("selected", 0),
            "rejected_count":status_counts.get("rejected", 0),
            "shortlist_rate":round(
                100 * status_counts.get("shortlisted", 0) / total_applications
            ) if total_applications else 0,
            "interview_rate":round(
                100 * status_counts.get("interview_scheduled", 0) / total_applications
            ) if total_applications else 0,
            "discoverable_candidate_count":UserProfile.objects.filter(
                role="seeker",discoverable=True,user__is_active=True
            ).count(),
        })
    resume=Resume.objects.filter(user=request.user,is_active=True).order_by("-uploaded_at").first()
    recommendations=[]; profile={}; recommendation_status="no_resume"
    recommendation_error=""
    if resume and resume.extracted_text:
        run = _ensure_recommendation_run(resume, request.user)
        recommendation_status = run.status
        recommendation_error = run.error_message
        recommendations, profile = _recommendation_context(run, request.user)
    candidate_profile = UserProfile.objects.filter(user=request.user).first()
    profile_fields = (
        request.user.get_full_name(), request.user.email,
        getattr(candidate_profile, "phone", ""),
        getattr(candidate_profile, "location", ""),
        getattr(candidate_profile, "headline", ""),
        getattr(candidate_profile, "career_summary", ""),
        getattr(candidate_profile, "skills", ""),
        bool(resume),
    )
    profile_completion = round(
        sum(bool(value) for value in profile_fields) * 100 / len(profile_fields)
    )
    saved_jobs_preview = list(
        SavedJob.objects.filter(user=request.user).select_related(
            "job", "job__recruiter"
        )[:4]
    )
    for saved_job in saved_jobs_preview:
        if saved_job.job_id:
            saved_job.preview_title = saved_job.job.title
            saved_job.preview_company = saved_job.job.company
            saved_job.preview_location = saved_job.job.location
            saved_job.preview_url = reverse("job_detail", args=[saved_job.job_id])
        else:
            from .ai_engine import get_dataset_job, _record_is_available

            record = get_dataset_job(saved_job.source_key)
            saved_job.preview_title = (
                record.get("job_title", "") if record else saved_job.title_snapshot
            )
            saved_job.preview_company = (
                record.get("company", "") if record else saved_job.company_snapshot
            )
            saved_job.preview_location = (
                record.get("location", "") if record else saved_job.location_snapshot
            )
            saved_job.preview_url = (
                reverse("dataset_job_detail", args=[saved_job.source_key])
                if record and _record_is_available(record, timezone.now()) else ""
            )
    groups = {
        "Programming": {"python", "java", "c++", "php", "javascript"},
        "Web & APIs": {"django", "flask", "fastapi", "html", "css", "react", "node.js", "rest api"},
        "Data & AI": {"machine learning", "deep learning", "artificial intelligence", "data science", "nlp", "tensorflow", "pytorch", "scikit-learn"},
        "Databases": {"sql", "mysql", "postgresql", "sqlite", "mongodb", "redis"},
        "Cloud & DevOps": {"aws", "azure", "docker", "kubernetes", "linux"},
        "Analytics": {"data analysis", "power bi", "tableau", "excel", "statistics", "pandas", "numpy"},
    }
    skill_chart = []
    if resume and resume.extracted_skills:
        skills = set(resume.extracted_skills)
        grouped = {label: len(skills & members) for label, members in groups.items()}
        uncategorized = skills - set().union(*groups.values())
        if uncategorized:
            grouped["Other skills"] = len(uncategorized)
        total = sum(grouped.values())
        if total:
            skill_chart = [
                {"label": label, "count": count, "percentage": round(count * 100 / total)}
                for label, count in grouped.items()
                if count
            ]
    resume_history = list(
        Resume.objects.filter(user=request.user).order_by("-uploaded_at")[:10]
    )
    for historical_resume in resume_history:
        historical_resume.ats_score = _resume_ats_score(historical_resume)
        if resume and historical_resume.pk == resume.pk:
            resume.ats_score = historical_resume.ats_score
    return render(request,"core/dashboard_main.html",{
        "role":role,"resume":resume,"resume_history":resume_history,
        "recommendations":recommendations,"profile":profile,"skill_chart":skill_chart,
        "recommendation_status":recommendation_status,
        "recommendation_error":recommendation_error,
        "recommendation_updates_url":reverse("recommendation_updates"),
        "recommendation_retry_url":reverse("retry_recommendations"),
        "applications":Application.objects.filter(applicant=request.user)
        .select_related("job", "recruiter").order_by("-updated_at")[:5],
        "application_status_summary":Application.objects.filter(
            applicant=request.user
        ).values("status").annotate(total=Count("pk")),
        "profile_completion":profile_completion,
        "latest_jobs":published_jobs().select_related(
            "recruiter"
        ).order_by("-created_at")[:4],
        "saved_jobs_preview":saved_jobs_preview,
        "recently_viewed_jobs":RecentlyViewedJob.objects.filter(
            user=request.user, job__in=published_jobs()
        ).select_related("job", "job__recruiter")[:4],
        "model":model_status(load=False),
    })


@login_required
@require_GET
def recommendation_updates(request):
    if role_of(request.user) != "seeker":
        return HttpResponseForbidden("Only job seekers can view recommendations.")
    resume = Resume.objects.filter(
        user=request.user, is_active=True
    ).order_by("-uploaded_at").first()
    run = None
    recommendations = []
    profile = {}
    status = "no_resume"
    error = ""
    if resume and resume.extracted_text:
        run = RecommendationRun.objects.filter(
            resume=resume, user=request.user
        ).first()
        if run is None:
            run = _ensure_recommendation_run(resume, request.user)
        else:
            input_hash, job_version = _recommendation_input_signature(
                resume, request.user
            )
            is_stale = (
                run.status in {"queued", "processing"}
                and run.updated_at < timezone.now() - RECOMMENDATION_STALE_AFTER
            )
            is_legacy_failure = (
                run.status == "failed"
                and run.error_message == LEGACY_RECOMMENDATION_BROKER_ERROR
            )
            if (
                run.input_hash != input_hash
                or run.job_version != job_version
                or is_stale
                or is_legacy_failure
            ):
                run = _ensure_recommendation_run(resume, request.user)
        status = run.status
        error = run.error_message
        recommendations, profile = _recommendation_context(run, request.user)

    html = render_to_string(
        "core/partials/recommendation_results.html",
        {
            "resume": resume,
            "recommendations": recommendations,
            "profile": profile,
            "recommendation_status": status,
            "recommendation_error": error,
            "recommendation_updates_url": reverse("recommendation_updates"),
            "recommendation_retry_url": reverse("retry_recommendations"),
        },
        request=request,
    )
    return JsonResponse({"status": status, "html": html})


@login_required
@require_POST
def retry_recommendations(request):
    if role_of(request.user) != "seeker":
        return HttpResponseForbidden("Only job seekers can request recommendations.")
    resume = Resume.objects.filter(
        user=request.user, is_active=True
    ).order_by("-uploaded_at").first()
    if not resume or not resume.extracted_text:
        messages.error(request, "Upload an analyzed resume before retrying.")
        return redirect("dashboard")
    run = _ensure_recommendation_run(resume, request.user, retry=True)
    if run.status == "failed":
        messages.error(request, run.error_message)
    else:
        messages.info(request, "Recommendation generation has been queued.")
    return redirect("dashboard")
@login_required
def profile(request):
    prof, _ = UserProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(
        request.POST or None,
        request.FILES or None,
        instance=prof,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            request.user.first_name = form.cleaned_data["first_name"].strip()
            request.user.last_name = form.cleaned_data["last_name"].strip()
            request.user.email = form.cleaned_data["email"]
            request.user.save(update_fields=["first_name", "last_name", "email"])
            prof = form.save()
        messages.success(request, "Profile updated.")
        return redirect("profile")
    profile_fields = {
        "personal": ["first_name", "last_name", "email", "phone", "location", "profile_image"],
        "professional": [
            "headline", "career_summary", "education", "experience_years", "skills",
        ],
        "links": ["linkedin_url", "github_url", "portfolio_url"],
        "preferences": [
            "preferred_roles", "preferred_locations", "preferred_category",
            "preferred_employment_type", "preferred_work_mode",
            "preferred_salary_min", "preferred_salary_max",
        ],
        "company": [
            "company_name", "company_website", "company_description", "company_industry",
        ],
        "privacy": ["discoverable"],
    }
    sections = [
        {
            "key": key,
            "title": title,
            "fields": [form[name] for name in field_names if name in form.fields],
        }
        for key, title, field_names in [
            ("personal", "Personal information", profile_fields["personal"]),
            ("professional", "Professional profile", profile_fields["professional"]),
            ("links", "Professional links", profile_fields["links"]),
            ("preferences", "Job preferences", profile_fields["preferences"]),
            ("company", "Company details", profile_fields["company"]),
            ("privacy", "Profile visibility", profile_fields["privacy"]),
        ]
        if any(name in form.fields for name in field_names)
    ]
    completion_fields = [
        ("Full name", f"{request.user.first_name} {request.user.last_name}".strip()),
        ("Email", request.user.email),
        ("Phone", prof.phone),
        ("Location", prof.location),
        ("Professional headline", prof.headline),
        ("Summary", prof.career_summary),
        ("Skills", prof.skills),
        ("Education", prof.education),
        ("LinkedIn", prof.linkedin_url),
        ("Portfolio", prof.portfolio_url),
    ]
    completed = sum(bool(value) for _, value in completion_fields)
    missing_profile_fields = [
        label for label, value in completion_fields if not value
    ]
    completion = round(completed * 100 / len(completion_fields))
    return render(request, "core/profile.html", {
        "form": form,
        "profile": prof,
        "profile_sections": sections,
        "profile_completion": completion,
        "missing_profile_fields": missing_profile_fields,
    })
def _resume_ats_breakdown(resume):
    return ats_breakdown(
        resume.extracted_text,
        resume.extracted_skills,
        education=resume.extracted_education,
        experience_years=resume.experience_years,
        internship_experience_years=resume.internship_experience_years,
        projects=resume.extracted_projects,
        certifications=resume.extracted_certifications,
    )


def _resume_ats_score(resume):
    return sum(item["score"] for item in _resume_ats_breakdown(resume))


@login_required
def upload_resume(request):
    """Handle resume upload and analysis for job seekers."""
    if role_of(request.user) != "seeker":
        return HttpResponseForbidden("Only job seekers can upload resumes.")
    form = ResumeForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.save(commit=False)
        upload.user = request.user
        if upload.file.size > 5 * 1024 * 1024:
            form.add_error("file", "Maximum file size is 5 MB.")
        else:
            try:
                upload.save()
                text = read_resume(upload.file.path)
                skills = extract_resume_skills(text)
                category, confidence = predict_category(text)
                # Detect language of resume text
                try:
                    from langdetect import detect, LangDetectException
                    language = detect(text)
                except Exception:
                    language = ""
                upload.extracted_text = text
                upload.extracted_skills = skills
                upload.extracted_education = extract_education(text)
                upload.professional_summary = "\n".join(
                    extract_resume_sections(
                        text, ("professional summary", "summary", "profile")
                    )
                )
                upload.extracted_projects = extract_projects(text)
                upload.extracted_certifications = extract_certifications(text)
                upload.experience_years = extract_experience(text)
                upload.internship_experience_years = (
                    extract_internship_experience(text)
                )
                upload.predicted_category = category
                upload.category_confidence = round(confidence, 4)
                upload.language = language
                upload.ats_score = _resume_ats_score(upload)
                upload.improvement_tips = tips(text, skills)
                upload.save()
                # Deactivate previous resumes
                Resume.objects.filter(user=request.user, is_active=True).exclude(pk=upload.pk).update(is_active=False)
                # Update user profile summary fields
                prof = request.user.profile
                prof.skills = ", ".join(skills)
                prof.education = ", ".join(upload.extracted_education)
                prof.experience_years = upload.experience_years
                if upload.professional_summary:
                    prof.career_summary = upload.professional_summary
                prof.save()
                messages.success(request, "Resume analyzed successfully.")
                return redirect("resume_analysis", resume_id=upload.pk)
            except (ValueError, OSError, RuntimeError, OpcError, XMLSyntaxError, KeyError, DatabaseError) as exc:
                if upload.pk:
                    upload.file.delete(save=False)
                    upload.delete()
                logger.exception("Resume analysis failed for user %s", request.user.pk)
                messages.error(request, f"Could not analyze this resume: {exc}")
    return render(request, "core/upload_resume.html", {
        "form": form,
        "model": model_status(load=False),
    })

@login_required
def resume_analysis(request, resume_id):
    if role_of(request.user) != "seeker":
        return HttpResponseForbidden("Only job seekers can view resume analysis.")
    resume = get_object_or_404(
        Resume.objects.select_related("parsing_result"),
        pk=resume_id,
        user=request.user,
    )
    form = ResumeReviewForm(request.POST or None, resume=resume)
    if request.method == "POST" and form.is_valid():
        cleaned = form.cleaned_data
        parsed_lists = form.cleaned_lists()
        with transaction.atomic():
            resume.professional_summary = cleaned["professional_summary"].strip()
            category_changed = (
                cleaned["predicted_category"].strip()
                != resume.predicted_category
            )
            resume.predicted_category = cleaned["predicted_category"].strip()
            if category_changed:
                resume.category_confidence = 0
            resume.extracted_skills = parsed_lists["skills"]
            resume.extracted_education = parsed_lists["education"]
            resume.extracted_projects = parsed_lists["projects"]
            resume.extracted_certifications = parsed_lists["certifications"]
            resume.experience_years = cleaned["experience_years"]
            resume.internship_experience_years = (
                cleaned["internship_experience_years"]
            )
            resume.ats_score = ats_estimate(
                resume.extracted_text,
                resume.extracted_skills,
                education=parsed_lists["education"],
                experience_years=resume.experience_years,
                internship_experience_years=resume.internship_experience_years,
                projects=parsed_lists["projects"],
                certifications=parsed_lists["certifications"],
            )
            resume.improvement_tips = tips(
                resume.extracted_text, resume.extracted_skills
            )
            resume.save(update_fields=[
                "professional_summary", "predicted_category",
                "category_confidence", "extracted_skills", "extracted_education",
                "extracted_projects", "extracted_certifications",
                "experience_years", "internship_experience_years", "ats_score",
                "improvement_tips",
            ])
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            profile.skills = ", ".join(resume.extracted_skills)
            profile.education = ", ".join(resume.extracted_education)
            profile.experience_years = resume.experience_years
            if resume.professional_summary:
                profile.career_summary = resume.professional_summary
            profile.save()
        messages.success(
            request, "Your reviewed resume details were saved and the ATS estimate recalculated."
        )
        return redirect("resume_analysis", resume_id=resume.pk)
    ats_breakdown_rows = _resume_ats_breakdown(resume)
    ats_score = sum(item["score"] for item in ats_breakdown_rows)
    if resume.ats_score != ats_score:
        resume.ats_score = ats_score
        resume.save(update_fields=["ats_score"])
    recommendation_status = "no_resume"
    recommendation_error = ""
    recommendations = []
    recommendation_profile = {}
    if resume.extracted_text:
        recommendation_run = _ensure_recommendation_run(resume, request.user)
        recommendation_status = recommendation_run.status
        recommendation_error = recommendation_run.error_message
        recommendations, recommendation_profile = _recommendation_context(
            recommendation_run, request.user
        )
    return render(request, "core/resume_analysis.html", {
        "resume": resume,
        "ats_score": ats_score,
        "ats_breakdown": ats_breakdown_rows,
        "parsing_result": getattr(resume, "parsing_result", None),
        "review_form": form,
        "recommendations": recommendations,
        "recommendation_profile": recommendation_profile,
        "recommendation_status": recommendation_status,
        "recommendation_error": recommendation_error,
        "recommendation_updates_url": reverse("recommendation_updates"),
        "recommendation_retry_url": reverse("retry_recommendations"),
    })


@login_required
def resume_builder(request):
    if role_of(request.user) != "seeker":
        return HttpResponseForbidden("Only job seekers can use the resume builder.")
    builder = ResumeBuilder.objects.filter(user=request.user).order_by("-updated_at").first()
    initial = None
    if builder:
        initial = {
            field: getattr(builder, "work_experience" if field == "experience" else field)
            for field in ATSResumeBuilderForm.base_fields
        }
    else:
        profile = getattr(request.user, "profile", None)
        initial = {
            "full_name": request.user.get_full_name(),
            "email": request.user.email,
        }
        if profile:
            initial.update({
                "phone": profile.phone,
                "city_state": profile.location,
                "linkedin": profile.linkedin_url,
                "github": profile.github_url,
                "portfolio": profile.portfolio_url,
                "professional_summary": profile.career_summary,
                "technical_skills": profile.skills,
                "education": profile.education,
            })
    form = ATSResumeBuilderForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        builder = builder or ResumeBuilder(user=request.user)
        for field, value in form.cleaned_data.items():
            setattr(builder, "work_experience" if field == "experience" else field, value)
        builder.save()
        previous_pdf_name = builder.generated_pdf.name
        generated_pdf = _builder_pdf_response(builder, attachment=False)
        pdf_name = f"resume-{uuid.uuid4().hex[:12]}.pdf"
        builder.generated_pdf.save(
            pdf_name, ContentFile(generated_pdf.content), save=True
        )
        if previous_pdf_name:
            builder.generated_pdf.storage.delete(previous_pdf_name)
        messages.success(request, "Your ATS resume is ready to preview.")
        return redirect("resume_builder_preview", builder_id=builder.pk)
    field_groups = [
        ("Personal details and template", ["template", "full_name", "target_job_title", "email", "phone", "city_state", "linkedin", "github", "portfolio"]),
        ("Professional profile", ["professional_summary", "career_objective"]),
        ("Technical skills", ["technical_skills"]),
        ("Experience and projects", ["experience", "projects"]),
        ("Education and credentials", ["education", "certifications"]),
        ("Additional achievements", ["awards", "training"]),
    ]
    preview_sections = [
        ("Professional summary", "professional_summary"),
        ("Career objective", "career_objective"),
        ("Technical skills", "technical_skills"),
        ("Experience", "experience"),
        ("Projects", "projects"),
        ("Education", "education"),
        ("Certifications", "certifications"),
        ("Awards", "awards"),
        ("Training and activities", "training"),
    ]
    resume_quality_checks = [
        ("Name", bool(form["full_name"].value())),
        ("Contact information", bool(
            form["email"].value() or form["phone"].value()
        )),
        ("Technical skills", bool(form["technical_skills"].value())),
        ("Education", bool(form["education"].value())),
        ("Experience details", bool(form["experience"].value())),
        ("Projects", bool(form["projects"].value())),
        ("Certifications", bool(form["certifications"].value())),
    ]
    resume_completeness = round(
        sum(is_complete for _, is_complete in resume_quality_checks)
        * 100 / len(resume_quality_checks)
    )
    return render(request, "core/resume_builder.html", {
        "form": form,
        "builder": builder,
        "resume_form_groups": [
            {"title": title, "fields": [form[name] for name in names]}
            for title, names in field_groups
        ],
        "resume_preview_sections": preview_sections,
        "resume_quality_checks": resume_quality_checks,
        "resume_completeness": resume_completeness,
    })


def _builder_pdf_response(builder, attachment):
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from django.utils.text import slugify
    import reportlab

    output = BytesIO()
    styles = getSampleStyleSheet()
    font_directory = Path(reportlab.__file__).parent / "fonts"
    if "JobPilotSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("JobPilotSans", str(font_directory / "Vera.ttf"))
        )
        pdfmetrics.registerFont(
            TTFont("JobPilotSans-Bold", str(font_directory / "VeraBd.ttf"))
        )
        pdfmetrics.registerFontFamily(
            "JobPilotSans",
            normal="JobPilotSans",
            bold="JobPilotSans-Bold",
            italic="JobPilotSans",
            boldItalic="JobPilotSans-Bold",
        )
    palette = {
        "modern": ("#18324b", TA_CENTER, 20),
        "classic": ("#243447", TA_LEFT, 19),
        "minimal": ("#111827", TA_LEFT, 18),
        "developer": ("#14532d", TA_LEFT, 19),
        "data_science": ("#4338ca", TA_LEFT, 19),
    }
    accent, heading_alignment, name_size = palette.get(
        builder.template, palette["modern"]
    )
    name_style = ParagraphStyle(
        "ResumeName", parent=styles["Title"], alignment=heading_alignment,
        textColor=colors.HexColor(accent), fontName="JobPilotSans-Bold",
        fontSize=name_size, leading=name_size + 4, spaceAfter=4,
    )
    contact_style = ParagraphStyle(
        "ResumeContact", parent=styles["Normal"], alignment=heading_alignment,
        fontName="JobPilotSans", fontSize=9, leading=13, spaceAfter=12,
    )
    section_style = ParagraphStyle(
        "ResumeSection", parent=styles["Heading2"], fontName="JobPilotSans-Bold",
        fontSize=11, leading=14, textColor=colors.HexColor(accent),
        alignment=heading_alignment,
        spaceBefore=9, spaceAfter=4, keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "ResumeBody", parent=styles["BodyText"], fontName="JobPilotSans",
        fontSize=9.5, leading=13, spaceAfter=3,
    )
    document = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=0.65 * inch,
        leftMargin=0.65 * inch, topMargin=0.55 * inch, bottomMargin=0.55 * inch,
        title=f"{builder.full_name} - Resume",
        author=builder.full_name,
    )
    story = [Paragraph(escape(builder.full_name), name_style)]
    if builder.target_job_title:
        story.append(Paragraph(escape(builder.target_job_title), contact_style))
    contact = [
        builder.email, builder.phone, builder.city_state, builder.linkedin,
        builder.github, builder.portfolio,
    ]
    contact_text = " &nbsp;|&nbsp; ".join(escape(value) for value in contact if value)
    if contact_text:
        story.append(Paragraph(contact_text, contact_style))
    sections = (
        ("Professional Summary", builder.professional_summary),
        ("Career Objective", builder.career_objective),
        ("Technical Skills", builder.technical_skills),
        ("Work Experience", builder.work_experience),
        ("Projects", builder.projects),
        ("Education", builder.education),
        ("Certifications", builder.certifications),
        ("Awards and Accomplishments", builder.awards),
        ("Training and Activities", builder.training),
    )
    bullet_sections = {
        "Technical Skills", "Work Experience", "Projects", "Education",
        "Certifications", "Awards and Accomplishments", "Training and Activities",
    }
    for title, content in sections:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            continue
        story.extend((Paragraph(escape(title), section_style),))
        story.extend(
            Paragraph(f"- {escape(line)}" if title in bullet_sections else escape(line), body_style)
            for line in lines
        )
    document.build(story)
    filename = f"{slugify(builder.full_name) or 'resume'}.pdf"
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    disposition = "attachment" if attachment else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


def _saved_builder_pdf_response(builder, attachment):
    if not builder.generated_pdf:
        return _builder_pdf_response(builder, attachment)
    with builder.generated_pdf.open("rb") as pdf_file:
        content = pdf_file.read()
    from django.utils.text import slugify

    filename = f"{slugify(builder.full_name) or 'resume'}.pdf"
    response = HttpResponse(content, content_type="application/pdf")
    disposition = "attachment" if attachment else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response


@login_required
def resume_builder_preview(request, builder_id):
    if role_of(request.user) != "seeker":
        return HttpResponseForbidden("Only job seekers can access the resume builder.")
    builder = get_object_or_404(ResumeBuilder, pk=builder_id, user=request.user)
    return render(request, "core/resume_builder_preview.html", {"builder": builder})


@login_required
def resume_builder_pdf(request, builder_id):
    if role_of(request.user) != "seeker":
        return HttpResponseForbidden("Only job seekers can access the resume builder.")
    builder = get_object_or_404(ResumeBuilder, pk=builder_id, user=request.user)
    response = _saved_builder_pdf_response(builder, attachment=False)
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response


@login_required
def resume_builder_download(request, builder_id):
    if role_of(request.user) != "seeker":
        return HttpResponseForbidden("Only job seekers can access the resume builder.")
    builder = get_object_or_404(ResumeBuilder, pk=builder_id, user=request.user)
    response = _saved_builder_pdf_response(builder, attachment=True)
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response

def job_list(request):
    filters = {
        name: request.GET.get(name, "").strip()
        for name in (
            "q", "title", "skills", "company", "category", "location",
            "experience", "experience_level", "type", "sort", "min_salary",
            "max_salary", "posted_within",
        )
    }
    filters["work_mode"] = request.GET.get("work_mode", "").strip()
    filters["database_only"] = True
    if filters["sort"] not in {"", "relevant", "newest", "oldest", "title"}:
        filters["sort"] = "newest"
    if filters["work_mode"] not in dict(Job.WORK_MODE_CHOICES):
        filters["work_mode"] = ""
    page_number = request.GET.get("page")
    cache_payload = {
        "filters": filters,
        "page": page_number,
        "job_version": cache.get(RECOMMENDATION_JOB_VERSION_KEY, ""),
    }
    cache_digest = hashlib.sha256(
        json.dumps(cache_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache_key = f"jobpilot:job-search:v1:{cache_digest}"
    cached_page = cache.get(cache_key)
    if cached_page is None:
        try:
            jobs = search_database_jobs(filters)
        except DatabaseError:
            logger.exception("Combined job search failed")
            messages.error(request, "Job search is temporarily unavailable. Please try again.")
            jobs = []
            page_obj = Paginator(jobs, 12).get_page(page_number)
        else:
            page_obj = Paginator(jobs, 12).get_page(page_number)
            page_obj.object_list = [
                _database_job_search_record(job)
                for job in page_obj.object_list
            ]
            cache.set(
                cache_key,
                (list(page_obj.object_list), page_obj.paginator.count),
                JOB_SEARCH_CACHE_TIMEOUT,
            )
    else:
        cached_jobs, result_count = cached_page
        paginator = Paginator(range(result_count), 12)
        page_obj = paginator.get_page(page_number)
        database_job_ids = [
            item["job"].pk for item in cached_jobs
            if item.get("source") == "database" and item.get("job") is not None
        ]
        available_job_ids = set(
            published_jobs().filter(pk__in=database_job_ids)
            .values_list("pk", flat=True)
        )
        page_obj.object_list = [
            job for job in cached_jobs
            if (
                _record_is_available(job, timezone.now())
                and (
                    job.get("source") != "database"
                    or (
                        job.get("job") is not None
                        and job["job"].pk in available_job_ids
                    )
                )
            )
        ]
    saved_job_ids=set()
    saved_dataset_keys=set()
    result_items = [dict(item) for item in page_obj.object_list]
    recruiter_ids = {
        item["job"].recruiter_id
        for item in result_items
        if item.get("source") == "database" and item.get("job") is not None
    }
    company_profiles = {
        profile.user_id: profile
        for profile in UserProfile.objects.filter(
            user_id__in=recruiter_ids, role="recruiter"
        )
    }
    for item in result_items:
        job = item.get("job")
        company_profile = company_profiles.get(job.recruiter_id) if job else None
        item["company_profile_id"] = (
            company_profile.user_id
            if company_profile
            and company_profile.company_name.strip().casefold()
            == job.company.strip().casefold()
            else None
        )
    if request.user.is_authenticated and role_of(request.user)=="seeker":
        saved_job_ids=set(SavedJob.objects.filter(
            user=request.user,job__isnull=False
        ).values_list("job_id",flat=True))
        saved_dataset_keys=set(SavedJob.objects.filter(
            user=request.user,job__isnull=True,source="dataset"
        ).values_list("source_key",flat=True))
    return render(request,"core/job_list.html",{
        "jobs":result_items,"page_obj":page_obj,**filters,
        "work_mode":filters["work_mode"],
        "saved_job_ids":saved_job_ids,
        "saved_dataset_keys":saved_dataset_keys,
        "user_role":role_of(request.user) if request.user.is_authenticated else "",
    })
def job_detail(request,job_id):
    job=get_object_or_404(
        published_jobs().select_related("recruiter", "recruiter__profile"),
        id=job_id,
    )
    applied=request.user.is_authenticated and Application.objects.filter(job=job,applicant=request.user).exists()
    score=None
    job_match=None
    if request.user.is_authenticated and role_of(request.user)=="seeker":
        RecentlyViewedJob.objects.update_or_create(
            user=request.user, job=job
        )
        resume=Resume.objects.filter(user=request.user,is_active=True).order_by("-uploaded_at").first()
        if resume:
            job_match=explain_job_match(
                resume.extracted_text, job,
                _resume_candidate_profile(resume, request.user),
            )
            score=job_match["match_score"] if job_match else 0
    job_details = _job_detail_presentation(job)
    company_profile = UserProfile.objects.filter(
        user=job.recruiter,
        role="recruiter",
        company_name__iexact=job.company,
    ).first()
    return render(request,"core/job_detail.html",{
        "job":job,"applied":applied,"score":score,"source_label":"Database",
        "job_match":job_match,
        "job_title":job.title,
        "employer_application_url":_safe_external_url(job.application_url),
        "is_saved":request.user.is_authenticated and SavedJob.objects.filter(
            user=request.user, job=job
        ).exists(),
        "is_dataset":False,"user_role":role_of(request.user) if request.user.is_authenticated else "",
        "job_details": job_details,
        "related_jobs": job_details["related_jobs"],
        "company_profile_id": company_profile.user_id if company_profile else None,
    })

def dataset_job_detail(request, source_key):
    from .ai_engine import _record_is_available

    job = get_dataset_job(source_key)
    if job is None or not _record_is_available(job, timezone.now()):
        raise Http404("Dataset job not found.")
    applied = request.user.is_authenticated and Application.objects.filter(
        applicant=request.user, source="dataset", external_job_key=str(source_key)
    ).exists()
    is_saved = request.user.is_authenticated and SavedJob.objects.filter(
        user=request.user, job__isnull=True, source="dataset",
        source_key=str(source_key),
    ).exists()
    job_match = None
    if request.user.is_authenticated and role_of(request.user) == "seeker":
        resume = Resume.objects.filter(
            user=request.user, is_active=True
        ).order_by("-uploaded_at").first()
        if resume:
            job_match = explain_job_match(
                resume.extracted_text, job,
                _resume_candidate_profile(resume, request.user),
            )
    return render(request, "core/job_detail.html", {
        "job":job,"applied":applied,
        "score":job_match["match_score"] if job_match else None,
        "job_match":job_match,"source_label":"Dataset",
        "job_title":job["job_title"],"is_dataset":True,"job_key":source_key,
        "employer_application_url":_safe_external_url(job.get("application_url")),
        "is_saved":is_saved,
        "user_role":role_of(request.user) if request.user.is_authenticated else "",
        "job_details": _job_detail_presentation(job, is_dataset=True),
        "related_jobs": [],
    })


def company_profile(request, recruiter_id):
    profile = get_object_or_404(
        UserProfile.objects.select_related("user"),
        user_id=recruiter_id,
        role="recruiter",
    )
    if not profile.company_name.strip():
        raise Http404("Company profile is not available.")
    jobs = published_jobs().filter(recruiter=profile.user)
    if profile.company_name.strip():
        jobs = jobs.filter(company__iexact=profile.company_name)
    jobs = jobs.order_by("-created_at")
    logo_job = jobs.exclude(company_logo="").first()
    company_name = profile.company_name or (
        jobs.values_list("company", flat=True).first() or "Company profile"
    )
    return render(request, "core/company_profile.html", {
        "company_profile": profile,
        "company_name": company_name,
        "jobs": jobs,
        "logo_job": logo_job,
    })
@login_required
def apply_job(request,job_id):
    if role_of(request.user)!="seeker": return HttpResponseForbidden("Only job seekers can apply.")
    job=get_object_or_404(published_jobs(),id=job_id)
    if Application.objects.filter(job=job,applicant=request.user).exists():
        messages.info(request,"You have already applied for this job."); return redirect("my_applications")
    latest_resume=Resume.objects.filter(user=request.user,is_active=True).order_by("-uploaded_at").first()
    form=ApplicationForm(
        request.POST or None, request.FILES or None, user=request.user,
        profile_resume=latest_resume,
    )
    if request.method=="POST" and form.is_valid():
        resume=form.cleaned_data.get("resume")
        resume_text=resume.extracted_text if resume else form.cleaned_data.get("technical_skills", "")
        ranked,_=recommend_jobs_for_text(
            resume_text,
            [job],
            1,
            candidate_profile=_resume_candidate_profile(resume, request.user)
            if resume else None,
        ) if resume_text else ([], {})
        try:
            with transaction.atomic():
                app=form.save(commit=False); app.job=job; app.applicant=request.user
                app.recruiter=job.recruiter
                app.source="database"; app.job_title_snapshot=job.title
                app.job_company_snapshot=job.company
                app.application_url_snapshot=job.application_url
                app.match_score=ranked[0]["match_score"] if ranked else 0; app.save()
                ApplicationStatusHistory.objects.create(
                    application=app,status="submitted",changed_by=request.user,
                    description="Application submitted.",
                )
                Notification.objects.create(
                    recipient=job.recruiter, application=app,
                    title="New job application",
                    message=f"{app.applicant_name} applied for {job.title}.",
                )
        except IntegrityError:
            messages.info(request,"You have already applied for this job.")
            return redirect("my_applications")
        messages.success(request,"Application submitted.")
        return redirect("application_confirmation", reference=app.reference)
    return render(request,"core/apply.html",{
        "form":form,"job":job,"job_title":job.title,"resume":latest_resume,
    })

@login_required
def apply_dataset_job(request, source_key):
    if role_of(request.user)!="seeker":
        return HttpResponseForbidden("Only job seekers can apply.")
    from .ai_engine import _record_is_available

    job = get_dataset_job(source_key)
    if job is None or not _record_is_available(job, timezone.now()):
        raise Http404("Dataset job not found.")
    existing = Application.objects.filter(
        applicant=request.user, source="dataset", external_job_key=str(source_key)
    ).first()
    if existing:
        messages.info(request, "You have already applied for this job.")
        return redirect("my_applications")
    latest_resume=Resume.objects.filter(
        user=request.user,is_active=True
    ).order_by("-uploaded_at").first()
    form=ApplicationForm(
        request.POST or None, request.FILES or None, user=request.user,
        profile_resume=latest_resume,
    )
    if request.method=="POST" and form.is_valid():
        resume=form.cleaned_data["resume"]
        resume_text=resume.extracted_text if resume else form.cleaned_data.get("technical_skills", "")
        ranked,_=recommend_jobs_for_text(
            resume_text,
            top_n=10,
            include_dataset=True,
            candidate_profile=_resume_candidate_profile(resume, request.user)
            if resume else None,
        ) if resume_text else ([], {})
        recommendation=next((item for item in ranked if item["source"]=="dataset"
            and item["source_key"]==str(source_key)), None)
        try:
            with transaction.atomic():
                app=form.save(commit=False)
                app.applicant=request.user
                app.resume=resume
                app.source="dataset"
                app.external_job_key=str(source_key)
                app.job_title_snapshot=job["job_title"]
                app.job_company_snapshot=job["company"]
                app.application_url_snapshot=job.get("application_url", "")
                app.match_score=recommendation["match_score"] if recommendation else 0
                app.save()
                ApplicationStatusHistory.objects.create(
                    application=app,status="submitted",changed_by=request.user,
                    description="Application submitted.",
                )
        except IntegrityError:
            messages.info(request,"You have already applied for this job.")
            return redirect("my_applications")
        messages.success(request,"Application submitted and added to your tracker.")
        return redirect("application_confirmation", reference=app.reference)
    return render(request,"core/apply.html",{
        "form":form,"job":job,"job_title":job["job_title"],
        "resume":latest_resume,"is_dataset":True,
    })


@login_required
def application_confirmation(request, reference):
    application=get_object_or_404(
        Application.objects.select_related("job"),
        reference=reference, applicant=request.user,
    )
    return render(request,"core/application_confirmation.html",{
        "application":application,
        "employer_application_url":_safe_external_url(
            application.application_url_snapshot
        ),
    })
@login_required
def my_applications(request):
    if role_of(request.user)!="seeker": return HttpResponseForbidden()
    applications=Application.objects.filter(applicant=request.user)
    applications=applications.select_related("job","resume").prefetch_related(
        "status_history", "interviews"
    )
    page_obj = Paginator(applications, 20).get_page(request.GET.get("page"))
    choice_labels=dict(Application.STATUS_CHOICES)
    progress_order=["submitted","resume_viewed","reviewing","shortlisted","interview_scheduled","selected"]
    for application in page_obj.object_list:
        application.employer_application_url = (
            _safe_external_url(application.application_url_snapshot)
            if application.source == "dataset" else ""
        )
        application.can_withdraw=application.status in WITHDRAWABLE_APPLICATION_STATUSES
        events_by_status = {}
        for event in application.status_history.all():
            if event.status == "interview_scheduled" or event.status not in events_by_status:
                events_by_status[event.status] = event
        if "submitted" not in events_by_status:
            events_by_status["submitted"] = None
        if application.status not in events_by_status:
            events_by_status[application.status] = None
        application.timeline_steps = [
            {
                "status": status,
                "label": choice_labels[status],
                "reached": status in events_by_status,
                "current": status == application.status,
                "changed_at": (
                    events_by_status[status].changed_at
                    if events_by_status.get(status)
                    else application.created_at if status == "submitted"
                    else application.updated_at if status == application.status
                    else None
                ),
            }
            for status in progress_order
        ]
        if application.status in {"rejected", "withdrawn"}:
            application.timeline_steps.append({
                "status": application.status,
                "label": choice_labels[application.status],
                "reached": True,
                "current": True,
                "changed_at": (
                    events_by_status[application.status].changed_at
                    if events_by_status.get(application.status)
                    else application.updated_at
                ),
            })
        application.progress_percent = round(
            100
            * sum(
                step["reached"]
                for step in application.timeline_steps
                if step["status"] in progress_order
            )
            / len(progress_order)
        )
    return render(request,"core/my_applications.html",{
        "applications":page_obj.object_list,
        "page_obj":page_obj,
    })

@login_required
@require_POST
def withdraw_application(request,application_id):
    if role_of(request.user)!="seeker": return HttpResponseForbidden()
    app=get_object_or_404(Application,pk=application_id,applicant=request.user)
    with transaction.atomic():
        app = get_object_or_404(
            Application.objects.select_for_update(),
            pk=application_id,
            applicant=request.user,
        )
        if app.status not in WITHDRAWABLE_APPLICATION_STATUSES:
            messages.error(request,"This application can no longer be withdrawn.")
            return redirect("my_applications")
        previous_status = app.status
        app.status="withdrawn"
        app.save(update_fields=["status", "updated_at"])
        ApplicationStatusHistory.objects.create(
            application=app,status="withdrawn",changed_by=request.user,
            previous_status=previous_status,
            description="Application withdrawn by applicant.",
        )
        _status_notification(app,"withdrawn")
    messages.success(request,"Application withdrawn.")
    return redirect("my_applications")

@login_required
@require_POST
def toggle_saved_job(request,job_id):
    if role_of(request.user)!="seeker": return HttpResponseForbidden("Only job seekers can save jobs.")
    job=get_object_or_404(published_jobs(),id=job_id)
    saved,created=SavedJob.objects.get_or_create(user=request.user,job=job)
    if created:
        messages.success(request,"Job saved.")
    else:
        saved.delete()
        messages.success(request,"Job removed from saved jobs.")
    next_url=request.POST.get("next","")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,allowed_hosts={request.get_host()},require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("saved_jobs")


@login_required
@require_POST
def toggle_saved_dataset_job(request,source_key):
    if role_of(request.user)!="seeker":
        return HttpResponseForbidden("Only job seekers can save jobs.")
    from .ai_engine import _record_is_available

    job=get_dataset_job(source_key)
    if job is None or not _record_is_available(job,timezone.now()):
        raise Http404("Dataset job not found or no longer available.")
    saved=SavedJob.objects.filter(
        user=request.user,job__isnull=True,source="dataset",source_key=str(source_key)
    ).first()
    if saved:
        saved.delete()
        messages.success(request,"Job removed from saved jobs.")
    else:
        SavedJob.objects.create(
            user=request.user,source="dataset",source_key=str(source_key),
            title_snapshot=job.get("job_title", ""),
            company_snapshot=job.get("company", ""),
            location_snapshot=job.get("location", ""),
            salary_snapshot=job.get("salary", ""),
        )
        messages.success(request,"Job saved.")
    next_url=request.POST.get("next","")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,allowed_hosts={request.get_host()},require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("saved_jobs")

@login_required
def saved_jobs(request):
    if role_of(request.user)!="seeker": return HttpResponseForbidden()
    saved=SavedJob.objects.filter(user=request.user).select_related("job").order_by("-created_at")
    applications={
        (application.source, str(application.job_id or application.external_job_key)):application
        for application in Application.objects.filter(applicant=request.user)
    }
    for saved_job in saved:
        if saved_job.job_id:
            saved_job.is_available=saved_job.job in published_jobs()
            key=str(saved_job.job_id)
        else:
            from .ai_engine import _record_is_available
            dataset_job=get_dataset_job(saved_job.source_key)
            saved_job.is_available=bool(
                dataset_job and _record_is_available(dataset_job,timezone.now())
            )
            key=saved_job.source_key
        saved_job.application=applications.get((saved_job.source,key))
    return render(request,"core/saved_jobs.html",{"saved_jobs":saved})


@login_required
def notifications(request):
    if role_of(request.user) not in {"seeker", "recruiter"}:
        return HttpResponseForbidden()
    items=Notification.objects.filter(
        recipient=request.user
    ).select_related("application", "job")
    for item in items:
        item.job_url = (
            reverse("job_detail", args=[item.job_id])
            if item.job and _job_is_available(item.job, timezone.now())
            else ""
        )
    return render(request,"core/notifications.html",{"notifications":items})


@login_required
@require_POST
def delete_recruiter_job(request, job_id):
    if role_of(request.user) != "recruiter":
        return HttpResponseForbidden("Recruiter account required.")
    with transaction.atomic():
        job = get_object_or_404(
            Job.objects.select_for_update(), pk=job_id, recruiter=request.user
        )
        if job.applications.exists():
            messages.error(
                request,
                "This job has application history and cannot be deleted. Close it instead.",
            )
            return redirect("manage_jobs")
        job.delete()
    messages.success(request, "Job listing deleted.")
    return redirect("manage_jobs")


@login_required
@require_POST
def mark_notifications_read(request):
    if role_of(request.user) not in {"seeker", "recruiter"}:
        return HttpResponseForbidden()
    Notification.objects.filter(
        recipient=request.user, read_at__isnull=True
    ).update(read_at=timezone.now())
    messages.success(request, "Notifications marked as read.")
    return redirect("notifications")


@login_required
@require_GET
def notification_updates(request):
    if role_of(request.user) not in {"seeker", "recruiter"}:
        return JsonResponse({"error": "Notifications are unavailable for this account."}, status=403)
    try:
        after_id = int(request.GET.get("after_id", "0"))
        if after_id < 0:
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "after_id must be a non-negative integer."}, status=400)

    notifications = list(
        Notification.objects.filter(
            recipient=request.user, pk__gt=after_id
        ).select_related("application", "job").order_by("pk")[:20]
    )
    updates = []
    for notification in notifications:
        application_url = ""
        if notification.application_id:
            application_url = reverse(
                "recruiter_applications"
                if role_of(request.user) == "recruiter"
                else "my_applications"
            )
        elif notification.job and _job_is_available(
            notification.job, timezone.now()
        ):
            application_url = reverse("job_detail", args=[notification.job_id])
        updates.append({
            "id": notification.pk,
            "title": notification.title,
            "message": notification.message,
            "created_at": notification.created_at.isoformat(),
            "url": application_url,
        })
    return JsonResponse({
        "unread_count": Notification.objects.filter(
            recipient=request.user, read_at__isnull=True
        ).count(),
        "notifications": updates,
        "cursor": updates[-1]["id"] if updates else after_id,
    })


@login_required
@require_POST
def recommendation_feedback(request, source, source_key):
    if role_of(request.user) != "seeker":
        return HttpResponseForbidden("Only job seekers can provide recommendation feedback.")
    reason = request.POST.get("reason", "").casefold()
    legacy_decision = request.POST.get("relevant", "").casefold()
    valid_reasons = dict(JobRecommendationFeedback.REASON_CHOICES)
    if not reason and legacy_decision in {"true", "false"}:
        reason = "relevant" if legacy_decision == "true" else "not_relevant"
    if reason not in valid_reasons:
        return HttpResponseForbidden("Choose a valid recommendation feedback reason.")
    is_relevant = reason == "relevant"
    if source == "database":
        job = get_object_or_404(published_jobs(), pk=source_key)
        feedback_identity = {"user": request.user, "job": job}
        defaults = {
            "source": "database",
            "source_key": str(job.pk),
            "is_relevant": is_relevant,
            "reason": reason,
        }
    elif source == "dataset":
        from .ai_engine import _record_is_available, get_dataset_job

        record = get_dataset_job(source_key)
        if record is None or not _record_is_available(record, timezone.now()):
            raise Http404("Dataset job not found or no longer available.")
        feedback_identity = {
            "user": request.user,
            "source": "dataset",
            "source_key": str(source_key),
        }
        defaults = {
            "job": None,
            "is_relevant": is_relevant,
            "reason": reason,
        }
    else:
        raise Http404("Unknown job source.")

    JobRecommendationFeedback.objects.update_or_create(
        **feedback_identity, defaults=defaults
    )
    messages.success(
        request,
        "Thanks. Your feedback will personalize future recommendations; it is not used as a training label.",
    )
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("dashboard")


@login_required
def create_job(request):
    if role_of(request.user)!="recruiter": return HttpResponseForbidden("Recruiter account required.")
    form=JobForm(request.POST or None, request.FILES or None)
    if request.method=="POST" and form.is_valid():
        job=form.save(commit=False); job.recruiter=request.user; job.save(); messages.success(request,"Job published."); return redirect("manage_jobs")
    return render(request,"core/job_form.html",{"form":form,"heading":"Post a new job"})
@login_required
def manage_jobs(request):
    if role_of(request.user)!="recruiter": return HttpResponseForbidden()
    return render(request,"core/manage_jobs.html",{
        "jobs":Job.objects.filter(recruiter=request.user)
        .annotate(application_count=Count("applications"))
        .order_by("-created_at"),
    })
@login_required
def edit_job(request,job_id):
    if role_of(request.user)!="recruiter": return HttpResponseForbidden()
    job=get_object_or_404(Job,id=job_id,recruiter=request.user)
    form=JobForm(request.POST or None, request.FILES or None, instance=job)
    if request.method=="POST" and form.is_valid():
        form.save(); messages.success(request,"Job listing updated."); return redirect("manage_jobs")
    return render(request,"core/job_form.html",{"form":form,"heading":"Edit job listing"})
@login_required
@require_POST
def toggle_job_status(request,job_id):
    if role_of(request.user)!="recruiter": return HttpResponseForbidden()
    job=get_object_or_404(Job,id=job_id,recruiter=request.user)
    job.is_active=not job.is_active
    job.status="open" if job.is_active else "closed"
    job.save(update_fields=["is_active","status","updated_at"])
    messages.success(request,"Job listing reopened." if job.is_active else "Job listing closed.")
    return redirect("manage_jobs")

def _require_job_permission(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied

@staff_member_required(login_url="/admin/login/")
def administration_jobs(request):
    _require_job_permission(request, "core.view_job")
    return render(request, "core/administration_jobs.html", {
        "jobs":Job.objects.select_related("recruiter").order_by("-updated_at"),
    })

@staff_member_required(login_url="/admin/login/")
def administration_job_create(request):
    _require_job_permission(request, "core.add_job")
    form=AdminJobForm(request.POST or None, request.FILES or None)
    if request.method=="POST" and form.is_valid():
        job=form.save(commit=False)
        job.recruiter=request.user
        try:
            job.save()
        except DatabaseError:
            logger.exception("Administrator job creation failed for user %s", request.user.pk)
            messages.error(request, "The job could not be saved. Please try again.")
        else:
            messages.success(request, "Job listing created.")
            return redirect("administration_jobs")
    return render(request,"core/job_form.html",{
        "form":form,"heading":"Create job listing","administration":True,
    })

@staff_member_required(login_url="/admin/login/")
def administration_job_edit(request, job_id):
    _require_job_permission(request, "core.change_job")
    job=get_object_or_404(Job,pk=job_id)
    form=AdminJobForm(request.POST or None, request.FILES or None, instance=job)
    if request.method=="POST" and form.is_valid():
        try:
            form.save()
        except DatabaseError:
            logger.exception("Administrator job update failed for job %s", job.pk)
            messages.error(request, "The job could not be updated. Please try again.")
        else:
            messages.success(request, "Job listing updated.")
            return redirect("administration_jobs")
    return render(request,"core/job_form.html",{
        "form":form,"heading":"Edit job listing","administration":True,
    })

@staff_member_required(login_url="/admin/login/")
@require_POST
def administration_job_delete(request, job_id):
    _require_job_permission(request, "core.delete_job")
    job=get_object_or_404(Job,pk=job_id)
    try:
        job.delete()
    except DatabaseError:
        logger.exception("Administrator job deletion failed for job %s", job_id)
        messages.error(request, "The job could not be deleted. Please try again.")
    else:
        messages.success(request, "Job listing deleted.")
    return redirect("administration_jobs")

@staff_member_required(login_url="/admin/login/")
@require_POST
def administration_job_status(request, job_id):
    _require_job_permission(request, "core.change_job")
    job=get_object_or_404(Job,pk=job_id)
    status=request.POST.get("status", "")
    if status not in dict(Job.STATUS_CHOICES):
        messages.error(request, "Choose a valid job status.")
        return redirect("administration_jobs")
    job.status=status
    job.is_active=status=="open"
    try:
        job.save(update_fields=["status","is_active","updated_at"])
    except DatabaseError:
        logger.exception("Administrator job status update failed for job %s", job.pk)
        messages.error(request, "The job status could not be updated. Please try again.")
    else:
        messages.success(request, f"Job listing marked {job.get_status_display().lower()}.")
    return redirect("administration_jobs")
@login_required
def job_candidates(request,job_id):
    if role_of(request.user)!="recruiter": return HttpResponseForbidden()
    job=get_object_or_404(Job,id=job_id,recruiter=request.user)
    candidates=recommend_candidates_for_job(job)
    query=request.GET.get("q","").strip().casefold()
    minimum_score=request.GET.get("min_score","").strip()
    skill_filter=request.GET.get("skill","").strip().casefold()
    education_filter=request.GET.get("education","").strip().casefold()
    minimum_experience=request.GET.get("min_experience","").strip()
    maximum_experience=request.GET.get("max_experience","").strip()
    application_status=request.GET.get("status","").strip()
    if minimum_score:
        try:
            score_floor=float(minimum_score)
            if not 0 <= score_floor <= 100:
                raise ValueError
        except ValueError:
            messages.error(request,"Match-score filter must be between 0 and 100.")
            score_floor=0.0
    else:
        score_floor=0.0
    try:
        experience_floor = float(minimum_experience) if minimum_experience else 0.0
        if experience_floor < 0:
            raise ValueError
    except ValueError:
        messages.error(request, "Minimum experience must be a non-negative number.")
        minimum_experience = ""
        experience_floor = 0.0
    try:
        experience_ceiling = (
            float(maximum_experience) if maximum_experience else None
        )
        if experience_ceiling is not None and experience_ceiling < 0:
            raise ValueError
    except ValueError:
        messages.error(request, "Maximum experience must be a non-negative number.")
        maximum_experience = ""
        experience_ceiling = None
    if application_status and application_status not in dict(Application.STATUS_CHOICES):
        messages.error(request, "Choose a valid application status.")
        application_status = ""
    candidate_ids = [candidate["user"].pk for candidate in candidates]
    applications_by_candidate = {
        application.applicant_id: application
        for application in Application.objects.filter(
            job=job, applicant_id__in=candidate_ids
        ).select_related("job")
    }
    filtered=[]
    for candidate in candidates:
        profile=getattr(candidate["user"],"profile",None)
        application = applications_by_candidate.get(candidate["user"].pk)
        if not profile or (
            not profile.discoverable and application is None
        ):
            continue
        candidate["application"] = application
        searchable=" ".join([
            candidate["user"].get_full_name(),
            candidate["user"].username,
            profile.location if profile else "",
            " ".join(candidate["skills"]),
        ]).casefold()
        candidate_experience = float(candidate["resume"].experience_years or 0)
        candidate_education = " ".join(
            candidate["resume"].extracted_education or []
        ).casefold()
        if query and query not in searchable:
            continue
        if candidate["match_score"] < score_floor:
            continue
        if skill_filter and skill_filter not in {
            skill.casefold() for skill in candidate["skills"]
        }:
            continue
        if education_filter and education_filter not in candidate_education:
            continue
        if candidate_experience < experience_floor:
            continue
        if (
            experience_ceiling is not None
            and candidate_experience > experience_ceiling
        ):
            continue
        if application_status and (
            application is None or application.status != application_status
        ):
            continue
        filtered.append(candidate)
    page_obj=Paginator(filtered,24).get_page(request.GET.get("page"))
    return render(request,"core/candidates.html",{
        "job":job,"candidates":page_obj.object_list,"page_obj":page_obj,"model":model_status(),
        "q":request.GET.get("q",""),"min_score":minimum_score,
        "skill_filter":skill_filter,
        "education_filter":education_filter,
        "minimum_experience":minimum_experience,
        "maximum_experience":maximum_experience,
        "application_status":application_status,
        "status_choices":Application.STATUS_CHOICES,
    })
@login_required
def recruiter_applications(request):
    if role_of(request.user)!="recruiter": return HttpResponseForbidden()
    apps=Application.objects.filter(job__recruiter=request.user).select_related(
        "job","applicant","resume"
    ).prefetch_related("status_history").order_by("-created_at")
    filters={
        "job":request.GET.get("job", ""),
        "status":request.GET.get("status", ""),
        "date_from":request.GET.get("date_from", ""),
        "date_to":request.GET.get("date_to", ""),
        "skills":request.GET.get("skills", "").strip(),
        "min_match":request.GET.get("min_match", "").strip(),
    }
    if filters["job"].isdigit():
        apps=apps.filter(job_id=filters["job"])
    if filters["status"] in dict(Application.STATUS_CHOICES):
        apps=apps.filter(status=filters["status"])
    date_from=parse_date(filters["date_from"]) if filters["date_from"] else None
    date_to=parse_date(filters["date_to"]) if filters["date_to"] else None
    if filters["date_from"] and date_from:
        apps=apps.filter(created_at__date__gte=date_from)
    elif filters["date_from"]:
        filters["date_from"]=""
        messages.error(request,"Enter a valid start date.")
    if filters["date_to"] and date_to:
        apps=apps.filter(created_at__date__lte=date_to)
    elif filters["date_to"]:
        filters["date_to"]=""
        messages.error(request,"Enter a valid end date.")
    if filters["skills"]:
        apps=apps.filter(technical_skills__icontains=filters["skills"])
    if filters["min_match"]:
        try:
            minimum_match=float(filters["min_match"])
            if not 0 <= minimum_match <= 100:
                raise ValueError
            apps=apps.filter(match_score__gte=minimum_match)
        except ValueError:
            filters["min_match"]=""
            messages.error(request,"Match score must be between 0 and 100.")
    page_obj = Paginator(apps, 20).get_page(request.GET.get("page"))
    for application in page_obj.object_list:
        application.allowed_statuses=[
            (status,dict(Application.STATUS_CHOICES)[status])
            for status in RECRUITER_STATUS_TRANSITIONS.get(application.status,set())
        ]
    return render(request,"core/recruiter_applications.html",{
        "applications":page_obj.object_list,"page_obj":page_obj,
        "status_form":ApplicationStatusForm(),
        "jobs":Job.objects.filter(recruiter=request.user).order_by("title"),
        "filters":filters,
        "status_choices":Application.STATUS_CHOICES,
    })


@login_required
@require_POST
def update_application_status(request,application_id):
    if role_of(request.user)!="recruiter": return HttpResponseForbidden()
    get_object_or_404(
        Application,pk=application_id,job__recruiter=request.user
    )
    form=ApplicationStatusForm(request.POST)
    if form.is_valid():
        status=form.cleaned_data["status"]
        with transaction.atomic():
            app=get_object_or_404(
                Application.objects.select_for_update(),
                pk=application_id,
                job__recruiter=request.user,
            )
            if status not in RECRUITER_STATUS_TRANSITIONS.get(app.status,set()):
                messages.error(request,"That status change is not allowed.")
                return redirect("recruiter_applications")
            previous_status = app.status
            app.status=status
            app.interview_at=form.cleaned_data["interview_at"] if status=="interview_scheduled" else None
            app.interview_details=form.cleaned_data["interview_details"] if status=="interview_scheduled" else ""
            app.interview_mode=form.cleaned_data["interview_mode"] if status=="interview_scheduled" else ""
            app.interview_link=form.cleaned_data["interview_link"] if status=="interview_scheduled" else ""
            app.recruiter_notes=form.cleaned_data["recruiter_notes"]
            app.save(update_fields=[
                "status","interview_at","interview_details","interview_mode",
                "interview_link","recruiter_notes","updated_at",
            ])
            ApplicationStatusHistory.objects.create(
                application=app,status=status,changed_by=request.user,
                previous_status=previous_status,
                description=f"Status changed to {app.get_status_display()}.",
                recruiter_remarks=form.cleaned_data["candidate_remarks"],
            )
            if status=="interview_scheduled":
                InterviewSchedule.objects.create(
                    application=app,scheduled_by=request.user,
                    starts_at=app.interview_at,mode=app.interview_mode,
                    meeting_link=app.interview_link,details=app.interview_details,
                )
            _status_notification(app,status)
        messages.success(request,"Application status updated.")
    else:
        messages.error(request,"Review the status and interview details and try again.")
    return redirect("recruiter_applications")
def about(request): return render(request,"core/about.html")
def privacy(request): return render(request,"core/legal.html",{"page_title":"Privacy"})
def terms(request): return render(request,"core/legal.html",{"page_title":"Terms"})
def contact(request):
    form=ContactForm(request.POST or None)
    if request.method=="POST" and form.is_valid():
        if not settings.EMAIL_HOST_USER:
            messages.error(request,"Contact email isn't configured yet. Please try again later.")
        else:
            data=form.cleaned_data
            try:
                send_mail(
                    f"JobPilot contact message from {data['name']}",
                    f"From: {data['name']} <{data['email']}>\n\n{data['message']}",
                    settings.DEFAULT_FROM_EMAIL,[settings.EMAIL_HOST_USER],fail_silently=False,
                )
            except (SMTPException,OSError):
                logger.exception("Contact email delivery failed")
                messages.error(request,"We couldn't send your message right now. Please try again later.")
            else:
                messages.success(request,"Thanks, your message has been sent.")
                return redirect("contact")
    return render(request,"core/contact.html",{"form":form})

@login_required
def resume_download(request,resume_id):
    resume=get_object_or_404(Resume.objects.select_related("user","user__profile"),pk=resume_id)
    is_owner=resume.user_id==request.user.pk
    is_admin=request.user.is_staff
    is_applicant_resume=Application.objects.filter(applicant=request.user,resume=resume).exists()
    recruiter_has_application=(
        role_of(request.user)=="recruiter"
        and role_of(resume.user)=="seeker"
        and Application.objects.filter(resume=resume,job__recruiter=request.user).exists()
    )
    candidate_discovery=(
        role_of(request.user)=="recruiter"
        and role_of(resume.user)=="seeker"
        and resume.is_active
        and resume.user.profile.discoverable
    )
    if not (is_owner or is_admin or is_applicant_resume or recruiter_has_application or candidate_discovery):
        return HttpResponseForbidden("You aren't allowed to view this resume.")
    try:
        file_handle=resume.file.open("rb")
    except (OSError,ValueError):
        raise Http404("Resume file is unavailable.")
    return FileResponse(file_handle,as_attachment=True,filename=Path(resume.file.name).name)


@login_required
def application_resume_download(request,application_id):
    if role_of(request.user)!="recruiter":
        return HttpResponseForbidden("Recruiter account required.")
    application=get_object_or_404(
        Application.objects.select_related("job","resume"),
        pk=application_id,job__recruiter=request.user,
    )
    if application.uploaded_resume:
        try:
            file_handle=application.uploaded_resume.open("rb")
        except (OSError,ValueError):
            raise Http404("Resume file is unavailable.")
        filename=Path(application.uploaded_resume.name).name
    elif application.resume_id:
        try:
            file_handle=application.resume.file.open("rb")
        except (OSError,ValueError):
            raise Http404("Resume file is unavailable.")
        filename=Path(application.resume.file.name).name
    else:
        raise Http404("No resume was attached to this application.")
    with transaction.atomic():
        application = get_object_or_404(
            Application.objects.select_for_update(),
            pk=application_id,
            job__recruiter=request.user,
        )
        if application.status=="submitted":
            application.status="resume_viewed"
            application.save(update_fields=["status","updated_at"])
            ApplicationStatusHistory.objects.create(
                application=application,status="resume_viewed",changed_by=request.user,
                previous_status="submitted",
                description="Recruiter opened the application resume.",
            )
            _status_notification(application,"resume_viewed")
    return FileResponse(file_handle,as_attachment=True,filename=filename)
