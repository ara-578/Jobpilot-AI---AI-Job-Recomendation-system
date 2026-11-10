from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .ai_engine import search_database_jobs, search_jobs
from .models import (
    Application, Job, Notification, RecentlyViewedJob, Resume, SavedJob,
    UserProfile,
)


class PortalUpgradeWorkflowTests(TestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(username="portal_recruiter")
        UserProfile.objects.create(
            user=self.recruiter,
            role="recruiter",
            company_name="Northstar",
            company_website="https://northstar.example",
            company_description="A software company.",
            company_industry="Technology",
            location="Chennai",
        )
        self.seeker = User.objects.create_user(
            username="portal_seeker",
            first_name="Jordan",
            email="jordan@example.com",
        )
        UserProfile.objects.create(
            user=self.seeker, role="seeker", location="Chennai"
        )

    def make_job(self, **overrides):
        values = {
            "recruiter": self.recruiter,
            "title": "Python Backend Engineer",
            "company": "Northstar",
            "location": "Chennai",
            "employment_type": "Full-time",
            "work_mode": "hybrid",
            "required_experience_years": 2,
            "salary": "INR 8-12 LPA",
            "required_skills": "Python, Django, SQL",
            "description": "Build and maintain Python APIs.",
        }
        values.update(overrides)
        return Job.objects.create(**values)

    def test_removed_career_routes_resolve_to_not_found(self):
        for path in (
            "/career/readiness/",
            "/career/roadmap/",
            "/career/mock-interview/",
            "/insights/jobs/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)

    def test_job_search_uses_database_jobs_and_company_filter(self):
        expected = self.make_job()
        self.make_job(
            title="Java Engineer",
            company="Other Company",
            required_skills="Java, Spring Boot",
        )
        dataset_record = {
            "source": "dataset",
            "source_key": "external-1",
            "job_title": expected.title,
            "company": expected.company,
            "location": expected.location,
            "required_skills": expected.required_skills,
            "created_at": expected.created_at,
            "status": "open",
            "is_active": True,
        }

        with patch("core.ai_engine._dataset_records", return_value=[dataset_record]):
            records = search_jobs({
                "database_only": True,
                "title": "Python Engineer",
                "skills": "Python, Django",
                "company": "Northstar",
            })

        self.assertEqual([item["job"].pk for item in records], [expected.pk])
        self.assertTrue(all(item["source"] == "database" for item in records))

    def test_database_search_prioritizes_exact_title_and_filters_unknown_salary(self):
        exact = self.make_job(title="Python Developer")
        self.make_job(
            title="Python Backend Engineer",
            salary="Salary not disclosed",
        )
        self.make_job(
            title="Python Developer",
            company="Northstar Remote",
            salary="INR 2-4 LPA",
        )

        results = list(search_database_jobs({
            "q": "Python Developer",
            "company": "Northstar",
            "sort": "relevant",
            "min_salary": "800000",
            "max_salary": "1200000",
        }))

        self.assertEqual([job.pk for job in results], [exact.pk])

    def test_job_search_view_exposes_company_and_posting_filters(self):
        self.make_job()
        response = self.client.get(
            reverse("job_list"),
            {"company": "Northstar", "posted_within": "30", "sort": "relevant"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["company"], "Northstar")
        self.assertEqual(response.context["posted_within"], "30")
        self.assertEqual(response.context["jobs"][0]["company"], "Northstar")

    def test_seeker_dashboard_links_to_upload_when_resume_is_missing(self):
        self.client.force_login(self.seeker)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("upload_resume"))

    def test_job_detail_records_recent_view_and_company_page_lists_owned_vacancy(self):
        job = self.make_job()
        self.client.force_login(self.seeker)

        detail = self.client.get(reverse("job_detail", args=[job.pk]))
        company = self.client.get(
            reverse("company_profile", args=[self.recruiter.pk])
        )

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(company.status_code, 200)
        self.assertEqual(company.context["jobs"][0].pk, job.pk)
        self.assertEqual(
            RecentlyViewedJob.objects.get(user=self.seeker, job=job).job_id,
            job.pk,
        )
        self.assertContains(detail, reverse("company_profile", args=[self.recruiter.pk]))

    def test_job_alert_workflow_is_removed_and_new_jobs_do_not_notify_seekers(self):
        self.client.force_login(self.seeker)
        self.assertEqual(self.client.get("/job-alerts/").status_code, 404)
        self.assertEqual(
            self.client.post("/job-alerts/").status_code,
            404,
        )
        with (
            patch(
                "core.views._ensure_recommendation_run",
                return_value=SimpleNamespace(status="ready", error_message=""),
            ),
            patch("core.views._recommendation_context", return_value=([], {})),
        ):
            dashboard = self.client.get(reverse("dashboard"))
        self.assertNotContains(dashboard, "JOB ALERTS")
        self.assertNotContains(dashboard, "/job-alerts/")

        self.make_job()
        self.assertFalse(
            Notification.objects.filter(
                recipient=self.seeker, job__isnull=False
            ).exists()
        )

    def test_dashboard_contains_profile_latest_saved_and_application_summaries(self):
        job = self.make_job()
        SavedJob.objects.create(user=self.seeker, job=job)
        Application.objects.create(
            job=job,
            recruiter=self.recruiter,
            applicant=self.seeker,
        )
        resume = Resume.objects.create(
            user=self.seeker,
            file=SimpleUploadedFile("resume.txt", b"Python developer"),
            extracted_text="Python developer",
            extracted_skills=["python"],
        )
        self.addCleanup(resume.file.delete, save=False)
        self.client.force_login(self.seeker)

        with (
            patch(
                "core.views._ensure_recommendation_run",
                return_value=SimpleNamespace(status="ready", error_message=""),
            ),
            patch("core.views._recommendation_context", return_value=([], {})),
        ):
            response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.context["profile_completion"], 0)
        self.assertEqual(response.context["latest_jobs"][0].pk, job.pk)
        self.assertEqual(response.context["saved_jobs_preview"][0].job_id, job.pk)
        self.assertContains(response, "Welcome back, Jordan")
        self.assertContains(response, "Profile completion")
        self.assertContains(response, "Latest jobs")
        self.assertNotContains(response, "Career readiness")

    def test_company_fields_are_not_editable_from_a_seeker_profile(self):
        self.client.force_login(self.seeker)
        response = self.client.post(reverse("profile"), {
            "first_name": "Jordan",
            "last_name": "",
            "email": "jordan@example.com",
            "phone": "",
            "location": "Chennai",
            "headline": "",
            "career_summary": "",
            "education": "",
            "experience_years": "0",
            "skills": "",
            "linkedin_url": "",
            "github_url": "",
            "portfolio_url": "",
            "preferred_roles": "",
            "preferred_locations": "",
            "preferred_category": "",
            "preferred_employment_type": "",
            "preferred_work_mode": "",
            "preferred_salary_min": "",
            "preferred_salary_max": "",
            "company_name": "Injected Company",
            "company_description": "Injected data",
        })

        self.assertEqual(response.status_code, 302)
        self.seeker.profile.refresh_from_db()
        self.assertEqual(self.seeker.profile.company_name, "")

    def test_recruiter_can_delete_unapplied_owned_job_but_preserves_application_history(self):
        own_job = self.make_job()
        other_recruiter = User.objects.create_user(username="another_recruiter")
        UserProfile.objects.create(user=other_recruiter, role="recruiter")
        other_job = Job.objects.create(
            recruiter=other_recruiter,
            title="Other role",
            company="Other",
            required_skills="Python",
            description="Role description.",
        )
        self.client.force_login(self.recruiter)

        forbidden = self.client.post(
            reverse("delete_recruiter_job", args=[other_job.pk])
        )
        deleted = self.client.post(
            reverse("delete_recruiter_job", args=[own_job.pk])
        )

        self.assertEqual(forbidden.status_code, 404)
        self.assertFalse(Job.objects.filter(pk=own_job.pk).exists())
        self.assertEqual(deleted.status_code, 302)

        protected_job = self.make_job(title="Protected role")
        Application.objects.create(
            job=protected_job,
            recruiter=self.recruiter,
            applicant=self.seeker,
        )
        response = self.client.post(
            reverse("delete_recruiter_job", args=[protected_job.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Job.objects.filter(pk=protected_job.pk).exists())
