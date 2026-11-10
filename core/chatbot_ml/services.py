import csv
import logging
import re
from functools import lru_cache
from pathlib import Path

from django.db.models import Count, Q
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from core.chatbot_ml.entity_extractor import extract_entities
from core.chatbot_ml.intent_classifier import classify_intent
from core.models import (
    Application,
    ChatConversation,
    ChatMessage,
    InterviewSchedule,
    Job,
    Resume,
    SavedJob,
    UserProfile,
)

logger = logging.getLogger(__name__)
CONFIDENCE_THRESHOLD = getattr(
    settings, "JOBPILOT_CHATBOT_CONFIDENCE_THRESHOLD", 0.10
)
MAX_JOBS = 5


@lru_cache(maxsize=1)
def _faq_rows():
    path = Path(__file__).resolve().parent / "faqs.csv"
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


@lru_cache(maxsize=1)
def _faq_vectorizer():
    return TfidfVectorizer(ngram_range=(1, 2), lowercase=True).fit(
        [row["question"] for row in _faq_rows()]
    )


def _role(user):
    if not user or not user.is_authenticated:
        return ""
    try:
        return user.profile.role
    except UserProfile.DoesNotExist:
        return ""


def _latest_resume(user):
    return Resume.objects.filter(user=user, is_active=True).order_by(
        "-uploaded_at", "-pk"
    ).first()


def _available_jobs():
    now = timezone.now()
    return Job.objects.filter(
        is_active=True, status="open"
    ).filter(
        Q(deadline__isnull=True) | Q(deadline__gt=now)
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )


def _job_card(job, score=None, matching_skills=None):
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location or "Location not specified",
        "salary": job.salary or "",
        "match_score": round(float(score), 1) if score is not None else None,
        "matching_skills": list(matching_skills or []),
        "url": reverse("job_detail", args=[job.pk]),
    }


def _jobs_response(jobs, intro, scores=None):
    cards = []
    for job in jobs:
        score_data = (scores or {}).get(job.pk, {})
        cards.append(_job_card(
            job,
            score=score_data.get("match_score"),
            matching_skills=score_data.get("matching_skills"),
        ))
    if not cards:
        return {"reply": "I couldn't find active jobs matching that search in the database.", "jobs": []}
    return {"reply": intro, "jobs": cards}


def _job_search(message, user):
    entities = extract_entities(message)
    jobs = _available_jobs().select_related("recruiter")
    if entities["job_title"]:
        jobs = jobs.filter(title__icontains=entities["job_title"])
    if entities["location"]:
        jobs = jobs.filter(location__icontains=entities["location"])
    if entities["company"]:
        jobs = jobs.filter(company__icontains=entities["company"])
    if entities["skills"]:
        skill_filter = Q()
        for skill in entities["skills"]:
            skill_filter |= Q(required_skills__icontains=skill) | Q(
                preferred_skills__icontains=skill
            ) | Q(title__icontains=skill)
        jobs = jobs.filter(skill_filter)
    if entities["employment_type"]:
        jobs = jobs.filter(employment_type__icontains=entities["employment_type"])
    if entities["experience_years"]:
        experience = float(entities["experience_years"])
        jobs = jobs.filter(
            required_experience_years__lte=experience
        ).filter(
            Q(max_experience_years__isnull=True)
            | Q(max_experience_years__gte=experience)
        )
    if entities["work_mode"]:
        work_mode = {
            "remote": "remote", "hybrid": "hybrid", "on-site": "on_site",
            "onsite": "on_site",
        }[entities["work_mode"]]
        jobs = jobs.filter(work_mode=work_mode)
    if not any(entities.values()):
        jobs = jobs.order_by("-created_at")
    found = list(jobs.order_by("-created_at", "-pk")[:MAX_JOBS])
    return _jobs_response(found, "Here are active database listings that match your search.")


