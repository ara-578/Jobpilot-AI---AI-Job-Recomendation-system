import uuid

from django.db import models
from django.contrib.auth.models import User
from django.core.validators import FileExtensionValidator, MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError

class UserProfile(models.Model):
    ROLE_CHOICES = [("seeker", "Job Seeker"), ("recruiter", "Recruiter")]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="seeker")
    profile_image = models.ImageField(
        upload_to="profile_images/", blank=True, null=True
    )
    phone = models.CharField(max_length=30, blank=True)
    location = models.CharField(max_length=120, blank=True)
    headline = models.CharField(max_length=180, blank=True)
    skills = models.TextField(blank=True)
    education = models.CharField(max_length=180, blank=True)
    experience_years = models.DecimalField(
        max_digits=4, decimal_places=1, default=0, validators=[MinValueValidator(0)]
    )
    company_name = models.CharField(max_length=180, blank=True)
    company_website = models.URLField(blank=True)
    company_description = models.TextField(blank=True)
    company_industry = models.CharField(max_length=120, blank=True)
    career_summary = models.TextField(blank=True)
    linkedin_url = models.URLField(blank=True)
    github_url = models.URLField(blank=True)
    portfolio_url = models.URLField(blank=True)
    preferred_roles = models.CharField(max_length=240, blank=True)
    preferred_locations = models.CharField(max_length=240, blank=True)
    preferred_category = models.CharField(max_length=120, blank=True)
    preferred_employment_type = models.CharField(
        max_length=60,
        blank=True,
        choices=[
            ("", "Any"),
            ("Full-time", "Full-time"),
            ("Part-time", "Part-time"),
            ("Contract", "Contract"),
            ("Internship", "Internship"),
        ],
    )
    preferred_work_mode = models.CharField(
        max_length=20,
        blank=True,
        choices=[
            ("", "Any"),
            ("on_site", "On-site"),
            ("hybrid", "Hybrid"),
            ("remote", "Remote"),
        ],
    )
    preferred_salary_min = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Minimum annual salary in INR, if relevant.",
    )
    preferred_salary_max = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Maximum annual salary in INR, if relevant.",
    )
    discoverable = models.BooleanField(
        default=False,
        help_text="Allow recruiters to find your resume in candidate recommendations.",
    )

    def clean(self):
        super().clean()
        if (
            self.preferred_salary_min is not None
            and self.preferred_salary_max is not None
            and self.preferred_salary_max < self.preferred_salary_min
        ):
            raise ValidationError(
                {"preferred_salary_max": "Maximum salary must be at least the minimum."}
            )

    def __str__(self): return f"{self.user.username} ({self.role})"

