import random
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Job
from django.contrib.auth import get_user_model

COMPANIES = [
    "TechNova Solutions",
    "Innovatech Corp",
    "DigitalPulse Ltd",
    "FutureStack Inc",
    "CloudSphere LLC",
    "DataMinds Agency",
    "QuantumLeap Systems",
    "SynergyWorks",
    "Apex Software",
    "Vertex Labs",
]

SKILL_SETS = {
    "Software and Web Development": ["Python", "Django", "Flask", "REST APIs", "Git", "Docker"],
    "AI, Machine Learning, and Data Science": ["Python", "Machine Learning", "Pandas", "NumPy", "Scikit-learn", "TensorFlow"],
    "Database, Cloud, and DevOps": ["SQL", "PostgreSQL", "MySQL", "AWS", "Docker", "Kubernetes"],
    "Cybersecurity, Networking, and IT Support": ["Network Security", "Linux", "Firewall", "Incident Response"],
    "Testing, Mobile, and Specialized Development": ["Selenium", "Appium", "Android", "iOS", "Flutter"],
    "Enterprise, Product, and Emerging IT Roles": ["Salesforce", "SAP", "Robotic Process Automation", "Blockchain"],
}

CATEGORIES = {
    "Software and Web Development": "Software Development",
    "AI, Machine Learning, and Data Science": "Data Science",
    "Database, Cloud, and DevOps": "Cloud & DevOps",
    "Cybersecurity, Networking, and IT Support": "Cybersecurity",
    "Testing, Mobile, and Specialized Development": "Testing & Mobile",
    "Enterprise, Product, and Emerging IT Roles": "Enterprise",
}

EMPLOYMENT_TYPES = ["Full-time", "Part-time", "Contract", "Internship"]
EXPERIENCE_LEVELS = ["Entry level", "Mid level", "Senior level", "Director"]
WORK_MODES = ["on_site", "remote", "hybrid"]

def random_salary():
    low = random.randint(50, 120) * 1000
    high = low + random.randint(5, 30) * 1000
    return f"${low:,} - ${high:,} USD per annum"

def random_future_date():
    return timezone.now() + timedelta(days=random.randint(30, 180))

def generate_job_data(i):
    categories = list(SKILL_SETS.keys())
    cat = categories[i % len(categories)]
    titles = [
        "Python Developer",
        "Django Developer",
        "Flask Developer",
        "Full Stack Developer",
        "Frontend Developer",
        "Backend Developer",
        "React Developer",
        "Angular Developer",
        "Node.js Developer",
        "Java Developer",
        "AI Engineer",
        "Machine Learning Engineer",
        "Data Scientist",
        "Data Analyst",
        "Database Administrator",
        "AWS Cloud Engineer",
        "Cybersecurity Analyst",
        "Network Engineer",
        "QA Engineer",
        "Mobile App Developer",
        "iOS Developer",
        "Android Developer",
        "Salesforce Developer",
        "Blockchain Developer",
        "RPA Developer",
    ]
    title = titles[i % len(titles)]
    company = random.choice(COMPANIES)
    description = f"We are looking for a {title} to join our {cat.lower()} team. Work with modern technologies and deliver high‑quality solutions."
    required_skills = ", ".join(random.sample(SKILL_SETS[cat], k=min(4, len(SKILL_SETS[cat]))))
    preferred_skills = ", ".join(random.sample(SKILL_SETS[cat], k=2))
    return {
        "title": title,
        "company": company,
        "category": CATEGORIES[cat],
        "description": description,
        "required_skills": required_skills,
        "preferred_skills": preferred_skills,
        "employment_type": random.choice(EMPLOYMENT_TYPES),
        "experience_level": random.choice(EXPERIENCE_LEVELS),
        "required_experience_years": random.choice([0, 0.5, 1, 2, 3]),
        "max_experience_years": random.choice([None, 5, 7, 10]),
        "salary": random_salary(),
        "location": random.choice(["Remote", "New York, NY", "San Francisco, CA", "London, UK", "Berlin, DE"]),
        "work_mode": random.choice(WORK_MODES),
        "deadline": random_future_date(),
        "status": "open",
        "is_active": True,
    }

class Command(BaseCommand):
    help = "Seeds the database with 100 distinct sample IT job records (idempotent)."

    def handle(self, *args, **options):
        User = get_user_model()
        recruiter, _ = User.objects.get_or_create(
            username="seed_admin",
            defaults={"email": "seed_admin@example.com", "is_staff": True, "is_superuser": True, "password": User.objects.make_random_password()},
        )
        created = 0
        for i in range(100):
            data = generate_job_data(i)
            if not Job.objects.filter(title=data["title"], company=data["company"], location=data["location"]).exists():
                Job.objects.create(recruiter=recruiter, **data)
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Seeded {created} new job records (total jobs: {Job.objects.count()})."))
