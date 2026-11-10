from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone
from zipfile import BadZipFile, ZipFile
from .models import (
    UserProfile, Resume, Job, Application, ResumeBuilder,
)

class RegisterForm(UserCreationForm):
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    role = forms.ChoiceField(choices=UserProfile.ROLE_CHOICES, widget=forms.RadioSelect)
    class Meta:
        model = User
        fields = ("username","first_name","last_name","email","password1","password2","role")
    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account already uses this email address.")
        return email
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"].strip().lower()
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        if commit:
            user.save()
            UserProfile.objects.create(user=user, role=self.cleaned_data["role"])
        return user

class ProfileForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150, required=False, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    email = forms.EmailField(required=True, label="Email address")

    class Meta:
        model = UserProfile
        exclude = ("user","role")
        labels = {
            "headline": "Professional headline / current role",
            "career_summary": "Professional summary",
            "education": "Education (degree, institution, year)",
            "skills": "Technical and professional skills",
            "preferred_salary_min": "Minimum expected annual salary (INR)",
            "preferred_salary_max": "Maximum expected annual salary (INR)",
        }
        widgets = {
            "skills": forms.Textarea(attrs={"rows":3}),
            "company_description": forms.Textarea(attrs={"rows":4}),
            "career_summary": forms.Textarea(attrs={"rows":4}),
            "profile_image": forms.ClearableFileInput(
                attrs={
                    "accept": "image/png,image/jpeg,image/webp",
                    "data-profile-image-input": "",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.user = user or (self.instance.user if self.instance.pk else None)
        if self.instance.role != "recruiter":
            for name in (
                "company_name", "company_website", "company_description",
                "company_industry",
            ):
                self.fields.pop(name, None)
        if self.user:
            self.fields["first_name"].initial = self.user.first_name
            self.fields["last_name"].initial = self.user.last_name
            self.fields["email"].initial = self.user.email

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        users = User.objects.filter(email__iexact=email)
        if self.user:
            users = users.exclude(pk=self.user.pk)
        if users.exists():
            raise forms.ValidationError("An account already uses this email address.")
        return email

    def clean_profile_image(self):
        image = self.cleaned_data.get("profile_image")
        if image and image.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Profile images must be no larger than 2 MB.")
        return image

class ResumeForm(forms.ModelForm):
    class Meta:
        model = Resume
        fields = ("file",)
        help_texts = {"file":"PDF, DOCX or TXT; maximum 5 MB."}

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if uploaded.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Maximum file size is 5 MB.")
        extension = uploaded.name.rsplit(".", 1)[-1].lower()
        uploaded.seek(0)
        header = uploaded.read(8)
        uploaded.seek(0)
        if extension == "pdf" and not header.startswith(b"%PDF-"):
            raise forms.ValidationError("The selected file is not a valid PDF.")
        if extension == "docx":
            try:
                with ZipFile(uploaded) as archive:
                    names = set(archive.namelist())
                    total_size = sum(info.file_size for info in archive.infolist())
                    valid_docx = {
                        "[Content_Types].xml", "word/document.xml"
                    }.issubset(names)
                if not valid_docx or total_size > 20 * 1024 * 1024:
                    raise forms.ValidationError("The selected file is not a valid DOCX document.")
            except BadZipFile:
                raise forms.ValidationError("The selected file is not a valid DOCX document.")
            finally:
                uploaded.seek(0)
        if extension == "txt":
            try:
                uploaded.read().decode("utf-8")
            except UnicodeDecodeError:
                raise forms.ValidationError("Text resumes must use UTF-8 encoding.")
            finally:
                uploaded.seek(0)
        return uploaded

class JobForm(forms.ModelForm):
    deadline = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type":"datetime-local"},format="%Y-%m-%dT%H:%M"),
    )

    class Meta:
        model = Job
        fields = (
            "title", "company", "company_logo", "description", "required_skills", "preferred_skills",
            "category", "experience_level", "required_experience_years",
            "max_experience_years",
            "education_requirements", "qualifications", "industry", "benefits", "location",
            "employment_type", "work_mode", "salary", "responsibilities",
            "application_url", "deadline",
        )
        widgets = {
            "company_logo": forms.ClearableFileInput(attrs={
                "accept": "image/png,image/jpeg,image/webp",
            }),
            "description": forms.Textarea(attrs={"rows":6}),
            "required_skills": forms.Textarea(attrs={"rows":2,"placeholder":"Python, SQL, Django, Machine Learning"}),
            "preferred_skills": forms.Textarea(attrs={"rows":2}),
            "education_requirements": forms.Textarea(attrs={"rows":2}),
            "qualifications": forms.Textarea(attrs={"rows":3}),
            "benefits": forms.Textarea(attrs={"rows":3}),
            "responsibilities": forms.Textarea(attrs={"rows":4}),
        }

    def clean_company_logo(self):
        logo = self.cleaned_data.get("company_logo")
        if logo and logo.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Company logos must be no larger than 2 MB.")
        return logo

    def save(self, commit=True):
        job = super().save(commit=False)
        job.expires_at = job.deadline
        if commit:
            job.save()
            self.save_m2m()
        return job


class AdminJobForm(JobForm):
    class Meta(JobForm.Meta):
        fields = JobForm.Meta.fields + ("status",)

    def save(self, commit=True):
        job = super().save(commit=False)
        job.is_active = job.status == "open"
        if commit:
            job.save()
            self.save_m2m()
        return job


class ContactForm(forms.Form):
    name = forms.CharField(max_length=120)
    email = forms.EmailField()
    message = forms.CharField(max_length=4000, widget=forms.Textarea(attrs={"rows": 5}))


class ApplicationStatusForm(forms.Form):
    status = forms.ChoiceField(choices=Application.STATUS_CHOICES)
    interview_at = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    interview_details = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 2}))
    interview_mode = forms.ChoiceField(
        required=False,
        choices=[("", "Choose mode"), ("onsite", "On-site"), ("phone", "Phone"), ("video", "Video")],
    )
    interview_link = forms.URLField(required=False, max_length=200)
    recruiter_notes = forms.CharField(
        required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 3})
    )
    candidate_remarks = forms.CharField(
        required=False,
        max_length=500,
        label="Message for candidate",
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("status") == "interview_scheduled":
            if not cleaned_data.get("interview_at"):
                self.add_error("interview_at", "Add the interview date and time.")
            if not cleaned_data.get("interview_mode"):
                self.add_error("interview_mode", "Choose an interview mode.")
            if cleaned_data.get("interview_mode") == "video" and not cleaned_data.get("interview_link"):
                self.add_error("interview_link", "Add the video meeting link.")
        return cleaned_data

class ApplicationForm(forms.ModelForm):
    resume = forms.ModelChoiceField(
        queryset=Resume.objects.none(), required=False, label="Use a profile resume"
    )
    uploaded_resume = forms.FileField(
        required=False, label="Upload a resume (PDF, DOC, or DOCX)",
    )
    consent_to_share = forms.BooleanField(
        required=True,
        label="I consent to sharing these application details and my resume with the recruiter.",
    )

    class Meta:
        model = Application
        fields = (
            "resume", "uploaded_resume", "applicant_name", "applicant_email",
            "applicant_phone", "applicant_location", "current_job_title",
            "experience_years", "highest_qualification", "graduation_year",
            "technical_skills", "certifications", "cover_letter", "why_suitable",
            "expected_salary", "notice_period", "preferred_work_mode",
            "portfolio_url", "linkedin_url", "github_url", "availability_date",
            "additional_info", "consent_to_share",
        )
        widgets = {
            "experience_years": forms.NumberInput(attrs={"min": "0", "step": "0.5"}),
            "graduation_year": forms.NumberInput(attrs={"min": "1950", "max": str(timezone.localdate().year + 10)}),
            "technical_skills": forms.Textarea(attrs={"rows": 2}),
            "certifications": forms.Textarea(attrs={"rows": 2}),
            "cover_letter": forms.Textarea(attrs={"rows": 4}),
            "why_suitable": forms.Textarea(attrs={"rows": 4}),
            "additional_info": forms.Textarea(attrs={"rows": 3}),
            "availability_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, user, profile_resume=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["resume"].queryset = Resume.objects.filter(
            user=user, is_active=True
        ).order_by("-uploaded_at")
        self.fields["resume"].label_from_instance = lambda resume: (
            f"{resume.file.name.rsplit('/', 1)[-1]} · {resume.uploaded_at:%b %d, %Y}"
        )
        self.fields["applicant_name"].required = True
        self.fields["applicant_email"].required = True
        self.fields["applicant_phone"].required = True
        self.fields["applicant_location"].required = True
        self.fields["experience_years"].required = False
        self.fields["graduation_year"].required = False
        self.fields["availability_date"].required = False
        self.fields["preferred_work_mode"].required = False

        profile = getattr(user, "profile", None)
        resume = profile_resume
        candidate_info = {}
        if resume:
            try:
                candidate_info = resume.parsing_result.candidate_info
            except Resume.parsing_result.RelatedObjectDoesNotExist:
                candidate_info = {}
        if not self.is_bound:
            self.initial.setdefault("applicant_name", user.get_full_name() or user.username)
            self.initial.setdefault("applicant_email", user.email)
            self.initial.setdefault("applicant_phone", getattr(profile, "phone", ""))
            self.initial.setdefault("applicant_location", getattr(profile, "location", ""))
            self.initial.setdefault("current_job_title", getattr(profile, "headline", ""))
            self.initial.setdefault(
                "experience_years",
                getattr(resume, "experience_years", None)
                or getattr(profile, "experience_years", None),
            )
            education = getattr(resume, "extracted_education", []) if resume else []
            self.initial.setdefault(
                "highest_qualification",
                ", ".join(education) if education else getattr(profile, "education", ""),
            )
            skills = getattr(resume, "extracted_skills", []) if resume else []
            self.initial.setdefault(
                "technical_skills",
                ", ".join(skills) if skills else getattr(profile, "skills", ""),
            )
            certifications = getattr(resume, "extracted_certifications", []) if resume else []
            self.initial.setdefault("certifications", "; ".join(certifications))
            self.initial.setdefault("portfolio_url", getattr(profile, "portfolio_url", ""))
            self.initial.setdefault("linkedin_url", getattr(profile, "linkedin_url", ""))
            self.initial.setdefault("github_url", getattr(profile, "github_url", ""))
            self.initial.setdefault("resume", resume)
            self.initial.setdefault("applicant_name", candidate_info.get("name") or self.initial["applicant_name"])
            self.initial.setdefault("applicant_email", candidate_info.get("email") or self.initial["applicant_email"])
            self.initial.setdefault("applicant_phone", candidate_info.get("phone") or self.initial["applicant_phone"])

    def clean_uploaded_resume(self):
        uploaded = self.cleaned_data.get("uploaded_resume")
        if not uploaded:
            return uploaded
        if uploaded.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Maximum file size is 5 MB.")
        extension = uploaded.name.rsplit(".", 1)[-1].lower()
        uploaded.seek(0)
        header = uploaded.read(8)
        uploaded.seek(0)
        if extension == "pdf" and not header.startswith(b"%PDF-"):
            raise forms.ValidationError("The selected file is not a valid PDF.")
        if extension == "doc" and not header.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
            raise forms.ValidationError("The selected file is not a valid DOC document.")
        if extension == "docx":
            try:
                with ZipFile(uploaded) as archive:
                    names = set(archive.namelist())
                    total_size = sum(info.file_size for info in archive.infolist())
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                    raise forms.ValidationError("The selected file is not a valid DOCX document.")
                if total_size > 20 * 1024 * 1024:
                    raise forms.ValidationError("The DOCX document is too large to process safely.")
            except BadZipFile:
                raise forms.ValidationError("The selected file is not a valid DOCX document.")
            finally:
                uploaded.seek(0)
        return uploaded

    def clean(self):
        cleaned_data = super().clean()
        selected_resume = cleaned_data.get("resume")
        uploaded_resume = cleaned_data.get("uploaded_resume")
        if bool(selected_resume) == bool(uploaded_resume):
            raise forms.ValidationError("Choose a profile resume or upload one resume file.")
        if selected_resume and (
            selected_resume.user_id != self.user.pk or not selected_resume.is_active
        ):
            self.add_error("resume", "Choose one of your active profile resumes.")
        if not cleaned_data.get("consent_to_share"):
            self.add_error("consent_to_share", "Consent is required to submit this application.")
        return cleaned_data

    def clean_resume(self):
        resume = self.cleaned_data["resume"]
        if resume.user_id != self.user.pk or not resume.is_active:
            raise forms.ValidationError("Choose one of your active resumes.")
        return resume


class ATSResumeBuilderForm(forms.Form):
    template = forms.ChoiceField(
        label="Resume template",
        required=False,
        initial="modern",
        choices=ResumeBuilder.TEMPLATE_CHOICES,
    )
    # Personal Information
    full_name = forms.CharField(label="Full name", max_length=150, required=True)
    email = forms.EmailField(label="Professional email", required=True)
    phone = forms.CharField(label="Phone number", max_length=30, required=False)
    city_state = forms.CharField(label="City and state", max_length=100, required=False)
    linkedin = forms.URLField(label="LinkedIn profile", required=False)
    github = forms.URLField(label="GitHub profile", required=False)
    portfolio = forms.URLField(label="Portfolio website", required=False)

    # Professional Information
    professional_summary = forms.CharField(label="Professional summary", widget=forms.Textarea(attrs={"rows": 4}), required=False)
    target_job_title = forms.CharField(label="Target job title", max_length=100, required=False)
    career_objective = forms.CharField(label="Career objective", widget=forms.Textarea(attrs={"rows": 3}), required=False)

    # Education
    education = forms.CharField(label="Education (institution, degree, year, grades)", widget=forms.Textarea(attrs={"rows": 3}), required=False)

    # Technical Skills and Tools
    technical_skills = forms.CharField(label="Technical skills and tools", widget=forms.Textarea(attrs={"rows": 3}), required=False)

    # Experience
    experience = forms.CharField(label="Work experience (company, role, dates, achievements)", widget=forms.Textarea(attrs={"rows": 5}), required=False)

    # Projects
    projects = forms.CharField(label="Projects (description, technologies, achievements)", widget=forms.Textarea(attrs={"rows": 5}), required=False)

    # Certifications
    certifications = forms.CharField(label="Certifications", widget=forms.Textarea(attrs={"rows": 3}), required=False)

    # Awards and Accomplishments
    awards = forms.CharField(label="Awards and accomplishments", widget=forms.Textarea(attrs={"rows": 3}), required=False)

    # Training / Extracurricular
    training = forms.CharField(label="Relevant training / extracurricular activities", widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("template"):
            cleaned_data["template"] = "modern"
        # Ensure at least one contact method is provided
        if not cleaned_data.get("email") and not cleaned_data.get("phone"):
            raise forms.ValidationError("Provide at least an email or phone number.")
        return cleaned_data


class ResumeReviewForm(forms.Form):
    professional_summary = forms.CharField(
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Review the extracted text. Do not add information that is not yours.",
    )
    predicted_category = forms.CharField(
        required=False,
        max_length=120,
        label="Detected career domain",
    )
    skills = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="One skill per line, or separate skills with commas.",
    )
    education = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    experience_years = forms.DecimalField(
        min_value=0, max_value=99.9, decimal_places=1, max_digits=4,
        required=True,
    )
    internship_experience_years = forms.DecimalField(
        min_value=0, max_value=99.9, decimal_places=1, max_digits=4,
        required=True,
    )
    projects = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    certifications = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    def __init__(self, *args, resume, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
        if not self.is_bound:
            self.initial.setdefault("professional_summary", resume.professional_summary)
            self.initial.setdefault("predicted_category", resume.predicted_category)
            self.initial.setdefault("skills", "\n".join(resume.extracted_skills or []))
            self.initial.setdefault("education", "\n".join(resume.extracted_education or []))
            self.initial.setdefault("experience_years", resume.experience_years)
            self.initial.setdefault(
                "internship_experience_years",
                resume.internship_experience_years,
            )
            self.initial.setdefault("projects", "\n".join(resume.extracted_projects or []))
            self.initial.setdefault(
                "certifications",
                "\n".join(resume.extracted_certifications or []),
            )

    @staticmethod
    def _list_value(value):
        items = []
        seen = set()
        for line in value.splitlines():
            for item in line.split(","):
                item = item.strip().lstrip("•*- ").strip()
                key = item.casefold()
                if item and key not in seen:
                    items.append(item)
                    seen.add(key)
        return items

    def cleaned_lists(self):
        return {
            field: self._list_value(self.cleaned_data[field])
            for field in ("skills", "education", "projects", "certifications")
        }
