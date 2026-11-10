import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.chatbot_ml.entity_extractor import extract_entities
from core.chatbot_ml.intent_classifier import classify_intent
from core.chatbot_ml.services import answer_message
from core.models import (
    Application,
    ChatConversation,
    ChatMessage,
    InterviewSchedule,
    Job,
    Resume,
    UserProfile,
)


class ChatbotFixtureMixin:
    def create_user(self, username, role="seeker"):
        user = User.objects.create_user(username=username, password="test-pass-123")
        UserProfile.objects.create(user=user, role=role)
        return user

    def create_job(self, recruiter, title="Python Developer", company="Acme", location="Trivandrum"):
        return Job.objects.create(
            recruiter=recruiter,
            title=title,
            company=company,
            location=location,
            required_skills="Python, Django, SQL",
            description="Build and maintain software services.",
        )


class ChatbotMLTests(ChatbotFixtureMixin, TestCase):
    def setUp(self):
        cache.clear()

    def test_classifier_handles_common_jobpilot_questions(self):
        samples = {
            "Find Python developer jobs.": "job_search",
            "Show data analyst jobs in Trivandrum.": "job_search",
            "What skills are missing from my resume?": "missing_skills",
            "Show my latest application status.": "application_status",
            "How can I upload my resume?": "website_faq",
            "Find candidates with Django and SQL skills.": "candidate_search",
        }
        for message, expected in samples.items():
            with self.subTest(message=message):
                intent, confidence = classify_intent(message)
                self.assertEqual(intent, expected)
                self.assertGreaterEqual(confidence, 0.10)

    def test_classifier_supports_recent_and_skill_matched_job_requests(self):
        samples = {
            "Find jobs matching my skills": "job_recommendation",
            "Show recently posted jobs": "job_search",
            "Find AI/ML internships": "job_search",
            "Find jobs in Trivandrum": "job_search",
            "Analyze my resume": "skill_analysis",
            "Show shortlisted candidates": "recruiter_applicants",
        }
        for message, expected in samples.items():
            with self.subTest(message=message):
                self.assertEqual(classify_intent(message)[0], expected)

    def test_entity_extractor_finds_title_skill_and_location(self):
        recruiter = self.create_user("entity-recruiter", "recruiter")
        self.create_job(recruiter)
        entities = extract_entities(
            "Find Python Developer jobs in Trivandrum with 1 year experience"
        )
        self.assertEqual(entities["job_title"].casefold(), "python developer")
        self.assertEqual(entities["location"].casefold(), "trivandrum")
        self.assertIn("python", entities["skills"])
        self.assertEqual(entities["experience_years"], "1")

    def test_job_search_uses_real_active_database_jobs(self):
        recruiter = self.create_user("job-recruiter", "recruiter")
        expected_job = self.create_job(recruiter)
        self.create_job(recruiter, title="Data Analyst", location="Kochi")

        result = answer_message("Find Python developer jobs in Trivandrum")

        self.assertEqual(result["intent"], "job_search")
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["jobs"][0]["title"], expected_job.title)
        self.assertEqual(result["jobs"][0]["salary"], "")
        self.assertIn(str(expected_job.pk), result["jobs"][0]["url"])

    def test_resume_skills_are_scoped_to_logged_in_user(self):
        seeker = self.create_user("resume-seeker")
        other = self.create_user("other-seeker")
        Resume.objects.create(
            user=seeker,
            file="",
            extracted_skills=["Python", "Django"],
            extracted_text="Python Django",
        )
        Resume.objects.create(
            user=other,
            file="",
            extracted_skills=["SecretSkill"],
            extracted_text="SecretSkill",
        )

        result = answer_message("What skills were extracted from my resume?", seeker)

        self.assertEqual(result["intent"], "skill_analysis")
        self.assertEqual(result["skills"], ["Python", "Django"])
        self.assertNotIn("SecretSkill", result["reply"])

    def test_application_records_are_scoped_to_applicant(self):
        recruiter = self.create_user("application-recruiter", "recruiter")
        seeker = self.create_user("application-seeker")
        other = self.create_user("application-other")
        job = self.create_job(recruiter)
        Application.objects.create(
            job=job, recruiter=recruiter, applicant=seeker, status="shortlisted"
        )
        Application.objects.create(
            job=job, recruiter=recruiter, applicant=other, status="rejected"
        )

        result = answer_message("What is my latest application status?", seeker)

        self.assertEqual(result["intent"], "application_status")
        self.assertEqual(len(result["applications"]), 1)
        self.assertEqual(result["applications"][0]["status"], "Shortlisted")

    def test_shortlist_and_interview_questions_use_their_actual_records(self):
        recruiter = self.create_user("schedule-recruiter", "recruiter")
        seeker = self.create_user("schedule-seeker")
        job = self.create_job(recruiter)
        shortlisted = Application.objects.create(
            job=job, recruiter=recruiter, applicant=seeker, status="shortlisted"
        )
        another_job = self.create_job(
            recruiter, title="Data Engineer", location="Kochi"
        )
        Application.objects.create(
            job=another_job,
            recruiter=recruiter,
            applicant=seeker,
            status="submitted",
        )
        InterviewSchedule.objects.create(
            application=shortlisted,
            starts_at=timezone.now(),
            mode="video",
        )

        shortlisted_result = answer_message(
            "Which applications are shortlisted?", seeker
        )
        interviews_result = answer_message("Do I have an interview scheduled?", seeker)

        self.assertEqual(len(shortlisted_result["applications"]), 1)
        self.assertEqual(shortlisted_result["applications"][0]["status"], "Shortlisted")
        self.assertEqual(len(interviews_result["interviews"]), 1)
        self.assertIn("1 interview schedule", interviews_result["reply"])

    def test_recruiter_applicants_are_scoped_to_owned_jobs(self):
        recruiter = self.create_user("owner-recruiter", "recruiter")
        other_recruiter = self.create_user("other-owner", "recruiter")
        applicant = self.create_user("job-applicant")
        own_job = self.create_job(recruiter)
        other_job = self.create_job(other_recruiter, title="Analyst", company="Elsewhere")
        Application.objects.create(
            job=own_job, recruiter=recruiter, applicant=applicant, status="shortlisted"
        )
        Application.objects.create(
            job=other_job, recruiter=other_recruiter, applicant=applicant
        )

        result = answer_message("Show applicants for my job", recruiter)

        self.assertEqual(result["intent"], "recruiter_applicants")
        self.assertEqual(len(result["applicants"]), 1)
        self.assertEqual(result["applicants"][0]["job_title"], own_job.title)

    def test_recruiter_active_job_summary_excludes_closed_jobs(self):
        recruiter = self.create_user("active-job-recruiter", "recruiter")
        self.create_job(recruiter)
        closed_job = self.create_job(
            recruiter, title="Closed Analyst", location="Kochi"
        )
        closed_job.status = "closed"
        closed_job.save(update_fields=["status"])

        result = answer_message("Show my active jobs", recruiter)

        self.assertEqual(result["intent"], "recruiter_jobs")
        self.assertIn("1 active job", result["reply"])
        self.assertEqual(len(result["jobs"]), 1)

    def test_role_permissions_reject_recruiter_data_for_job_seekers(self):
        seeker = self.create_user("role-seeker")
        result = answer_message("How many jobs have I posted?", seeker)
        self.assertIn("recruiters", result["reply"])
        self.assertNotIn("jobs", result)

    def test_recruiter_applicant_question_is_disambiguated_by_role(self):
        recruiter = self.create_user("applicant-count-recruiter", "recruiter")
        applicant = self.create_user("applicant-count-user")
        job = self.create_job(recruiter)
        Application.objects.create(
            job=job, recruiter=recruiter, applicant=applicant
        )
        result = answer_message("How many applicants do I have?", recruiter)
        self.assertEqual(result["intent"], "recruiter_applicants")
        self.assertEqual(len(result["applicants"]), 1)

    @patch("core.chatbot_ml.services.classify_intent", return_value=("job_search", 0.05))
    def test_low_confidence_intents_ask_for_clarification(self, classify):
        result = answer_message("something ambiguous")
        self.assertEqual(result["intent"], "fallback")
        self.assertIn("try asking", result["reply"].casefold())

    def test_candidate_matching_is_called_for_the_recruiters_owned_job(self):
        recruiter = self.create_user("candidate-recruiter", "recruiter")
        job = self.create_job(recruiter)
        candidate = self.create_user("discoverable-candidate")
        UserProfile.objects.filter(user=candidate).update(discoverable=True)
        candidate_data = {
            "user": candidate,
            "match_score": 82.4,
            "matching_skills": ["Python", "Django"],
        }
        with patch(
            "core.ai_engine.recommend_candidates_for_job",
            return_value=[candidate_data],
        ) as recommend:
            result = answer_message("Find candidates for Python Developer job", recruiter)
        recommend.assert_called_once_with(job, top_n=5)
        self.assertEqual(result["candidates"][0]["match_score"], 82.4)
        self.assertNotIn("email", result["candidates"][0])


