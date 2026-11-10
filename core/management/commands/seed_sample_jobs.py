from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Job


SAMPLE_JOBS = (
    {
        "title": "Python Developer",
        "company": "Northstar Digital",
        "category": "Software Engineering",
        "industry": "Technology",
        "required_skills": "Python, REST APIs, Git, SQL",
        "preferred_skills": "FastAPI, Docker, AWS",
        "experience_level": "Mid level",
        "required_experience_years": 2,
        "max_experience_years": 5,
        "education_requirements": "Bachelor's degree in Computer Science or equivalent experience",
        "qualifications": "Production Python experience and automated testing skills",
        "location": "Remote",
        "employment_type": "Full-time",
        "work_mode": "remote",
        "salary": "INR 12-20 LPA",
        "responsibilities": "Design APIs, review code, improve service reliability, and maintain automated tests.",
        "description": "Sample listing for local testing. Build and operate Python services used by customer-facing products.",
        "application_url": "https://example.com/careers/python-developer",
    },
    {
        "title": "Django Developer",
        "company": "Cedar Labs",
        "category": "Web Development",
        "industry": "Technology",
        "required_skills": "Python, Django, PostgreSQL, HTML, CSS",
        "preferred_skills": "Redis, Celery, Docker",
        "experience_level": "Mid level",
        "required_experience_years": 2,
        "max_experience_years": 6,
        "education_requirements": "Degree or equivalent practical experience in software development",
        "qualifications": "Experience shipping secure Django applications",
        "location": "Chennai, India",
        "employment_type": "Full-time",
        "work_mode": "hybrid",
        "salary": "INR 10-18 LPA",
        "responsibilities": "Develop Django features, improve database performance, and maintain deployment pipelines.",
        "description": "Sample listing for local testing. Join a product team building maintainable Django web applications.",
        "application_url": "https://example.com/careers/django-developer",
    },
    {
        "title": "Data Scientist",
        "company": "Blue Oak Analytics",
        "category": "Data Science",
        "industry": "Analytics",
        "required_skills": "Python, SQL, Statistics, Pandas, Scikit-learn",
        "preferred_skills": "Experiment design, Tableau, Spark",
        "experience_level": "Mid level",
        "required_experience_years": 3,
        "max_experience_years": 7,
        "education_requirements": "Bachelor's or Master's degree in Statistics, Mathematics, or a related field",
        "qualifications": "Demonstrated model evaluation and data storytelling experience",
        "location": "Bengaluru, India",
        "employment_type": "Full-time",
        "work_mode": "hybrid",
        "salary": "INR 16-28 LPA",
        "responsibilities": "Analyze product data, build predictive models, and communicate findings to stakeholders.",
        "description": "Sample listing for local testing. Apply statistical and machine-learning methods to real product questions.",
        "application_url": "https://example.com/careers/data-scientist",
    },
    {
        "title": "Data Analyst",
        "company": "Harbor Metrics",
        "category": "Data Analytics",
        "industry": "Analytics",
        "required_skills": "SQL, Excel, Data Visualization, Statistics",
        "preferred_skills": "Python, Power BI, Tableau",
        "experience_level": "Entry level",
        "required_experience_years": 0,
        "max_experience_years": 3,
        "education_requirements": "Bachelor's degree in a quantitative or business discipline",
        "qualifications": "Strong attention to detail and ability to explain analytical findings",
        "location": "Remote",
        "employment_type": "Full-time",
        "work_mode": "remote",
        "salary": "INR 7-12 LPA",
        "responsibilities": "Prepare dashboards, validate data, and answer recurring business questions.",
        "description": "Sample listing for local testing. Turn reliable datasets into clear operational reports and insights.",
        "application_url": "https://example.com/careers/data-analyst",
    },
    {
        "title": "ML Engineer",
        "company": "Juniper Intelligence",
        "category": "Machine Learning",
        "industry": "Artificial Intelligence",
        "required_skills": "Python, Machine Learning, Scikit-learn, SQL",
        "preferred_skills": "PyTorch, Docker, Kubernetes, MLflow",
        "experience_level": "Mid level",
        "required_experience_years": 3,
        "max_experience_years": 8,
        "education_requirements": "Degree in Computer Science, Data Science, or equivalent experience",
        "qualifications": "Experience deploying and monitoring machine-learning models",
        "location": "Hyderabad, India",
        "employment_type": "Full-time",
        "work_mode": "hybrid",
        "salary": "INR 18-32 LPA",
        "responsibilities": "Build model pipelines, productionize inference services, and monitor model quality.",
        "description": "Sample listing for local testing. Deliver reliable machine-learning features from experimentation through production.",
        "application_url": "https://example.com/careers/ml-engineer",
    },
    {
        "title": "AI Engineer",
        "company": "Meridian AI Works",
        "category": "Artificial Intelligence",
        "industry": "Artificial Intelligence",
        "required_skills": "Python, Natural Language Processing, Machine Learning, APIs",
        "preferred_skills": "PyTorch, LLMs, Vector Databases, Docker",
        "experience_level": "Mid level",
        "required_experience_years": 2,
        "max_experience_years": 7,
        "education_requirements": "Degree in Computer Science, AI, or a related field",
        "qualifications": "Experience evaluating and integrating AI models into applications",
        "location": "Remote",
        "employment_type": "Full-time",
        "work_mode": "remote",
        "salary": "INR 18-35 LPA",
        "responsibilities": "Prototype AI capabilities, build evaluation suites, and integrate model-backed services.",
        "description": "Sample listing for local testing. Build user-focused AI features with measurable quality and safety checks.",
        "application_url": "https://example.com/careers/ai-engineer",
    },
    {
        "title": "Java Developer",
        "company": "Granite Systems",
        "category": "Software Engineering",
        "industry": "Technology",
        "required_skills": "Java, Spring Boot, REST APIs, SQL",
        "preferred_skills": "Microservices, Kafka, Kubernetes",
        "experience_level": "Mid level",
        "required_experience_years": 2,
        "max_experience_years": 6,
        "education_requirements": "Bachelor's degree in Computer Science or equivalent experience",
        "qualifications": "Experience designing and testing backend services",
        "location": "Pune, India",
        "employment_type": "Full-time",
        "work_mode": "on_site",
        "salary": "INR 11-19 LPA",
        "responsibilities": "Implement backend services, write integration tests, and participate in design reviews.",
        "description": "Sample listing for local testing. Develop dependable Java services for business-critical workflows.",
        "application_url": "https://example.com/careers/java-developer",
    },
    {
        "title": "Web Developer",
        "company": "Lighthouse Web Studio",
        "category": "Web Development",
        "industry": "Technology",
        "required_skills": "HTML, CSS, JavaScript, Responsive Design",
        "preferred_skills": "React, Bootstrap, Accessibility",
        "experience_level": "Entry level",
        "required_experience_years": 0,
        "max_experience_years": 4,
        "education_requirements": "Relevant degree, portfolio, or equivalent practical experience",
        "qualifications": "Portfolio demonstrating responsive, accessible web interfaces",
        "location": "Coimbatore, India",
        "employment_type": "Full-time",
        "work_mode": "hybrid",
        "salary": "INR 6-13 LPA",
        "responsibilities": "Build responsive pages, collaborate with designers, and improve accessibility.",
        "description": "Sample listing for local testing. Create polished and accessible web experiences across devices.",
        "application_url": "https://example.com/careers/web-developer",
    },
    {
        "title": "Business Analyst",
        "company": "Willow Creek Consulting",
        "category": "Business Analysis",
        "industry": "Consulting",
        "required_skills": "Requirements Analysis, SQL, Communication, Documentation",
        "preferred_skills": "Agile, Power BI, Process Mapping",
        "experience_level": "Mid level",
        "required_experience_years": 2,
        "max_experience_years": 6,
        "education_requirements": "Bachelor's degree in Business, Information Systems, or a related field",
        "qualifications": "Experience translating stakeholder needs into clear requirements",
        "location": "Chennai, India",
        "employment_type": "Full-time",
        "work_mode": "hybrid",
        "salary": "INR 9-16 LPA",
        "responsibilities": "Facilitate discovery, document workflows, and validate proposed solutions with users.",
        "description": "Sample listing for local testing. Connect business stakeholders with delivery teams through clear analysis.",
        "application_url": "https://example.com/careers/business-analyst",
    },
    {
        "title": "Software Engineer",
        "company": "Redwood Product Group",
        "category": "Software Engineering",
        "industry": "Technology",
        "required_skills": "Programming, Data Structures, Git, Testing",
        "preferred_skills": "Python, Java, Cloud Platforms, CI/CD",
        "experience_level": "Entry level",
        "required_experience_years": 0,
        "max_experience_years": 4,
        "education_requirements": "Bachelor's degree in Computer Science or equivalent practical experience",
        "qualifications": "Strong fundamentals and a commitment to readable, tested code",
        "location": "Bengaluru, India",
        "employment_type": "Full-time",
        "work_mode": "on_site",
        "salary": "INR 8-16 LPA",
        "responsibilities": "Implement product features, write tests, and collaborate on maintainable system design.",
        "description": "Sample listing for local testing. Work with a product team to deliver reliable software improvements.",
        "application_url": "https://example.com/careers/software-engineer",
    },
)


class Command(BaseCommand):
    help = "Add the sample job listings without duplicating existing seeded records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--recruiter",
            help="Username that will own the sample jobs (defaults to a superuser).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        user_model = get_user_model()
        recruiter_name = options.get("recruiter")
        if recruiter_name:
            recruiter = user_model.objects.filter(username=recruiter_name).first()
            if recruiter is None:
                raise CommandError(f"Recruiter account '{recruiter_name}' does not exist.")
        else:
            recruiter = user_model.objects.filter(is_superuser=True).order_by("pk").first()
            if recruiter is None:
                raise CommandError(
                    "Create an admin account first or pass --recruiter USERNAME."
                )

        created = 0
        existing = 0
        for sample in SAMPLE_JOBS:
            defaults = {
                **sample,
                "status": "open",
                "is_active": True,
            }
            _, was_created = Job.objects.get_or_create(
                recruiter=recruiter,
                title=sample["title"],
                company=sample["company"],
                location=sample["location"],
                defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                existing += 1

        self.stdout.write(self.style.SUCCESS(
            f"Sample jobs complete: {created} created, {existing} already existed."
        ))