def _resume_recommendations(user):
    resume = _latest_resume(user)
    if not resume:
        return None, "I couldn't find an active uploaded resume on your account. Upload one to get resume-based recommendations."
    from core.ai_engine import recommend_jobs_for_text

    ranked, _profile = recommend_jobs_for_text(
        resume.extracted_text,
        db_jobs=_available_jobs().select_related("recruiter"),
        top_n=MAX_JOBS,
        include_dataset=False,
        user=user,
        candidate_profile={
            "skills": resume.extracted_skills or [],
            "education": resume.extracted_education or [],
            "experience_years": float(resume.experience_years or 0),
        },
    )
    cards = []
    for item in ranked:
        source_key = str(item.get("source_key") or "")
        if item.get("source") != "database" or not source_key.isdigit():
            continue
        job = Job.objects.filter(pk=int(source_key)).first()
        if job:
            cards.append(_job_card(
                job,
                item.get("match_score"),
                item.get("matching_skills"),
            ))
    if not cards:
        return [], "I couldn't find active database jobs to recommend for your resume right now."
    return cards, "These active jobs were ranked using your resume and JobPilot's recommendation engine."


def _saved_jobs(user):
    saved = SavedJob.objects.filter(user=user).select_related("job").order_by("-created_at")[:MAX_JOBS]
    cards = []
    for item in saved:
        if item.job_id:
            cards.append(_job_card(item.job))
        else:
            cards.append({
                "title": item.title_snapshot,
                "company": item.company_snapshot,
                "location": item.location_snapshot or "Location not specified",
                "salary": item.salary_snapshot or "",
                "match_score": None,
                "matching_skills": [],
                "url": reverse("dataset_job_detail", args=[item.source_key])
                if item.source == "dataset" and item.source_key else "",
            })
    if not cards:
        return {"reply": "You haven't saved any jobs yet.", "jobs": []}
    return {"reply": "Here are the jobs saved to your account.", "jobs": cards}


def _application_status(user, message=""):
    application_query = Application.objects.filter(applicant=user)
    if "shortlisted" in message.casefold():
        application_query = application_query.filter(status="shortlisted")
    applications = list(
        application_query
        .select_related("job")
        .prefetch_related("interviews")
        .order_by("-created_at", "-pk")[:5]
    )
    total = Application.objects.filter(applicant=user).count()
    if "interview" in message.casefold():
        schedules = list(
            InterviewSchedule.objects.filter(application__applicant=user)
            .select_related("application", "application__job")
            .order_by("-starts_at", "-pk")[:5]
        )
        total_schedules = InterviewSchedule.objects.filter(
            application__applicant=user
        ).count()
        return {
            "reply": (
                f"You have {total_schedules} interview schedule(s)."
                if total_schedules
                else "There are no interview schedules on your applications yet."
            ),
            "interviews": [{
                "job_title": schedule.application.display_title or "Job listing",
                "company": schedule.application.display_company or "",
                "starts_at": schedule.starts_at.isoformat(),
                "mode": schedule.get_mode_display(),
            } for schedule in schedules],
        }
    if not applications:
        if "shortlisted" in message.casefold():
            return {
                "reply": "You don't have any shortlisted applications yet.",
                "applications": [],
            }
        return {"reply": "I couldn't find any applications on your account.", "applications": []}
    rows = []
    for application in applications:
        interviews = [
            {
                "starts_at": interview.starts_at.isoformat(),
                "mode": interview.get_mode_display(),
            }
            for interview in application.interviews.all()[:3]
        ]
        rows.append({
            "title": application.display_title or "Job listing",
            "company": application.display_company or "Company not specified",
            "applied_at": application.created_at.strftime("%b %d, %Y"),
            "status": application.get_status_display(),
            "interviews": interviews,
        })
    return {
        "reply": (
            f"You have {total} application{'s' if total != 1 else ''}. "
            + (
                "Here are your shortlisted applications."
                if "shortlisted" in message.casefold()
                else "Here are your latest updates."
            )
        ),
        "applications": rows,
    }