class Resume(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="resumes")
    file = models.FileField(upload_to="resumes/", validators=[FileExtensionValidator(["pdf","docx","txt"])])
    extracted_text = models.TextField(blank=True)
    extracted_skills = models.JSONField(default=list, blank=True)
    extracted_education = models.JSONField(default=list, blank=True)
    professional_summary = models.TextField(blank=True)
    language = models.CharField(max_length=30, blank=True, help_text="Detected language of the resume text.")
    extracted_projects = models.JSONField(default=list, blank=True)
    extracted_certifications = models.JSONField(default=list, blank=True)
    experience_years = models.DecimalField(
        max_digits=4, decimal_places=1, default=0, validators=[MinValueValidator(0)]
    )
    internship_experience_years = models.DecimalField(
        max_digits=4, decimal_places=1, default=0,
        validators=[MinValueValidator(0)],
    )
    predicted_category = models.CharField(max_length=120, blank=True)
    category_confidence = models.FloatField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(1)]
    )
    ats_score = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(100)]
    )
    improvement_tips = models.JSONField(default=list, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    def __str__(self): return f"Resume: {self.user.username}"
class ResumeParsingResult(models.Model):
    """Stores the structured output of the resume parser.
    Linked one‑to‑one with a :class:`Resume` instance.
    """
    resume = models.OneToOneField(Resume, on_delete=models.CASCADE, related_name="parsing_result")
    parsed_at = models.DateTimeField(auto_now_add=True)
    extracted_text = models.TextField(blank=True)
    sections = models.JSONField(default=dict, blank=True, help_text="Detected sections with line ranges")
    normalized_skills = models.JSONField(default=list, blank=True, help_text="List of skill dicts {name, category, evidence, strength}")
    education = models.JSONField(default=list, blank=True, help_text="Extracted education entries")
    projects = models.JSONField(default=list, blank=True, help_text="Extracted project entries")
    certifications = models.JSONField(default=list, blank=True, help_text="Extracted certification entries")
    parsing_warnings = models.JSONField(default=list, blank=True, help_text="Any issues encountered during parsing")
    candidate_info = models.JSONField(default=dict, blank=True, help_text="Extracted candidate name, email, phone")
    def __str__(self):
        return f"ParsingResult for {self.resume.user.username}"
class Job(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("open", "Open"),
        ("closed", "Closed"),
        ("filled", "Filled"),
    ]
    WORK_MODE_CHOICES = [
        ("on_site", "On-site"),
        ("hybrid", "Hybrid"),
        ("remote", "Remote"),
    ]
    recruiter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="posted_jobs")
    title = models.CharField(max_length=180)
    company = models.CharField(max_length=180)
    company_logo = models.ImageField(upload_to='company_logos/', blank=True, null=True)
    location = models.CharField(max_length=120, blank=True)
    employment_type = models.CharField(max_length=60, default="Full-time")
    experience_level = models.CharField(max_length=60, default="Entry level")
    required_experience_years = models.FloatField(default=0, validators=[MinValueValidator(0)])
    max_experience_years = models.FloatField(
        null=True, blank=True, validators=[MinValueValidator(0)]
    )
    salary = models.CharField(max_length=100, blank=True)
    industry = models.CharField(max_length=120, blank=True)
    category = models.CharField(max_length=120, blank=True)
    work_mode = models.CharField(max_length=20, choices=WORK_MODE_CHOICES, default="on_site")
    required_skills = models.TextField(help_text="Comma-separated skills")
    preferred_skills = models.TextField(blank=True)
    education_requirements = models.TextField(blank=True)
    qualifications = models.TextField(blank=True)
    responsibilities = models.TextField(blank=True)
    description = models.TextField()
    responsibilities = models.TextField(blank=True, help_text='Optional detailed responsibilities')
    qualifications = models.TextField(blank=True, help_text='Optional qualifications')
    benefits = models.TextField(blank=True, help_text='Optional benefits offered')
    application_url = models.URLField(blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    def clean(self):
        super().clean()
        if (
            self.max_experience_years is not None
            and self.max_experience_years < self.required_experience_years
        ):
            raise ValidationError({
                "max_experience_years": (
                    "Maximum experience must be greater than or equal to the minimum."
                )
            })
    class Meta:
        unique_together = (('title', 'company', 'location'),)
        indexes = [
            models.Index(fields=['title'], name='idx_job_title'),
            models.Index(fields=['category'], name='idx_job_category'),
            models.Index(fields=['is_active'], name='idx_job_active'),
            models.Index(
                fields=["status", "is_active", "created_at"],
                name="idx_job_open_recent",
            ),
        ]

class Application(models.Model):
    STATUS_CHOICES = [
        ("submitted", "Applied"),
        ("resume_viewed", "Resume viewed"),
        ("reviewing", "Under review"),
        ("shortlisted", "Shortlisted"),
        ("interview_scheduled", "Interview scheduled"),
        ("selected", "Selected"),
        ("rejected", "Rejected"),
        ("withdrawn", "Withdrawn"),
    ]
    job = models.ForeignKey(
        Job, on_delete=models.CASCADE, related_name="applications",
        null=True, blank=True,
    )
    recruiter = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="received_applications",
    )
    applicant = models.ForeignKey(User, on_delete=models.CASCADE, related_name="applications")
    resume = models.ForeignKey(Resume, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_resume = models.FileField(
        upload_to="application_resumes/", blank=True,
        validators=[FileExtensionValidator(["pdf", "doc", "docx"])],
    )
    applicant_name = models.CharField(max_length=180, blank=True)
    applicant_email = models.EmailField(blank=True)
    applicant_phone = models.CharField(max_length=30, blank=True)
    applicant_location = models.CharField(max_length=180, blank=True)
    current_job_title = models.CharField(max_length=180, blank=True)
    experience_years = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    highest_qualification = models.CharField(max_length=180, blank=True)
    graduation_year = models.PositiveSmallIntegerField(null=True, blank=True)
    technical_skills = models.TextField(blank=True)
    certifications = models.TextField(blank=True)
    cover_letter = models.TextField(blank=True)
    why_suitable = models.TextField(blank=True)
    expected_salary = models.CharField(max_length=100, blank=True)
    notice_period = models.CharField(max_length=100, blank=True)
    preferred_work_mode = models.CharField(
        max_length=20, choices=Job.WORK_MODE_CHOICES, blank=True,
    )
    portfolio_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    github_url = models.URLField(blank=True)
    availability_date = models.DateField(null=True, blank=True)
    additional_info = models.TextField(blank=True)
    consent_to_share = models.BooleanField(default=False)
    source = models.CharField(
        max_length=20, choices=[("database", "Database"), ("dataset", "Dataset")],
        default="database",
    )
    external_job_key = models.CharField(max_length=64, blank=True)
    job_title_snapshot = models.CharField(max_length=180, blank=True)
    job_company_snapshot = models.CharField(max_length=180, blank=True)
    application_url_snapshot = models.URLField(blank=True)
    match_score = models.FloatField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="submitted")
    created_at = models.DateTimeField(auto_now_add=True)
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    interview_at = models.DateTimeField(null=True, blank=True)
    interview_details = models.TextField(blank=True)
    interview_mode = models.CharField(max_length=40, blank=True)
    interview_link = models.URLField(blank=True)
    recruiter_notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        unique_together = ("job", "applicant")
        constraints = [
            models.UniqueConstraint(
                fields=("applicant", "external_job_key"),
                condition=models.Q(source="dataset") & ~models.Q(external_job_key=""),
                name="unique_dataset_job_application_per_applicant",
            ),
        ]
        ordering = ["-created_at"]
    def __str__(self): return f"{self.applicant} → {self.job}"

    @property
    def display_title(self):
        return self.job_title_snapshot or (self.job.title if self.job_id else "")

    @property
    def display_company(self):
        return self.job_company_snapshot or (self.job.company if self.job_id else "")


