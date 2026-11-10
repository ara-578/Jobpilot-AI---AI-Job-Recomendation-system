import re
from functools import lru_cache

from django.core.cache import cache

COMMON_SKILLS = {
    "python", "django", "flask", "java", "javascript", "typescript", "react",
    "node.js", "sql", "postgresql", "mysql", "mongodb", "html", "css", "aws",
    "azure", "docker", "kubernetes", "machine learning", "deep learning",
    "data analysis", "data analytics", "power bi", "tableau", "excel", "pandas",
    "numpy", "scikit-learn", "nlp", "artificial intelligence", "tensorflow",
    "pytorch", "git", "rest api", "c++", "c#", "linux",
}
COMMON_JOB_TITLES = {
    "python developer", "django developer", "data analyst", "data scientist",
    "machine learning engineer", "ai engineer", "software engineer",
    "software developer", "web developer", "frontend developer",
    "backend developer", "full stack developer", "java developer",
    "react developer", "business analyst", "devops engineer", "product manager",
}
COMMON_LOCATIONS = {
    "trivandrum", "thiruvananthapuram", "kochi", "ernakulam", "kollam",
    "kozhikode", "calicut", "thrissur", "bengaluru", "bangalore", "chennai",
    "hyderabad", "mumbai", "pune", "delhi", "new delhi", "noida", "gurugram",
    "gurgaon", "kolkata", "remote",
}


def _job_dictionaries():
    from core.models import Job

    cached = cache.get("chatbot:job-entity-dictionaries")
    if cached is not None:
        return cached
    records = list(
        Job.objects.filter(is_active=True, status="open")
        .values_list(
            "title", "company", "location", "required_skills", "preferred_skills"
        )[:500]
    )
    titles = sorted(
        COMMON_JOB_TITLES | {row[0].strip().casefold() for row in records if row[0]},
        key=len,
        reverse=True,
    )
    companies = sorted(
        {row[1].strip() for row in records if row[1]}, key=len, reverse=True
    )
    locations = sorted(
        COMMON_LOCATIONS | {row[2].strip().casefold() for row in records if row[2]},
        key=len,
        reverse=True,
    )
    skills = set(COMMON_SKILLS)
    for row in records:
        for value in (row[3], row[4]):
            skills.update(
                item.strip().casefold()
                for item in re.split(r"[,;\n|]", value or "")
                if len(item.strip()) > 1
            )
    result = {
        "titles": titles,
        "companies": companies,
        "locations": locations,
        "skills": sorted(skills, key=len, reverse=True),
    }
    cache.set("chatbot:job-entity-dictionaries", result, 300)
    return result


def _find_terms(text, terms):
    found = []
    for term in terms:
        if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I):
            found.append(term)
    return found


def extract_entities(text):
    dictionaries = _job_dictionaries()
    titles = _find_terms(text, dictionaries["titles"])
    locations = _find_terms(text, dictionaries["locations"])
    companies = _find_terms(text, dictionaries["companies"])
    skills = _find_terms(text, dictionaries["skills"])
    experience = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(years?|yrs?)\s*(?:of\s*)?(?:experience|exp)?\b",
        text,
        re.I,
    )
    salary = re.search(
        r"\b(?:under|above|over|between|at least|minimum|maximum)?\s*"
        r"(?:₹|rs\.?\s*)?([\d,]+(?:\.\d+)?)\s*(lpa|lakhs?|k)?"
        r"(?:\s*(?:to|-)\s*(?:₹|rs\.?\s*)?([\d,]+(?:\.\d+)?)\s*(lpa|lakhs?|k)?)?\b",
        text,
        re.I,
    )
    mode = next(
        (value for value in ("remote", "hybrid", "on-site", "onsite") if value in text.casefold()),
        "",
    )
    employment = next(
        (value for value in ("internship", "part-time", "full-time", "contract") if value in text.casefold()),
        "",
    )
    return {
        "job_title": titles[0] if titles else "",
        "company": companies[0] if companies else "",
        "location": locations[0] if locations else "",
        "skills": skills[:12],
        "experience_years": experience.group(1) if experience else "",
        "salary": salary.group(0).strip() if salary and re.search(r"\d", salary.group(0)) else "",
        "work_mode": mode,
        "employment_type": employment,
    }
