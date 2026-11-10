import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any

from .ai_engine import extract_skills, read_resume
from .models import Resume, ResumeParsingResult

logger = logging.getLogger(__name__)
import re

@dataclass
class Section:
    name: str
    start_line: int
    end_line: int
    content: str

@dataclass
class SkillEvidence:
    name: str
    category: str
    evidence: str  # e.g. "skill-section", "project", "experience"
    strength: float  # 0.0 – 1.0 based on evidence type

@dataclass
class ParsedResume:
    extracted_text: str = ""
    sections: List[Section] = field(default_factory=list)
    normalized_skills: List[SkillEvidence] = field(default_factory=list)
    education: List[Dict[str, Any]] = field(default_factory=list)
    projects: List[Dict[str, Any]] = field(default_factory=list)
    certifications: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # Contact information extracted from the resume
    name: str = ""
    email: str = ""
    phone: str = ""

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _load_file(resume: Resume) -> str:
    """Read the uploaded file and return plain‑text content.
    Supports PDF, DOCX and plain TXT. Any error is logged and re‑raised as
    a ValueError with a user‑friendly message.
    """
    file_path = Path(resume.file.path)
    try:
        return read_resume(file_path)
    except Exception as exc:
        logger.exception("Failed to extract text from resume %s", resume.id)
        raise ValueError(f"Could not read the resume file: {exc}")


def _detect_sections(text: str) -> List[Section]:
    """Very light‑weight heuristic section detector.
    Looks for common headings (case‑insensitive) followed by a line break.
    Returns a list of Section objects ordered by appearance.
    """
    heading_aliases = {
        "technical skills": "skills",
        "skill set": "skills",
        "work experience": "experience",
        "professional experience": "experience",
        "employment": "experience",
        "employment history": "experience",
        "personal projects": "projects",
        "academic projects": "projects",
        "certificates": "certifications",
        "licenses and certifications": "certifications",
    }
    headings = {
        "objective", "summary", "education", "experience", "projects",
        "certifications", "skills", "languages",
    }
    headings.update(heading_aliases)
    lines = text.splitlines()
    sections: List[Section] = []
    current_name = "body"
    start = 0
    for i, line in enumerate(lines):
        heading = line.strip().lower().rstrip(":").strip()
        if heading in headings:
            if i > start:
                sections.append(Section(name=current_name, start_line=start + 1, end_line=i, content="\n".join(lines[start:i])))
            current_name = heading_aliases.get(heading, heading)
            start = i + 1
    sections.append(Section(name=current_name, start_line=start + 1, end_line=len(lines), content="\n".join(lines[start:])))
    return sections
def _extract_contact_info(text: str) -> Dict[str, str]:
    """Extract name, email and phone number from raw resume text.

    Simple heuristics:
    * Email – first email‑like pattern.
    * Phone – a sequence of digits (allowing spaces, dashes, parentheses).
    * Name – first line with 1‑3 capitalised words that is not a section heading.
    """
    info: Dict[str, str] = {}
    # Email
    email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if email_match:
        info["email"] = email_match.group(0)
    # Phone
    phone_match = re.search(r"(\+?\d{1,3}[\s-]?)?(\(?\d{2,4}\)?[\s-]?)?\d{3,4}[\s-]?\d{3,4}", text)
    if phone_match:
        info["phone"] = re.sub(r"\s+", " ", phone_match.group(0)).strip()
    # Name – look at the first few lines
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines[:10]:
        if re.match(r"^[A-Za-z]+(?:\s+[A-Za-z\.]+){0,2}$", line):
            if line.lower() not in {"objective", "summary", "profile"}:
                info["name"] = line
                break
    return info


def _normalize_skill(raw: str) -> SkillEvidence:
    """Map a raw skill token to the canonical taxonomy entry.
    If no match is found the original string is kept and the category is set
    to "unknown". Strength is 0.5 for a plain mention.
    """
    matches = extract_skills(raw)
    if matches:
        return SkillEvidence(
            name=matches[0],
            category="unknown",
            evidence="skill-section",
            strength=0.6,
        )
    return SkillEvidence(
        name=raw.strip(),
        category="unknown",
        evidence="raw-mention",
        strength=0.5,
    )


def _extract_skills(sections: List[Section]) -> List[SkillEvidence]:
    """Collect skill tokens from a "skills" section (comma‑ or line‑separated).
    Returns a list of SkillEvidence objects with an initial strength based on
    where the skill was found. Duplicate mentions are de‑duplicated while keeping
    the highest strength.
    """
    skill_map: Dict[str, SkillEvidence] = {}
    for sec in sections:
        if sec.name == "skills":
            for skill in extract_skills(sec.content):
                ev = _normalize_skill(skill)
                if ev.name not in skill_map or ev.strength > skill_map[ev.name].strength:
                    skill_map[ev.name] = ev
    return list(skill_map.values())

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_resume(resume: Resume) -> ParsedResume:
    """Parse a ``Resume`` instance and return a populated ``ParsedResume``.
    The function never raises for missing optional sections – it records a
    warning instead. Errors that prevent any text extraction raise ``ValueError``
    with a clear message for the UI layer.
    """
    result = ParsedResume()
    try:
        raw_text = _load_file(resume)
        result.extracted_text = raw_text
        result.sections = _detect_sections(raw_text)
        # Extract contact information (name, email, phone)
        contact = _extract_contact_info(raw_text)
        result.name = contact.get("name", "")
        result.email = contact.get("email", "")
        result.phone = contact.get("phone", "")
    except ValueError as exc:
        result.warnings.append(str(exc))
        raise

    result.normalized_skills = _extract_skills(result.sections)

    for sec in result.sections:
        if sec.name == "education":
            lines = [ln.strip() for ln in sec.content.splitlines() if ln.strip()]
            for ln in lines:
                result.education.append({"raw": ln})
        if sec.name == "projects":
            lines = [ln.strip() for ln in sec.content.splitlines() if ln.strip()]
            for ln in lines:
                result.projects.append({"raw": ln})
        if sec.name == "certifications":
            lines = [ln.strip() for ln in sec.content.splitlines() if ln.strip()]
            for ln in lines:
                result.certifications.append({"raw": ln})

    if not result.normalized_skills:
        result.warnings.append("No skills detected – ensure a Skills section exists.")

    return result


def persist_parsing_result(resume: Resume, parsed: ParsedResume) -> ResumeParsingResult:
    """Create or update the ``ResumeParsingResult`` linked to *resume*.
    Any previous result is overwritten – the raw ``Resume`` file never changes.
    """
    obj, _ = ResumeParsingResult.objects.update_or_create(
        resume=resume,
        defaults={
            "extracted_text": parsed.extracted_text,
            "sections": {sec.name: {"start": sec.start_line, "end": sec.end_line} for sec in parsed.sections},
            "normalized_skills": [
                {"name": ev.name, "category": ev.category, "evidence": ev.evidence, "strength": ev.strength}
                for ev in parsed.normalized_skills
            ],
            "education": parsed.education,
            "projects": parsed.projects,
            "certifications": parsed.certifications,
            "parsing_warnings": parsed.warnings,
            "candidate_info": {
                "name": parsed.name,
                "email": parsed.email,
                "phone": parsed.phone,
            },
        },
    )
    return obj