class SavedJob(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_jobs")
    job = models.ForeignKey(
        Job, on_delete=models.CASCADE, related_name="saved_by",
        null=True, blank=True,
    )
    source = models.CharField(
        max_length=20,
        choices=[("database", "Database"), ("dataset", "Dataset")],
        default="database",
    )
    source_key = models.CharField(max_length=64, blank=True)
    title_snapshot = models.CharField(max_length=180, blank=True)
    company_snapshot = models.CharField(max_length=180, blank=True)
    location_snapshot = models.CharField(max_length=180, blank=True)
    salary_snapshot = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "job"], condition=models.Q(job__isnull=False),
                name="unique_saved_job_per_user",
            ),
            models.UniqueConstraint(
                fields=["user", "source", "source_key"],
                condition=models.Q(job__isnull=True, source="dataset")
                & ~models.Q(source_key=""),
                name="unique_saved_dataset_job_per_user",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} saved {self.job}"


class ResumeBuilder(models.Model):
    TEMPLATE_CHOICES = [
        ("modern", "Modern Professional"),
        ("classic", "Classic Corporate"),
        ("minimal", "Minimal ATS"),
        ("developer", "Technical Developer"),
        ("data_science", "AI and Data Science Professional"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='resume_builders')
    full_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    city_state = models.CharField(max_length=255, blank=True)
    linkedin = models.URLField(blank=True)
    github = models.URLField(blank=True)
    portfolio = models.URLField(blank=True)
    professional_summary = models.TextField(blank=True)
    target_job_title = models.CharField(max_length=255, blank=True)
    career_objective = models.TextField(blank=True)
    education = models.TextField(blank=True, help_text='One entry per line')
    technical_skills = models.TextField(blank=True, help_text='Comma-separated or one per line')
    work_experience = models.TextField(blank=True, help_text='One entry per line')
    projects = models.TextField(blank=True, help_text='One entry per line')
    certifications = models.TextField(blank=True, help_text='One entry per line')
    awards = models.TextField(blank=True, help_text='One entry per line')
    training = models.TextField(blank=True, help_text='One entry per line')
    template = models.CharField(
        max_length=24, choices=TEMPLATE_CHOICES, default="modern"
    )
    generated_pdf = models.FileField(
        upload_to="generated_resumes/", blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Resume Builder for {self.user.username} ({self.full_name})"



class JobRecommendationFeedback(models.Model):
    SOURCE_CHOICES = [("database", "Database"), ("dataset", "Dataset")]
    REASON_CHOICES = [
        ("relevant", "Relevant"),
        ("not_relevant", "Not relevant"),
        ("wrong_category", "Wrong job category"),
        ("skills_mismatch", "Skills do not match"),
        ("experience_mismatch", "Experience does not match"),
        ("not_interested", "Not interested"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="recommendation_feedback"
    )
    job = models.ForeignKey(
        Job, on_delete=models.CASCADE, null=True, blank=True,
        related_name="recommendation_feedback",
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    source_key = models.CharField(max_length=64)
    is_relevant = models.BooleanField()
    reason = models.CharField(max_length=32, choices=REASON_CHOICES, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "job"),
                condition=models.Q(job__isnull=False),
                name="unique_user_job_recommendation_feedback",
            ),
            models.UniqueConstraint(
                fields=("user", "source", "source_key"),
                condition=models.Q(job__isnull=True, source="dataset"),
                name="unique_user_dataset_recommendation_feedback",
            ),
        ]

    def __str__(self):
        return f"{self.user} feedback for {self.source}:{self.source_key}"


class ApplicationStatusHistory(models.Model):
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="status_history"
    )
    status = models.CharField(max_length=20, choices=Application.STATUS_CHOICES)
    previous_status = models.CharField(
        max_length=20, choices=Application.STATUS_CHOICES, blank=True,
    )
    changed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    description = models.CharField(
        max_length=500, blank=True,
        help_text="Audit description for this application event.",
    )
    recruiter_remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["changed_at", "pk"]

    def __str__(self):
        return f"{self.application.reference}: {self.get_status_display()}"


class InterviewSchedule(models.Model):
    MODE_CHOICES = [
        ("onsite", "On-site"),
        ("phone", "Phone"),
        ("video", "Video"),
    ]

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="interviews"
    )
    scheduled_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="scheduled_interviews",
    )
    starts_at = models.DateTimeField()
    mode = models.CharField(max_length=20, choices=MODE_CHOICES)
    meeting_link = models.URLField(blank=True)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at", "pk"]

    def __str__(self):
        return f"Interview for {self.application.reference} at {self.starts_at}"