class ChatbotApiTests(ChatbotFixtureMixin, TestCase):
    def setUp(self):
        cache.clear()

    def post_message(self, message):
        return self.client.post(
            reverse("chatbot_message"),
            data=json.dumps({"message": message}),
            content_type="application/json",
        )

    def test_empty_and_malformed_messages_are_rejected(self):
        empty = self.post_message(" ")
        self.assertEqual(empty.status_code, 400)
        malformed = self.client.post(
            reverse("chatbot_message"), data="{", content_type="application/json"
        )
        self.assertEqual(malformed.status_code, 400)

    def test_anonymous_user_can_get_local_faq_but_not_private_resume_data(self):
        faq = self.post_message("How do I upload my resume?")
        self.assertEqual(faq.status_code, 200)
        self.assertIn("upload", faq.json()["reply"].casefold())
        personal = self.post_message("What skills were extracted from my resume?")
        self.assertEqual(personal.status_code, 200)
        self.assertTrue(personal.json().get("auth_required"))

    def test_authenticated_history_is_isolated_and_clear_is_scoped(self):
        owner = self.create_user("chat-owner")
        other = self.create_user("chat-other")
        owner_conversation = ChatConversation.objects.create(user=owner)
        other_conversation = ChatConversation.objects.create(user=other)
        ChatMessage.objects.create(
            conversation=owner_conversation, sender="user", content="my message"
        )
        ChatMessage.objects.create(
            conversation=other_conversation, sender="user", content="private message"
        )
        self.client.force_login(owner)

        history = self.client.get(reverse("chatbot_history")).json()
        self.assertEqual(len(history["messages"]), 1)
        self.assertEqual(history["messages"][0]["content"], "my message")
        self.assertEqual(self.client.post(reverse("chatbot_clear")).status_code, 200)
        self.assertFalse(ChatConversation.objects.filter(user=owner).exists())
        self.assertTrue(ChatConversation.objects.filter(user=other).exists())

    def test_authenticated_exchange_is_saved_for_the_user(self):
        user = self.create_user("chat-saver")
        self.client.force_login(user)

        response = self.post_message("How does JobPilot work?")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["response_time_ms"] >= 0)
        self.assertEqual(ChatMessage.objects.filter(
            conversation__user=user
        ).count(), 2)