def _recruiter_jobs(user, message=""):
    job_query = Job.objects.filter(recruiter=user)
    active_only = "active" in message.casefold()
    if active_only:
        job_query = job_query.filter(is_active=True, status="open")
    jobs = list(
        job_query
        .annotate(applicant_count=Count("applications", distinct=True))
        .order_by("-created_at", "-pk")[:MAX_JOBS]
    )
    cards = [
        {
            **_job_card(job),
            "status": job.get_status_display(),
            "applicant_count": job.applicant_count,
        }
        for job in jobs
    ]
    total_jobs = job_query.count()
    total_applicants = Application.objects.filter(
        job__recruiter=user,
        **({"job__is_active": True, "job__status": "open"} if active_only else {}),
    ).count()
    if not cards:
        return {"reply": "You haven't posted any jobs yet.", "jobs": []}
    return {
        "reply": (
            f"You have {total_jobs} active job(s)"
            if active_only else f"You've posted {total_jobs} job(s)"
        ) + f" and received {total_applicants} application(s). Here are your listings.",
        "jobs": cards,
    }


def _owned_recruiter_jobs(user, message, entities):
    jobs = Job.objects.filter(recruiter=user, is_active=True, status="open")
    if entities["job_title"]:
        jobs = jobs.filter(title__icontains=entities["job_title"])
    if entities["company"]:
        jobs = jobs.filter(company__icontains=entities["company"])
    if entities["skills"]:
        skill_filter = Q()
        for skill in entities["skills"]:
            skill_filter |= Q(required_skills__icontains=skill)
        jobs = jobs.filter(skill_filter)
    if not entities["job_title"] and not entities["skills"]:
        words = [
            word for word in re.findall(r"[a-zA-Z+#.]+", message.casefold())
            if word not in {"find", "matching", "candidates", "candidate", "for", "my", "job", "the", "with", "suitable"}
        ]
        if len(words) >= 1:
            title_filter = Q()
            for word in words:
                title_filter |= Q(title__icontains=word)
            jobs = jobs.filter(title_filter)
    return list(jobs.order_by("-created_at", "-pk")[:5])


def _recruiter_applicants(user):
    applications = list(
        Application.objects.filter(job__recruiter=user)
        .select_related("job", "applicant")
        .order_by("-created_at", "-pk")[:MAX_JOBS]
    )
    total = Application.objects.filter(job__recruiter=user).count()
    if not applications:
        return {"reply": "There are no applications for your job listings yet.", "applicants": []}
    rows = [{
        "name": item.applicant.get_full_name() or item.applicant.username,
        "job_title": item.display_title or "Job listing",
        "company": item.display_company or "",
        "status": item.get_status_display(),
        "applied_at": item.created_at.strftime("%b %d, %Y"),
        "match_score": item.match_score if item.match_score else None,
    } for item in applications]
    return {
        "reply": f"Your listings have {total} application(s). Here are the latest applicants.",
        "applicants": rows,
    }


def _candidate_search(user, message):
    entities = extract_entities(message)
    jobs = _owned_recruiter_jobs(user, message, entities)
    if not jobs:
        return {
            "reply": "I couldn't identify an active job you own for this search. Mention one of your job titles, or post an active job first.",
            "candidates": [],
        }
    if len(jobs) > 1 and not entities["job_title"] and not entities["skills"]:
        titles = ", ".join(job.title for job in jobs[:4])
        return {
            "reply": f"Which of your jobs should I use? Your active listings include: {titles}.",
            "candidates": [],
        }
    from core.ai_engine import recommend_candidates_for_job

    candidates = recommend_candidates_for_job(jobs[0], top_n=5)
    requested_skills = {skill.casefold() for skill in entities["skills"]}
    results = []
    for candidate in candidates:
        matching = candidate.get("matching_skills", [])
        if requested_skills and not requested_skills.intersection(
            skill.casefold() for skill in matching
        ):
            continue
        person = candidate["user"]
        results.append({
            "name": person.get_full_name() or person.username,
            "job_title": jobs[0].title,
            "match_score": round(float(candidate["match_score"]), 1),
            "matching_skills": matching[:8],
        })
    if not results:
        return {
            "reply": f"I couldn't find candidates who match the requirements for {jobs[0].title}. Only discoverable resumes and applicants who consented to sharing are considered.",
            "candidates": [],
        }
    return {
        "reply": f"Here are candidates matched to your job, {jobs[0].title}. Only discoverable resumes and applicants who consented to sharing are included.",
        "candidates": results,
    }