class Notification(models.Model):
    recipient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="notifications"
    )
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, null=True, blank=True,
        related_name="notifications",
    )
    job = models.ForeignKey(
        Job, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="notifications",
    )
    title = models.CharField(max_length=180)
    message = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.recipient}: {self.title}"


class JobAlert(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="job_alerts"
    )
    name = models.CharField(max_length=100)
    preferred_titles = models.CharField(max_length=240, blank=True)
    skills = models.CharField(max_length=500, blank=True)
    locations = models.CharField(max_length=240, blank=True)
    salary_min = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    salary_max = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    employment_type = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["user", "is_active"], name="idx_alert_user_active"),
        ]

    def clean(self):
        super().clean()
        if self.salary_min is not None and self.salary_max is not None:
            if self.salary_max < self.salary_min:
                raise ValidationError(
                    {"salary_max": "Maximum salary must be at least the minimum."}
                )
        if not any((self.preferred_titles, self.skills, self.locations)):
            raise ValidationError(
                "Enter a job title, skill, or location to define this alert."
            )

    def __str__(self):
        return f"{self.name} ({self.user.username})"


class RecentlyViewedJob(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="recently_viewed_jobs"
    )
    job = models.ForeignKey(
        Job, on_delete=models.CASCADE, related_name="recent_views"
    )
    viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-viewed_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "job"], name="unique_recent_job_view_per_user"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-viewed_at"], name="idx_recent_view_user"),
        ]


