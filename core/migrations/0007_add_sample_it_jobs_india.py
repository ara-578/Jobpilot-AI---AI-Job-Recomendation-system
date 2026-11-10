from django.db import migrations, models
from django.utils import timezone

def add_sample_jobs(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Job = apps.get_model('core', 'Job')
    # Ensure there is at least one recruiter (staff) user
    recruiter = User.objects.filter(is_staff=True).first()
    if not recruiter:
        recruiter = User.objects.create_user(
            username='sample_recruiter',
            password='samplepass123',
            is_staff=True,
            is_superuser=False,
        )
    sample_data = [
        {
            "title": "Python Backend Developer",
            "company": "TechMinds Solutions",
            "location": "Bengaluru, Karnataka, India",
            "employment_type": "Full-time",
            "experience_level": "Mid level",
            "required_experience_years": 2,
            "salary": "12-15 LPA",
            "industry": "Software Development",
            "category": "IT Services",
            "work_mode": "remote",
            "required_skills": "python, django, rest api, postgresql",
            "preferred_skills": "docker, aws",
            "education_requirements": "B.Tech/CSE or equivalent",
            "description": "Build and maintain backend services using Django REST framework. Collaborate with front‑end teams and DevOps.",
            "application_url": "https://techminds.example.com/jobs/py-backend",
        },
        {
            "title": "Frontend Engineer",
            "company": "DigitalWave Labs",
            "location": "Hyderabad, Telangana, India",
            "employment_type": "Full-time",
            "experience_level": "Entry level",
            "required_experience_years": 0,
            "salary": "6-9 LPA",
            "industry": "Digital Services",
            "category": "Web Development",
            "work_mode": "on_site",
            "required_skills": "javascript, html, css, react",
            "preferred_skills": "redux, typescript",
            "education_requirements": "B.Sc./B.Tech in CS or related",
            "description": "Develop responsive UI components, work with design team, and ensure cross‑browser compatibility.",
            "application_url": "https://digitalwave.example.com/careers/frontend",
        },
        {
            "title": "Data Scientist",
            "company": "Insight Analytics",
            "location": "Mumbai, Maharashtra, India",
            "employment_type": "Full-time",
            "experience_level": "Senior level",
            "required_experience_years": 5,
            "salary": "20-25 LPA",
            "industry": "Analytics",
            "category": "Data Science",
            "work_mode": "hybrid",
            "required_skills": "python, pandas, numpy, scikit-learn, sql, statistics",
            "preferred_skills": "tensorflow, pytorch, spark",
            "education_requirements": "M.Tech/M.Sc. in Data Science or related",
            "description": "Lead end‑to‑end data science projects, build predictive models, and communicate insights to stakeholders.",
            "application_url": "https://insightanalytics.example.com/jobs/data-scientist",
        },
        {
            "title": "DevOps Engineer",
            "company": "CloudSphere Technologies",
            "location": "Pune, Maharashtra, India",
            "employment_type": "Full-time",
            "experience_level": "Mid level",
            "required_experience_years": 3,
            "salary": "14-18 LPA",
            "industry": "Cloud Services",
            "category": "DevOps",
            "work_mode": "remote",
            "required_skills": "docker, kubernetes, aws, linux, ci/cd",
            "preferred_skills": "terraform, ansible",
            "education_requirements": "B.Tech in IT or related",
            "description": "Design and maintain CI/CD pipelines, manage Kubernetes clusters, and ensure high availability of services.",
            "application_url": "https://cloudsphere.example.com/careers/devops",
        },
        {
            "title": "Full Stack Engineer",
            "company": "InnovateX",
            "location": "Chennai, Tamil Nadu, India",
            "employment_type": "Full-time",
            "experience_level": "Mid level",
            "required_experience_years": 2,
            "salary": "13-16 LPA",
            "industry": "Product Development",
            "category": "Full Stack",
            "work_mode": "hybrid",
            "required_skills": "python, django, javascript, react, postgresql",
            "preferred_skills": "graphql, docker",
            "education_requirements": "B.E./B.Tech in CS",
            "description": "Develop end‑to‑end features, integrate front‑end with back‑end APIs, and participate in code reviews.",
            "application_url": "https://innovatex.example.com/jobs/fullstack",
        },
    ]
    for data in sample_data:
        Job.objects.create(
            recruiter=recruiter,
            title=data["title"],
            company=data["company"],
            location=data["location"],
            employment_type=data["employment_type"],
            experience_level=data["experience_level"],
            required_experience_years=data["required_experience_years"],
            salary=data["salary"],
            industry=data["industry"],
            category=data["category"],
            work_mode=data["work_mode"],
            required_skills=data["required_skills"],
            preferred_skills=data["preferred_skills"],
            education_requirements=data["education_requirements"],
            description=data["description"],
            application_url=data["application_url"],
            is_active=True,
            status="open",
            created_at=timezone.now(),
        )

def remove_sample_jobs(apps, schema_editor):
    Job = apps.get_model('core', 'Job')
    Job.objects.filter(title__in=[
        "Python Backend Developer",
        "Frontend Engineer",
        "Data Scientist",
        "DevOps Engineer",
        "Full Stack Engineer",
    ]).delete()

class Migration(migrations.Migration):
    dependencies = [
        ('core', '0006_add_job_experience_ceiling'),
    ]
    operations = [
        migrations.RunPython(add_sample_jobs, reverse_code=remove_sample_jobs),
    ]