def _faq_answer(message):
    rows = _faq_rows()
    vectorizer = _faq_vectorizer()
    query = vectorizer.transform([message])
    scores = cosine_similarity(query, vectorizer.transform(
        [row["question"] for row in rows]
    )).ravel()
    best = int(scores.argmax())
    if scores[best] < 0.12:
        return "I can help with registration, uploading a resume, applying for jobs, using the resume builder, and recruiter tools. What would you like to know?"
    return rows[best]["answer"]


def _personal_access(user, required_role):
    if not user or not user.is_authenticated:
        return "Please sign in to access information from your account."
    if _role(user) != required_role:
        return (
            "This feature is available to job seekers after signing in."
            if required_role == "seeker"
            else "This feature is available to recruiters after signing in."
        )
    return ""


def answer_message(message, user=None):
    intent, confidence = classify_intent(message)
    if confidence < CONFIDENCE_THRESHOLD:
        return {
            "reply": "I'm not sure I understood that. Try asking about jobs, your resume, applications, or JobPilot features.",
            "intent": "fallback",
            "confidence": confidence,
            "follow_up": ["Find jobs for my skills", "How do I upload my resume?"],
        }
    if (
        intent == "application_status"
        and _role(user) == "recruiter"
        and re.search(r"\bapplicant|applicants|shortlist|shortlisted\b", message, re.I)
    ):
        intent = "recruiter_applicants"
    result = {"intent": intent, "confidence": round(confidence, 3)}
    if intent == "greeting":
        result["reply"] = "Hi! I'm your JobPilot AI Assistant. I can help you find suitable jobs, understand your resume, and track your applications. What would you like to do?"
    elif intent == "goodbye":
        result["reply"] = "You're welcome. Good luck with your next opportunity!"
    elif intent == "website_faq":
        result["reply"] = _faq_answer(message)
    elif intent == "fallback":
        result["reply"] = "I can help with job search, resume analysis, applications, saved jobs, recruiter postings, and JobPilot FAQs. Could you rephrase your question?"
    elif intent == "job_search":
        result.update(_job_search(message, user))
    elif intent == "job_recommendation":
        if not user or not user.is_authenticated:
            result["reply"] = "Sign in as a job seeker to get recommendations based on your resume."
            result["auth_required"] = True
        else:
            denial = _personal_access(user, "seeker")
            if denial:
                result["reply"] = denial
            else:
                cards, reply = _resume_recommendations(user)
                result["reply"] = reply
                result["jobs"] = cards or []
    elif intent in {"skill_analysis", "missing_skills", "ats_help"}:
        if not user or not user.is_authenticated:
            result["reply"] = "Sign in as a job seeker to view your resume analysis."
            result["auth_required"] = True
        else:
            denial = _personal_access(user, "seeker")
            if denial:
                result["reply"] = denial
            else:
                resume = _latest_resume(user)
                if not resume:
                    result["reply"] = "I couldn't find an active resume on your account. Upload a resume to use this feature."
                elif intent == "skill_analysis":
                    skills = resume.extracted_skills or []
                    result["reply"] = (
                        "Your resume analysis extracted: " + ", ".join(map(str, skills[:30]))
                        if skills else "Your uploaded resume doesn't have extracted skills yet. Try running the resume analysis again."
                    )
                    result["skills"] = list(map(str, skills[:30]))
                elif intent == "ats_help":
                    tips = [str(item) for item in (resume.improvement_tips or [])[:8]]
                    result["reply"] = f"Your current ATS score is {resume.ats_score}/100. "
                    result["reply"] += (
                        "Based on your saved resume analysis: " + " ".join(tips)
                        if tips else "No improvement tips were saved with this analysis, so I won't guess. Add clear section headings, measurable results, and role-specific keywords, then analyze the updated resume."
                    )
                    result["ats_score"] = resume.ats_score
                    result["tips"] = tips
                else:
                    cards, reply = _resume_recommendations(user)
                    result["reply"] = reply
                    result["jobs"] = cards or []
                    result["missing_skills"] = []
                    if cards:
                        from core.ai_engine import recommend_jobs_for_text

                        ranked, _ = recommend_jobs_for_text(
                            resume.extracted_text,
                            db_jobs=_available_jobs().select_related("recruiter"),
                            top_n=1,
                            include_dataset=False,
                            user=user,
                            candidate_profile={"skills": resume.extracted_skills or []},
                        )
                        if ranked:
                            missing = ranked[0].get("missing_skills", [])
                            result["missing_skills"] = list(map(str, missing[:20]))
                            result["reply"] = (
                                f"Compared with the best matching active job ({ranked[0]['job_title']}), "
                                + ("the listed skills are missing from your resume: " + ", ".join(result["missing_skills"])
                                   if result["missing_skills"] else "no missing required skills were detected.")
                            )
    elif intent == "application_status":
        if not user or not user.is_authenticated:
            result["reply"] = "Sign in as a job seeker to view your applications."
            result["auth_required"] = True
        else:
            denial = _personal_access(user, "seeker")
            if denial:
                result["reply"] = denial
            else:
                result.update(_application_status(user, message))
    elif intent == "saved_jobs":
        if not user or not user.is_authenticated:
            result["reply"] = "Sign in as a job seeker to view your saved jobs."
            result["auth_required"] = True
        else:
            denial = _personal_access(user, "seeker")
            result.update({"reply": denial} if denial else _saved_jobs(user))
    elif intent == "recruiter_jobs":
        denial = _personal_access(user, "recruiter")
        if denial:
            result["reply"] = denial
            result["auth_required"] = not (user and user.is_authenticated)
        else:
            result.update(_recruiter_jobs(user, message))
    elif intent == "recruiter_applicants":
        denial = _personal_access(user, "recruiter")
        if denial:
            result["reply"] = denial
            result["auth_required"] = not (user and user.is_authenticated)
        else:
            result.update(_recruiter_applicants(user))
    elif intent == "candidate_search":
        denial = _personal_access(user, "recruiter")
        if denial:
            result["reply"] = denial
            result["auth_required"] = not (user and user.is_authenticated)
        else:
            result.update(_candidate_search(user, message))
    result.setdefault("reply", "I couldn't complete that request. Please try asking another way.")
    if not result.get("follow_up"):
        result["follow_up"] = _follow_ups(intent, _role(user))
    return result


def _follow_ups(intent, role):
    if role == "recruiter":
        return ["Show my active jobs", "How many applicants do I have?"]
    if role == "seeker":
        return ["Recommend jobs based on my resume", "Show my application status"]
    if intent in {"greeting", "website_faq"}:
        return ["How do I upload my resume?", "Find jobs"]
    return ["Find jobs", "How does JobPilot work?"]


def save_exchange(user, message, result):
    conversation, _created = ChatConversation.objects.get_or_create(user=user)
    conversation.save(update_fields=["updated_at"])
    ChatMessage.objects.create(
        conversation=conversation, sender="user", content=message
    )
    ChatMessage.objects.create(
        conversation=conversation,
        sender="assistant",
        content=result["reply"],
        intent=result.get("intent", ""),
        metadata={
            key: value for key, value in result.items()
            if key not in {"reply", "intent", "confidence", "follow_up"}
        },
    )
    old_messages = conversation.messages.order_by("-created_at", "-pk")[100:]
    ids_to_delete = list(old_messages.values_list("pk", flat=True))
    if ids_to_delete:
        ChatMessage.objects.filter(pk__in=ids_to_delete).delete()
    return conversation.pk