class RecommendationRun(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="recommendation_runs"
    )
    resume = models.OneToOneField(
        Resume, on_delete=models.CASCADE, related_name="recommendation_run"
    )
    input_hash = models.CharField(max_length=64)
    job_version = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="queued"
    )
    task_id = models.CharField(max_length=255, blank=True)
    recommendations = models.JSONField(default=list, blank=True)
    profile = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["user", "status"],
                name="idx_recommend_run_user_status",
            ),
        ]


class MockInterviewSession(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="mock_interview_sessions"
    )
    job = models.ForeignKey(
        Job, on_delete=models.PROTECT, related_name="mock_interview_sessions"
    )
    resume = models.ForeignKey(
        Resume, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="mock_interview_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-pk"]
        indexes = [
            models.Index(
                fields=["user", "updated_at"],
                name="idx_mock_session_user_updated",
            ),
        ]

    def __str__(self):
        return f"Mock interview: {self.user} / {self.job}"


class MockInterviewAnswer(models.Model):
    CATEGORY_CHOICES = [
        ("technical", "Technical"),
        ("behavioral", "Behavioral"),
        ("hr", "HR"),
    ]

    session = models.ForeignKey(
        MockInterviewSession, on_delete=models.CASCADE, related_name="answers"
    )
    question_key = models.SlugField(max_length=80)
    category = models.CharField(max_length=16, choices=CATEGORY_CHOICES)
    question = models.CharField(max_length=500)
    targeted_skill = models.CharField(max_length=80, blank=True)
    answer = models.TextField(blank=True)
    feedback = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "question_key"],
                name="unique_mock_answer_question",
            ),
        ]

    def __str__(self):
        return f"{self.get_category_display()} question for {self.session_id}"


class ChatConversation(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="chatbot_conversations"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-pk"]
        indexes = [
            models.Index(fields=["user", "-updated_at"], name="idx_chat_user_updated"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user"], name="unique_user_chat_conversation"
            ),
        ]


class ChatMessage(models.Model):
    SENDER_CHOICES = [("user", "User"), ("assistant", "Assistant")]
    conversation = models.ForeignKey(
        ChatConversation, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES)
    content = models.CharField(max_length=2000)
    intent = models.CharField(max_length=40, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "pk"]


class CareerRoadmapMilestone(models.Model):
    STAGE_CHOICES = [
        ("beginner", "Beginner"),
        ("intermediate", "Intermediate"),
        ("advanced", "Advanced"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="career_milestones"
    )
    job = models.ForeignKey(
        Job, on_delete=models.CASCADE, related_name="career_milestones"
    )
    skill = models.CharField(max_length=80)
    stage = models.CharField(max_length=16, choices=STAGE_CHOICES)
    project_idea = models.CharField(max_length=500)
    learning_resource = models.CharField(max_length=200)
    learning_resource_url = models.URLField(blank=True)
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["stage", "skill"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "job", "skill"],
                name="unique_user_job_roadmap_skill",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "job", "is_completed"],
                name="idx_roadmap_user_job_done",
            ),
        ]

    def __str__(self):
        return f"{self.skill} roadmap item for {self.user}"
