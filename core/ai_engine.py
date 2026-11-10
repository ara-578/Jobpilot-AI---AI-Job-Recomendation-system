import os, re, shutil, hashlib
from itertools import chain
from functools import lru_cache
import numpy as np
import logging
import math
import pickle
from threading import Lock
from pathlib import Path
from datetime import date, datetime, time
import joblib
# duplicate import removed
import pymupdf
from docx import Document
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

MODEL_PATH = Path(
    os.environ.get(
        "JOBPILOT_MODEL_PATH",
        Path(settings.BASE_DIR) / "saved_models" / "ai_job_recommendation_system.pkl",
    )
)
logger = logging.getLogger(__name__)
_bundle = None
_bundle_error = ""
_bundle_load_signature = None
_bundle_load_lock = Lock()

# Optional sentence-transformer model, loaded only from a local model directory.
_embed_model = None
_embed_model_attempted = False
_embed_model_source = ""


def _available_model_path():
    if MODEL_PATH.is_file():
        return MODEL_PATH
    candidates = sorted(MODEL_PATH.parent.glob("*.pkl"))
    return candidates[0] if len(candidates) == 1 else None
SKILLS = {
"python":["python","python programming"],"java":["java","java programming"],"c++":["c++","cpp"],"sql":["sql"],"mysql":["mysql"],
"postgresql":["postgresql","postgres"],"sqlite":["sqlite"],"django":["django","django development"],"flask":["flask"],
"fastapi":["fastapi"],"html":["html","html5"],"css":["css","css3"],"javascript":["javascript","js"],
"react":["react","reactjs"],"node.js":["node.js","nodejs"],"bootstrap":["bootstrap"],"rest api":["rest api","restful api","rest apis"],
"machine learning":["machine learning","ml"],"deep learning":["deep learning"],"artificial intelligence":["artificial intelligence","ai"],
"data science":["data science"],"data analysis":["data analysis"],"natural language processing":["natural language processing","nlp"],
"computer vision":["computer vision"],"generative ai":["generative ai"],"llm":["llm","large language model"],
"tensorflow":["tensorflow"],"pytorch":["pytorch"],"scikit-learn":["scikit-learn","sklearn"],"pandas":["pandas"],
"numpy":["numpy"],"matplotlib":["matplotlib"],"power bi":["power bi"],"tableau":["tableau"],"excel":["excel"],
"aws":["aws","amazon web services"],"azure":["azure"],"git":["git"],"github":["github"],"docker":["docker"],
"kubernetes":["kubernetes"],"linux":["linux"],"statistics":["statistics"],"opencv":["opencv"],
"communication":["communication"],"project management":["project management"],"figma":["figma"],"php":["php"],
"spring boot":["spring boot"],"mongodb":["mongodb","mongo db"],"redis":["redis"],"spark":["apache spark","pyspark"]
}
EDUCATION_PATTERNS = {
"phd":r"\bph\.?d\.?\b|\bdoctorate\b","mtech":r"\bm\.?tech\b|master of technology",
"msc":r"\bm\.?sc\b|master of science","mba":r"\bmba\b|master of business administration",
"mca":r"\bmca\b|master of computer applications","btech":r"\bb\.?tech\b|bachelor of technology",
"be":r"\bb\.?e\.?\b|bachelor of engineering","bsc":r"\bb\.?sc\b|bachelor of science",
"bca":r"\bbca\b|bachelor of computer applications","computer science":r"\bcomputer science\b",
"information technology":r"\binformation technology\b","data science":r"\bdata science\b",
"artificial intelligence":r"\bartificial intelligence\b"
}
def normalize(text):
    return re.sub(r"\s+"," ",str(text).lower().replace("&"," and ")).strip()
from .taxonomy import SkillTaxonomy

# Cache for skill alias lookup to avoid DB hits on every call
_skill_lookup_cache = None
_skill_alias_pattern = None

def _build_skill_lookup():
    """Build a mapping of alias -> canonical skill name from the DB."""
    global _skill_lookup_cache
    if _skill_lookup_cache is None:
        _skill_lookup_cache = {
            normalize(alias): canonical
            for canonical, aliases in SKILLS.items()
            for alias in aliases
        }
        for entry in SkillTaxonomy.objects.all():
            canonical = normalize(entry.canonical)
            _skill_lookup_cache[canonical] = canonical  # direct match
            for alias in entry.aliases or []:
                normalized_alias = normalize(alias)
                if normalized_alias:
                    _skill_lookup_cache[normalized_alias] = canonical
    return _skill_lookup_cache

def _build_skill_alias_pattern(lookup):
    global _skill_alias_pattern
    if _skill_alias_pattern is None:
        aliases = sorted(lookup, key=lambda alias: (-len(alias), alias))
        if not aliases:
            return None
        alternatives = "|".join(re.escape(alias) for alias in aliases)
        _skill_alias_pattern = re.compile(
            rf"(?<![a-z0-9])({alternatives})(?![a-z0-9])"
        )
    return _skill_alias_pattern

@lru_cache(maxsize=65536)
def _extract_normalized_skills(t):
    lookup = _build_skill_lookup()
    pattern = _build_skill_alias_pattern(lookup)
    if pattern is None:
        return []
    return sorted({lookup[match.group(1)] for match in pattern.finditer(t)})


def extract_skills(text):
    """Return normalized skill names found in text using taxonomy aliases."""
    return list(_extract_normalized_skills(normalize(text)))

def extract_education(text):
    t=normalize(text)
    return sorted(k for k,p in EDUCATION_PATTERNS.items() if re.search(p,t))
def extract_resume_sections(text, section_names):
    items = []
    target_headings = {name.casefold() for name in section_names}
    other_headings = {
        "summary", "professional summary", "profile", "objective", "skills",
        "technical skills", "technical skills and tools", "programming languages",
        "frameworks", "tools",
        "education", "experience", "work experience", "employment",
        "internship", "internships",
        "professional experience",
        "certifications", "certificates", "licenses",
        "projects", "personal projects", "academic projects",
        "awards", "publications", "languages", "references",
    }
    collecting = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = re.sub(r"[:\-]+$", "", line).strip().casefold()
        if heading in target_headings:
            collecting = True
            continue
        if heading in other_headings:
            collecting = False
            continue
        if collecting and line:
            item = line.strip(" \t-*•")
            if item:
                items.append(item)
    return list(dict.fromkeys(items))
def extract_projects(text):
    return extract_resume_sections(
        text, ("projects", "personal projects", "academic projects")
    )
def extract_certifications(text):
    return extract_resume_sections(
        text, ("certifications", "certificates", "licenses", "licenses and certifications")
    )


_RESUME_EVIDENCE_SECTIONS = (
    "summary",
    "professional summary",
    "profile",
    "objective",
    "skills",
    "technical skills",
    "technical skills and tools",
    "programming languages",
    "frameworks",
    "tools",
    "work experience",
    "professional experience",
    "experience",
    "employment",
    "projects",
    "personal projects",
    "academic projects",
    "internship",
    "internships",
)


def _resume_evidence_text(text):
    raw_text = str(text or "")
    sections = extract_resume_sections(raw_text, _RESUME_EVIDENCE_SECTIONS)
    if sections:
        heading_pattern = (
            r"(?:summary|professional summary|profile|objective|skills|"
            r"technical skills|technical skills and tools|programming languages|"
            r"frameworks|tools|education|experience|work experience|"
            r"professional experience|employment|projects|personal projects|"
            r"academic projects|internship|internships|certifications?|references)"
        )
        preamble = []
        for line in raw_text.splitlines():
            if re.fullmatch(
                heading_pattern,
                re.sub(r"[:\-]+$", "", line.strip()).casefold(),
            ):
                break
            if "@" not in line and not re.search(r"\b\d{7,}\b", line):
                preamble.append(line)
        return "\n".join([*preamble, *sections])
    has_section_heading = any(
        re.fullmatch(
            r"(?:summary|professional summary|profile|objective|skills|"
            r"technical skills|technical skills and tools|programming languages|"
            r"frameworks|tools|education|experience|work experience|professional experience|"
            r"employment|projects|personal projects|academic projects|"
            r"internship|internships|certifications?|references)",
            re.sub(r"[:\-]+$", "", line.strip()).casefold(),
        )
        for line in raw_text.splitlines()
    )
    return "" if has_section_heading else raw_text


def extract_resume_skills(text):
    """Extract skills from relevant resume sections, not unrelated sections."""
    return extract_skills(_resume_evidence_text(text))


def _years_from_experience_text(text):
    t = normalize(text)
    values = []
    patterns = [
        r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\s+"
        r"(?:of\s+)?(?:professional\s+)?(?:experience|exp)",
        r"(?:experience|exp)[^0-9]{0,30}(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)",
    ]
    for pattern in patterns:
        values.extend(
            float(match.group(1)) for match in re.finditer(pattern, t)
        )
    for start, end in re.findall(
        r"\b((?:19|20)\d{2})\s*(?:-|–|—|to)\s*"
        r"((?:19|20)\d{2}|present|current|now)\b",
        t,
        flags=re.IGNORECASE,
    ):
        end_year = (
            date.today().year
            if end.casefold() in {"present", "current", "now"}
            else int(end)
        )
        if end_year >= int(start):
            values.append(float(end_year - int(start)))
    return max(values) if values else 0.0


def extract_internship_experience(text):
    internship_sections = extract_resume_sections(
        text, ("internship", "internships")
    )
    return _years_from_experience_text("\n".join(internship_sections))


def extract_experience(text):
    raw_text = str(text or "")
    work_sections = extract_resume_sections(
        raw_text,
        ("experience", "work experience", "professional experience", "employment"),
    )
    if work_sections:
        t = normalize("\n".join(work_sections))
    else:
        heading_pattern = (
            r"(?:summary|professional summary|profile|objective|education|"
            r"academic projects?|personal projects?|projects?|work experience|"
            r"professional experience|employment|internships?|certifications?|"
            r"skills|technical skills)"
        )
        sectioned_resume = any(
            re.fullmatch(
                heading_pattern,
                re.sub(r"[:\-]+$", "", line.strip()).casefold(),
            )
            for line in raw_text.splitlines()
        )
        if sectioned_resume:
            preamble = []
            for line in raw_text.splitlines():
                if re.fullmatch(
                    heading_pattern,
                    re.sub(r"[:\-]+$", "", line.strip()).casefold(),
                ):
                    break
                preamble.append(line)
            t = normalize("\n".join(preamble))
            if not re.search(
                r"\d+(?:\.\d+)?\s*\+?\s*(?:years?|yrs?)\s+"
                r"(?:of\s+)?(?:professional\s+)?(?:experience|exp)",
                t,
            ):
                return 0.0
        else:
            t = normalize(raw_text)
    return _years_from_experience_text(t)

def _configure_tesseract():
    if shutil.which("tesseract"):
        return
    install_roots = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    )
    for root in install_roots:
        install_dir = root / "Tesseract-OCR"
        if (
            (install_dir / "tesseract.exe").is_file()
            and (install_dir / "tessdata" / "eng.traineddata").is_file()
        ):
            os.environ["PATH"] = os.pathsep.join(
                part for part in (str(install_dir), os.environ.get("PATH", "")) if part
            )
            return

def _ocr_memory_error(exc):
    if isinstance(exc, MemoryError):
        return True
    return (
        isinstance(exc, pymupdf.mupdf.FzErrorBase)
        and "memoryerror" in str(exc).casefold()
    )

def _ocr_page(page):
    _configure_tesseract()
    for attempt, dpi in enumerate((150, 96)):
        try:
            return page.get_textpage_ocr(language="eng", dpi=dpi, full=True)
        except RuntimeError as exc:
            if "tesseract" in str(exc).lower():
                raise ValueError(
                    "This PDF needs OCR, but Tesseract is not installed or "
                    "is not available on PATH. Install Tesseract OCR with "
                    "English language data, then restart the application."
                ) from exc
            raise
        except (MemoryError, pymupdf.mupdf.FzErrorBase) as exc:
            if not _ocr_memory_error(exc):
                if isinstance(exc, pymupdf.mupdf.FzErrorBase):
                    raise ValueError(
                        "OCR could not process a scanned page in this PDF. "
                        "Check that the file is readable and Tesseract is installed."
                    ) from exc
                raise
            if attempt == 1:
                raise ValueError(
                    "This scanned PDF page is too large to process safely. "
                    "Please upload a smaller or lower-resolution PDF."
                ) from exc

def read_resume(path):
    """Read raw text from a resume file.

    Supports ``.pdf`` (with optional OCR), ``.docx`` and ``.txt``.  Returns the
    extracted text or raises ``ValueError`` with a user‑friendly message.
    """
    ext = Path(path).suffix.lower()
    max_size = 5 * 1024 * 1024  # 5 MiB limit
    if Path(path).stat().st_size > max_size:
        raise ValueError("Resume file is too large (max 5 MiB). Please upload a smaller file.")

    if ext == ".pdf":
        pages = []
        with pymupdf.open(path) as doc:
            for page in doc:
                # First try native text extraction
                page_text = page.get_text()
                # If the page contains very little text, fall back to OCR
                if len(page_text.strip()) < 30:
                    textpage = _ocr_page(page)
                    page_text = page.get_text(textpage=textpage)
                pages.append(page_text)
        text = "\n".join(pages)
    elif ext == ".docx":
        doc = Document(path)
        content = [paragraph.text for paragraph in doc.paragraphs]
        # Include table contents as plain text
        for table in doc.tables:
            for row in table.rows:
                content.append("\t".join(cell.text for cell in row.cells))
        text = "\n".join(content)
    elif ext == ".txt":
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    else:
        raise ValueError("Upload a PDF, DOCX or TXT resume.")
    if len(text.strip()) < 30:
        raise ValueError(
            "We could not read enough text from this resume. For scanned PDFs, "
            "ensure the scan is clear and Tesseract OCR with English language data "
            "is installed."
        )
    return text
def get_bundle():
    global _bundle, _bundle_error, _bundle_load_signature
    if _bundle is not None:
        return _bundle

    model_path = _available_model_path()
    if model_path is None:
        return None
    try:
        stat = model_path.stat()
    except OSError:
        return None
    signature = (str(model_path), stat.st_mtime_ns, stat.st_size)
    if signature == _bundle_load_signature:
        return None

    with _bundle_load_lock:
        if _bundle is not None:
            return _bundle
        try:
            stat = model_path.stat()
        except OSError:
            return None
        signature = (str(model_path), stat.st_mtime_ns, stat.st_size)
        if signature == _bundle_load_signature:
            return None
        _bundle_load_signature = signature
        _bundle_error = ""
        try:
            _bundle = joblib.load(model_path)
        except (
            AttributeError, EOFError, ImportError, OSError, pickle.UnpicklingError,
            RuntimeError, TypeError, ValueError,
        ) as exc:
            _bundle_error = str(exc)
            logger.exception("Could not load the configured recommendation model")
            return None
    return _bundle

def model_status(load=True):
    if not load and _bundle is None:
        if _bundle_error:
            return {
                "ready": False,
                "message": f"Trained model could not be loaded: {_bundle_error}",
            }
        model_path = _available_model_path()
        if model_path is None:
            if list(MODEL_PATH.parent.glob("*.pkl")):
                message = (
                    "Set JOBPILOT_MODEL_PATH to select the trained model "
                    f"(expected: {MODEL_PATH})"
                )
            else:
                message = f"Place your trained PKL at {MODEL_PATH}"
            return {"ready": False, "message": message}
        return {
            "ready": True,
            "message": "Trained model bundle is available and loads when AI features are used.",
        }

    b=get_bundle()
    if b is None:
        if _bundle_error:
            return {"ready":False,"message":f"Trained model could not be loaded: {_bundle_error}"}
        if list(MODEL_PATH.parent.glob("*.pkl")):
            message=f"Set JOBPILOT_MODEL_PATH to select the trained model (expected: {MODEL_PATH})"
        else:
            message=f"Place your trained PKL at {MODEL_PATH}"
        return {"ready":False,"message":message}
    needed={"tfidf_vectorizer","job_matrix","jobs"}
    missing=needed-set(b)
    if missing: return {"ready":False,"message":"PKL bundle missing keys: "+", ".join(sorted(missing))}
    return {"ready":True,"message":"Trained model bundle loaded"}
def predict_category(text):
    b=get_bundle()
    if not b or "category_model" not in b: return "",0.0
    model=b["category_model"]
    try:
        pred=model.predict([text])[0]
        confidence=float(max(model.predict_proba([text])[0])) if hasattr(model,"predict_proba") else 0.0
        return str(pred),confidence
    except (AttributeError, TypeError, ValueError) as exc:
        logger.warning("Category prediction failed: %s", exc, exc_info=True)
        return "",0.0
def ats_estimate(
    text,
    skills,
    *,
    education=None,
    experience_years=None,
    internship_experience_years=None,
    projects=None,
    certifications=None,
):
    return sum(item["score"] for item in ats_breakdown(
        text,
        skills,
        education=education,
        experience_years=experience_years,
        internship_experience_years=internship_experience_years,
        projects=projects,
        certifications=certifications,
    ))


def ats_breakdown(
    text,
    skills,
    *,
    education=None,
    experience_years=None,
    internship_experience_years=None,
    projects=None,
    certifications=None,
):
    text = text or ""
    word_count = len(text.split())
    education = extract_education(text) if education is None else education
    has_experience = (
        extract_experience(text) > 0
        if experience_years is None and internship_experience_years is None
        else (experience_years or 0) > 0
        or (internship_experience_years or 0) > 0
    )
    projects = extract_projects(text) if projects is None else projects
    certifications = (
        extract_certifications(text) if certifications is None else certifications
    )
    achievements = extract_resume_sections(
        text, ("achievements", "awards", "accomplishments")
    )
    section_headings = {
        "summary", "professional summary", "profile", "objective",
        "skills", "technical skills", "education", "experience",
        "work experience", "employment", "internship", "internships",
        "projects", "personal projects", "academic projects",
        "certifications", "certificates", "licenses", "achievements",
        "awards", "publications", "languages",
    }
    detected_sections = sum(
        re.sub(r"[:\-]+$", "", line).strip().casefold() in section_headings
        for line in text.splitlines()
    )
    has_email = bool(re.search(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, re.I
    ))
    has_phone = bool(re.search(r"(?<!\d)\+?\d[\d\s().-]{8,}\d(?!\d)", text))
    project_count = len(projects)
    certification_count = len(certifications) + len(achievements)
    components = [
        {
            "label": "Readable content and section structure",
            "score": (
                (16 if word_count >= 300 else 12 if word_count >= 150 else 6 if word_count >= 75 else 0)
                + min(4, max(0, detected_sections - 1))
            ),
            "maximum": 20,
        },
        {
            "label": "Contact details",
            "score": (4 if has_email else 0) + (4 if has_phone else 0),
            "maximum": 8,
        },
        {
            "label": "Relevant skills identified",
            "score": min(
                16,
                len({
                    str(skill).strip().casefold()
                    for skill in skills or []
                    if str(skill).strip()
                }) * 2,
            ),
            "maximum": 16,
        },
        {
            "label": "Education details",
            "score": 8 if education else 0,
            "maximum": 8,
        },
        {
            "label": "Experience details",
            "score": 12 if has_experience else 0,
            "maximum": 12,
        },
        {
            "label": "Project evidence",
            "score": min(8, project_count * 4),
            "maximum": 8,
        },
        {
            "label": "Certifications or achievements",
            "score": min(8, certification_count * 4),
            "maximum": 8,
        },
    ]
    for component in components:
        component["percentage"] = round(
            component["score"] * 100 / component["maximum"]
        )
    return components
def tips(text, skills):
    out = []
    if len(text.split()) < 150:
        out.append("Add a concise professional summary and more detail about projects and responsibilities.")
    if not extract_education(text):
        out.append("Add your degree, institution and graduation year.")
    if not extract_experience(text):
        out.append("Clarify internship or work experience with dates and responsibilities; use 0 only if you have none.")
    if len(skills)<5: out.append("Include relevant technical skills that you can genuinely demonstrate.")
    if not re.search(r"\b(project|projects)\b",text,re.I): out.append("Add a Projects section with tools used and measurable outcomes.")
    if not re.search(r"\b(achievements|achievement|certifications|certification)\b",text,re.I): out.append("Add relevant certifications or achievements if you have them.")
    out.append("Tailor keywords to each job description and keep formatting simple and readable.")
    return out

_dataset_records_cache = None
_dataset_jobs_by_key = None
_dataset_similarity_matrix = None
_dataset_similarity_matrix_key = None
_database_similarity_matrix = None
_database_similarity_matrix_key = None
_similarity_matrix_lock = Lock()

def _as_text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return ", ".join(str(item) for item in value if item is not None)
    try:
        if np.isnan(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()

def _normalize_dataset_columns(record):
    return {
        re.sub(r"[^a-z0-9]+", "", str(key).casefold()): value
        for key, value in record.items()
    }

def _dataset_value(normalized_record, *aliases):
    for alias in aliases:
        value = normalized_record.get(
            re.sub(r"[^a-z0-9]+", "", alias.casefold())
        )
        text = _as_text(value)
        if text:
            return value
    return ""

def _dataset_records():
    global _dataset_records_cache, _dataset_jobs_by_key
    if _dataset_records_cache is not None:
        return _dataset_records_cache
    bundle = get_bundle()
    frame = bundle.get("jobs") if bundle else None
    if frame is None or not hasattr(frame, "to_dict"):
        _dataset_records_cache = []
        _dataset_jobs_by_key = {}
        return _dataset_records_cache
    aliases = {
        "job_title": ("job_title", "title", "position", "role"),
        "description": (
            "job_text", "description", "job_description", "processed_text",
            "summary",
        ),
        "required_skills": (
            "required_skills", "skills", "skill_set", "technical_skills",
        ),
        "company": ("company", "employer", "organization"),
        "preferred_skills": ("preferred_skills", "desired_skills"),
        "category": ("category", "job_category", "industry", "sector"),
        "industry": ("industry", "sector"),
        "experience_level": ("experience_level", "experience", "seniority"),
        "required_experience_years": (
            "required_experience_years", "minimum_experience_years",
            "min_experience_years", "years_experience",
        ),
        "max_experience_years": (
            "max_experience_years", "maximum_experience_years", "experience_max",
        ),
        "education_requirements": ("education_requirements", "education", "degree"),
        "qualifications": ("qualifications", "qualification", "requirements"),
        "location": ("location", "job_location", "city", "region"),
        "employment_type": ("employment_type", "job_type", "type"),
        "salary": ("salary", "compensation", "pay"),
        "responsibilities": ("responsibilities", "duties"),
        "application_url": (
            "application_url", "apply_url", "application_link", "url",
        ),
        "work_mode": ("work_mode", "remote_type"),
        "deadline": ("deadline", "expires_at", "closing_date"),
        "status": ("status", "job_status", "listing_status"),
        "is_active": ("is_active", "active", "is_open"),
        "created_at": ("created_at", "posted_at", "posting_date", "date_posted"),
    }
    columns = {
        re.sub(r"[^a-z0-9]+", "", str(column).casefold()): column
        for column in frame.columns
    }
    field_columns = {
        field: next(
            (
                columns[normalized_alias]
                for alias in field_aliases
                if (
                    normalized_alias := re.sub(
                        r"[^a-z0-9]+", "", alias.casefold()
                    )
                ) in columns
            ),
            None,
        )
        for field, field_aliases in aliases.items()
    }

    def raw_value(row, field):
        column = field_columns[field]
        return row.get(column) if column is not None else None

    def text_value(row, field):
        return _as_text(raw_value(row, field))

    records = []
    for position, (index, value) in enumerate(
        zip(frame.index, frame.to_dict(orient="records"))
    ):
        title = text_value(value, "job_title")
        if not title:
            continue
        record = {
            "job": None,
            "source": "dataset",
            "source_label": "Dataset",
            "source_key": str(index),
            "dataset_index": position,
            "job_title": title,
            "company": text_value(value, "company") or "Company not specified",
            "description": text_value(value, "description"),
            "required_skills": text_value(value, "required_skills"),
            "preferred_skills": text_value(value, "preferred_skills"),
            "category": text_value(value, "category"),
            "industry": text_value(value, "industry"),
            "experience_level": text_value(value, "experience_level"),
            "required_experience_years": text_value(
                value, "required_experience_years"
            ),
            "max_experience_years": text_value(value, "max_experience_years"),
            "education_requirements": text_value(value, "education_requirements"),
            "qualifications": text_value(value, "qualifications"),
            "location": text_value(value, "location"),
            "employment_type": text_value(value, "employment_type"),
            "salary": text_value(value, "salary"),
            "responsibilities": text_value(value, "responsibilities"),
            "application_url": text_value(value, "application_url"),
            "work_mode": text_value(value, "work_mode"),
            "deadline": text_value(value, "deadline"),
            "status": text_value(value, "status"),
            "is_active": raw_value(value, "is_active"),
            "created_at": raw_value(value, "created_at") or None,
            "skills": [],
        }
        records.append(record)
    _dataset_records_cache = records
    _dataset_jobs_by_key = {record["source_key"]: record for record in records}
    return records

def get_dataset_job(source_key):
    _dataset_records()
    return (_dataset_jobs_by_key or {}).get(str(source_key))

def _record_skill_names(record, bundle=None):
    skills = set(record.get("skills") or [])
    if skills:
        return skills
    if record.get("source") == "dataset":
        try:
            position = int(record.get("dataset_index"))
        except (TypeError, ValueError):
            position = -1
        bundle = bundle or (get_bundle() if position >= 0 else None)
        matrix = (bundle or {}).get("job_skill_matrix")
        if matrix is not None and 0 <= position < matrix.shape[0]:
            labels = (bundle or {}).get("skill_labels", [])
            return {
                labels[column]
                for column in matrix.getrow(position).indices
                if column < len(labels)
            }
        return set(extract_skills(
            f"{record.get('required_skills', '')} {record.get('description', '')}"
        ))
    return set(extract_skills(
        f"{record.get('required_skills', '')} {record.get('preferred_skills', '')}"
    ))


def _database_records(jobs, include_skills=True):
    return [
        {
            "job": job,
            "source": "database",
            "source_label": "Database",
            "source_key": str(job.pk),
            "job_title": job.title,
            "company": job.company,
            "description": job.description,
            "required_skills": job.required_skills,
            "preferred_skills": job.preferred_skills,
            "category": job.category,
            "industry": job.industry,
            "experience_level": job.experience_level,
            "required_experience_years": job.required_experience_years,
            "max_experience_years": job.max_experience_years,
            "education_requirements": job.education_requirements,
            "qualifications": job.qualifications,
            "location": job.location,
            "employment_type": job.employment_type,
            "salary": job.salary,
            "responsibilities": job.responsibilities,
            "application_url": job.application_url,
            "work_mode": job.work_mode,
            "deadline": job.deadline or job.expires_at,
            "status": job.status,
            "is_active": job.is_active,
            "expires_at": job.expires_at,
            "created_at": job.created_at,
            "skills": (
                sorted(set(extract_skills(
                    f"{job.required_skills} {job.preferred_skills}"
                )))
                if include_skills else []
            ),
        }
        for job in jobs
    ]

def _job_fingerprint(record):
    parts = (record.get("job_title", ""), record.get("company", ""), record.get("location", ""))
    normalized = " ".join(
        re.sub(r"[^a-z0-9]+", " ", normalize(part)) for part in parts
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _annual_salary_bounds(value):
    salary = str(value or "").strip().casefold()
    if not salary or any(code in salary for code in ("usd", "eur", "gbp", "$", "€", "£")):
        return None
    amounts = [
        float(number.replace(",", ""))
        for number in re.findall(r"\d[\d,]*(?:\.\d+)?", salary)
    ]
    if not amounts:
        return None
    multiplier = 1.0
    if any(unit in salary for unit in ("lpa", "lakh", "lac")):
        multiplier = 100000.0
    elif re.search(r"\b(?:crore|cr)\b", salary):
        multiplier = 10000000.0
    elif re.search(r"\b(?:k|thousand)\b", salary):
        multiplier = 1000.0
    if any(unit in salary for unit in ("per month", "/month", "monthly")):
        multiplier *= 12
    annual_amounts = [amount * multiplier for amount in amounts]
    return min(annual_amounts), max(annual_amounts)


def _record_is_available(record, now):
    if str(record.get("status", "")).strip().casefold() in {
        "closed", "expired", "filled", "inactive", "cancelled",
    }:
        return False
    active_value = record.get("is_active")
    if active_value is not None and str(active_value).strip().casefold() in {
        "false", "0", "no", "inactive",
    }:
        return False
    from django.utils import dateparse, timezone

    for key in ("deadline", "expires_at"):
        value = record.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            expiry = value
        elif isinstance(value, date):
            expiry = datetime.combine(value, time.max)
        else:
            expiry = dateparse.parse_datetime(str(value))
            if expiry is None:
                parsed_date = dateparse.parse_date(str(value))
                if parsed_date is None:
                    continue
                expiry = datetime.combine(parsed_date, time.max)
        if timezone.is_naive(expiry):
            expiry = timezone.make_aware(expiry, timezone.get_current_timezone())
        if expiry <= now:
            return False
    return True


_ROLE_TITLE_ALIASES = {
    "developer": {"developer", "engineer", "programmer"},
    "engineer": {"engineer", "developer", "programmer"},
    "programmer": {"programmer", "developer", "engineer"},
    "scientist": {"scientist"},
    "analyst": {"analyst"},
    "designer": {"designer"},
    "architect": {"architect"},
    "manager": {"manager"},
}

_TITLE_SYNONYM_LOOKUP = None
_DEFAULT_TITLE_SYNONYMS = (
    "machine learning engineer",
    "ml engineer",
    "ai engineer",
    "ai ml engineer",
    "artificial intelligence engineer",
)
_EXPERIENCE_LEVEL_ALIASES = {
    "entry": {"entry", "entry level", "junior", "graduate", "intern"},
    "junior": {"entry", "entry level", "junior", "graduate", "intern"},
    "mid": {"mid", "mid level", "intermediate"},
    "intermediate": {"mid", "mid level", "intermediate"},
    "senior": {"senior", "lead", "staff", "principal"},
    "lead": {"senior", "lead", "staff", "principal"},
    "principal": {"senior", "lead", "staff", "principal"},
}


def _normalized_title_text(value):
    return " ".join(re.findall(r"[a-z0-9+#.]+", normalize(value)))


def _title_synonym_variants(query):
    global _TITLE_SYNONYM_LOOKUP
    if _TITLE_SYNONYM_LOOKUP is None:
        from .taxonomy import TitleSynonym

        groups = {
            variant: set(_DEFAULT_TITLE_SYNONYMS)
            for variant in _DEFAULT_TITLE_SYNONYMS
        }
        for entry in TitleSynonym.objects.all():
            variants = {
                _normalized_title_text(value)
                for value in [entry.title, *(entry.synonyms or [])]
                if _normalized_title_text(value)
            }
            for variant in variants:
                groups[variant] = variants
        _TITLE_SYNONYM_LOOKUP = groups
    normalized_query = _normalized_title_text(query)
    return _TITLE_SYNONYM_LOOKUP.get(normalized_query, {normalized_query})


def _title_relevance(query, title):
    query_tokens = re.findall(r"[a-z0-9+#.]+", normalize(query))
    title_tokens = set(re.findall(r"[a-z0-9+#.]+", normalize(title)))
    if not query_tokens or not title_tokens:
        return 0.0
    normalized_query = " ".join(query_tokens)
    normalized_title = " ".join(re.findall(r"[a-z0-9+#.]+", normalize(title)))
    variants = _title_synonym_variants(query)
    if normalized_title in variants:
        return 1.0

    matched_weight = 0
    total_weight = 0
    for token in query_tokens:
        aliases = _ROLE_TITLE_ALIASES.get(token, {token})
        weight = 2 if token in _ROLE_TITLE_ALIASES else 1
        total_weight += weight
        if aliases & title_tokens:
            matched_weight += weight
    return matched_weight / total_weight if total_weight else 0.0


def _title_matches_query(query, title):
    title_tokens = set(re.findall(r"[a-z0-9+#.]+", normalize(title)))
    title_text = _normalized_title_text(title)
    if title_text in _title_synonym_variants(query):
        return True
    query_tokens = re.findall(r"[a-z0-9+#.]+", normalize(query))
    return bool(query_tokens) and all(
        _ROLE_TITLE_ALIASES.get(token, {token}) & title_tokens
        for token in query_tokens
    )


def search_jobs(filters=None):
    """Search across both database and dataset jobs with relevance ranking.

    Prioritises exact title matches, skill overlap, and recency.
    """
    from .models import Job
    from django.db.models import Q
    from django.utils import timezone
    import re

    filters = filters or {}
    now = timezone.now()
    # Base queryset for active, open jobs
    database_jobs = Job.objects.filter(is_active=True, status="open").filter(
        Q(deadline__isnull=True) | Q(deadline__gt=now)
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    ).select_related("recruiter").order_by("-created_at")

    # Normalize filters before scanning records so per-job work stays minimal.
    keyword = str(filters.get("q", "")).strip()
    title_filter = str(filters.get("title", "")).strip()
    skills_filter = str(filters.get("skills", "")).strip().casefold()
    category = str(filters.get("category", "")).strip().casefold()
    location = str(filters.get("location", "")).strip().casefold()
    company = str(filters.get("company", "")).strip().casefold()
    employment = str(filters.get("type", filters.get("employment_type", ""))).strip().casefold()
    work_mode = str(filters.get("work_mode", "")).strip().casefold()
    experience_level = normalize(filters.get("experience_level", ""))
    experience = str(filters.get("experience", "")).strip()
    try:
        experience_years = float(experience) if experience else None
        if experience_years is not None and experience_years < 0:
            experience_years = None
    except ValueError:
        experience_years = None
    try:
        minimum_salary = float(filters.get("min_salary", "") or 0) or None
        maximum_salary = float(filters.get("max_salary", "") or 0) or None
        if minimum_salary is not None and minimum_salary < 0:
            minimum_salary = None
        if maximum_salary is not None and maximum_salary < 0:
            maximum_salary = None
    except (TypeError, ValueError):
        minimum_salary = maximum_salary = None
    keyword_tokens = re.findall(r"[a-z0-9+#.]+", normalize(keyword))
    requested_skills = set(extract_skills(skills_filter)) if skills_filter else set()
    query = title_filter or keyword
    query_skills = set(extract_skills(query)) if query else set()
    sort_order = str(filters.get("sort", "newest")).casefold()
    posted_within = str(filters.get("posted_within", "")).strip()
    if posted_within not in {"7", "30"}:
        posted_within = ""
    has_constraints = any((
        keyword_tokens, title_filter, skills_filter, category, location, company,
        employment, work_mode, experience_level, experience_years is not None,
        minimum_salary is not None, maximum_salary is not None, posted_within,
    ))
    if company:
        database_jobs = database_jobs.filter(company__icontains=company)
    if title_filter:
        for token in re.findall(r"[a-z0-9+#.]+", normalize(title_filter)):
            variants = _ROLE_TITLE_ALIASES.get(token, {token})
            title_match = Q()
            for variant in variants:
                title_match |= Q(title__icontains=variant)
            database_jobs = database_jobs.filter(title_match)
    if keyword_tokens:
        for token in keyword_tokens:
            variants = _ROLE_TITLE_ALIASES.get(token, {token})
            keyword_match = Q()
            for variant in variants:
                for field in (
                    "title", "company", "required_skills", "preferred_skills",
                    "category", "description",
                ):
                    keyword_match |= Q(**{f"{field}__icontains": variant})
            database_jobs = database_jobs.filter(keyword_match)
    if category:
        database_jobs = database_jobs.filter(category__icontains=category)
    if location:
        database_jobs = database_jobs.filter(location__icontains=location)
    if employment:
        database_jobs = database_jobs.filter(employment_type__icontains=employment)
    if work_mode:
        database_jobs = database_jobs.filter(work_mode__iexact=work_mode)
    if skills_filter:
        search_fields = (
            "required_skills", "preferred_skills", "description", "responsibilities",
        )
        if requested_skills:
            for skill in requested_skills:
                skill_match = Q()
                for alias in SKILLS.get(skill, [skill]):
                    for field in search_fields:
                        skill_match |= Q(**{f"{field}__icontains": alias})
                database_jobs = database_jobs.filter(skill_match)
        else:
            skill_match = Q()
            for field in search_fields:
                skill_match |= Q(**{f"{field}__icontains": skills_filter})
            database_jobs = database_jobs.filter(skill_match)
    if experience_years is not None:
        database_jobs = database_jobs.filter(
            required_experience_years__lte=experience_years
        ).filter(
            Q(max_experience_years__isnull=True)
            | Q(max_experience_years=0)
            | Q(max_experience_years__gte=experience_years)
        )
    if posted_within:
        from datetime import timedelta

        database_jobs = database_jobs.filter(
            created_at__gte=now - timedelta(days=int(posted_within))
        )

    # Gather and deduplicate records without building an extra combined list.
    database_records = _database_records(database_jobs, include_skills=False)
    dataset_records = (
        [] if filters.get("database_only") else _dataset_records()
    )
    seen = set()
    unique_records = []
    for record in chain(database_records, dataset_records):
        if not _record_is_available(record, now):
            continue
        fp = _job_fingerprint(record)
        if fp in seen:
            continue
        seen.add(fp)
        unique_records.append(record)

    # Apply filters
    filtered = []
    for record in unique_records:
        if not has_constraints:
            filtered.append(record)
            continue
        searchable = ""
        if keyword_tokens:
            searchable = " ".join(
                str(record.get(key, "") or "")
                for key in (
                    "job_title", "company", "required_skills",
                    "preferred_skills", "category", "description",
                )
            ).casefold()
            searchable_tokens = set(re.findall(r"[a-z0-9+#.]+", searchable))
            keyword_matches = (
                all(
                    _ROLE_TITLE_ALIASES.get(token, {token}) & searchable_tokens
                    for token in keyword_tokens
                )
                or _title_matches_query(keyword, record.get("job_title", ""))
            )
            if not keyword_matches:
                continue
        if title_filter and not _title_matches_query(title_filter, record.get("job_title", "")):
            continue
        if skills_filter:
            searchable = searchable or " ".join(
                str(record.get(key, "") or "")
                for key in (
                    "job_title", "company", "required_skills",
                    "preferred_skills", "category", "description",
                )
            ).casefold()
            job_skills = _record_skill_names(record)
            job_skills.update(extract_skills(
                f"{record.get('required_skills', '')} "
                f"{record.get('preferred_skills', '')}"
            ))
            if requested_skills and not requested_skills.issubset(job_skills):
                continue
            if not requested_skills and skills_filter not in searchable:
                continue
        if category and category not in str(record.get("category", "")).casefold():
            continue
        if location and location not in str(record.get("location", "")).casefold():
            continue
        if company and company not in str(record.get("company", "")).casefold():
            continue
        if employment and employment not in str(record.get("employment_type", "")).casefold():
            continue
        if work_mode and work_mode != str(record.get("work_mode", "")).casefold():
            continue
        if minimum_salary is not None or maximum_salary is not None:
            salary_bounds = _annual_salary_bounds(record.get("salary", ""))
            if salary_bounds is None:
                continue
            lower_salary, upper_salary = salary_bounds
            if minimum_salary is not None and upper_salary < minimum_salary:
                continue
            if maximum_salary is not None and lower_salary > maximum_salary:
                continue
        if experience_level:
            job_level = normalize(record.get("experience_level", ""))
            matching_levels = _EXPERIENCE_LEVEL_ALIASES.get(
                experience_level, {experience_level}
            )
            if not job_level or not any(
                level == job_level
                or level in job_level
                or job_level in level
                for level in matching_levels
            ):
                continue
        if experience_years is not None:
            try:
                required_years = float(record.get("required_experience_years") or 0)
            except (TypeError, ValueError):
                required_years = 0
            max_years = _maximum_experience_requirement(record)
            if required_years > experience_years:
                continue
            if max_years and experience_years > max_years:
                continue
        if posted_within:
            created_at = record.get("created_at")
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
            if isinstance(created_at, date) and not isinstance(created_at, datetime):
                created_at = datetime.combine(created_at, time.min)
            if not isinstance(created_at, datetime):
                continue
            if timezone.is_naive(created_at):
                created_at = timezone.make_aware(created_at)
            if created_at < now - timedelta(days=int(posted_within)):
                continue
        filtered.append(record)

    def relevance(record):
        title_score = _title_relevance(query, record.get("job_title", "")) if query else 0
        job_skills = set()
        if query_skills:
            job_skills = _record_skill_names(record)
            job_skills.update(extract_skills(
                f"{record.get('required_skills', '')} "
                f"{record.get('preferred_skills', '')}"
            ))
        skill_score = (
            len(job_skills & query_skills) / len(query_skills)
            if query_skills
            else 0
        )
        title = normalize(record.get("job_title", ""))
        phrase_score = float(bool(query and normalize(query) in title))
        return title_score, phrase_score, skill_score

    def recency(record):
        created_at = record.get("created_at")
        return (
            created_at.isoformat()
            if hasattr(created_at, "isoformat")
            else str(created_at or "")
        )

    if sort_order == "title":
        if query:
            filtered.sort(key=lambda record: (
                tuple(-value for value in relevance(record)),
                normalize(record.get("job_title", "")),
            ))
        else:
            filtered.sort(key=lambda record: normalize(record.get("job_title", "")))
    elif sort_order == "oldest":
        if query:
            filtered.sort(key=lambda record: (
                tuple(-value for value in relevance(record)),
                recency(record),
            ), reverse=False)
        else:
            filtered.sort(key=recency)
    else:
        filtered.sort(key=lambda record: (
            relevance(record) if query else (),
            recency(record),
        ), reverse=True)
    return filtered


def search_database_jobs(filters=None):
    """Build a database-only job search queryset with filters pushed into SQL."""
    from datetime import timedelta
    from django.db.models import Case, IntegerField, Q, Value, When
    from django.utils import timezone
    from .models import Job

    filters = filters or {}
    now = timezone.now()
    jobs = Job.objects.filter(
        is_active=True,
        status="open",
    ).filter(
        Q(deadline__isnull=True) | Q(deadline__gt=now)
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )

    query = str(filters.get("q", "")).strip()
    title = str(filters.get("title", "")).strip()
    search_text = title or query
    fields = (
        "title", "company", "required_skills", "preferred_skills",
        "category", "description", "responsibilities",
    )
    for token in re.findall(r"[a-z0-9+#.]+", normalize(query)):
        variants = _ROLE_TITLE_ALIASES.get(token, {token})
        token_filter = Q()
        for variant in variants:
            for field in fields:
                token_filter |= Q(**{f"{field}__icontains": variant})
        jobs = jobs.filter(token_filter)

    for token in re.findall(r"[a-z0-9+#.]+", normalize(title)):
        title_filter = Q()
        for variant in _ROLE_TITLE_ALIASES.get(token, {token}):
            title_filter |= Q(title__icontains=variant)
        jobs = jobs.filter(title_filter)

    company = str(filters.get("company", "")).strip()
    category = str(filters.get("category", "")).strip()
    location = str(filters.get("location", "")).strip()
    employment = str(filters.get("type", filters.get("employment_type", ""))).strip()
    work_mode = str(filters.get("work_mode", "")).strip()
    experience_level = normalize(filters.get("experience_level", ""))
    if company:
        jobs = jobs.filter(company__icontains=company)
    if category:
        jobs = jobs.filter(category__icontains=category)
    if location:
        jobs = jobs.filter(location__icontains=location)
    if employment:
        jobs = jobs.filter(employment_type__icontains=employment)
    if work_mode in dict(Job.WORK_MODE_CHOICES):
        jobs = jobs.filter(work_mode=work_mode)
    if experience_level:
        level_aliases = _EXPERIENCE_LEVEL_ALIASES.get(
            experience_level, {experience_level}
        )
        level_filter = Q()
        for alias in level_aliases:
            level_filter |= Q(experience_level__icontains=alias)
        jobs = jobs.filter(level_filter)

    skill_query = str(filters.get("skills", "")).strip()
    requested_skills = extract_skills(skill_query)
    skill_fields = (
        "required_skills", "preferred_skills", "description", "responsibilities",
    )
    if requested_skills:
        for skill in requested_skills:
            skill_filter = Q()
            for alias in SKILLS.get(skill, [skill]):
                for field in skill_fields:
                    skill_filter |= Q(**{f"{field}__icontains": alias})
            jobs = jobs.filter(skill_filter)
    elif skill_query:
        skill_filter = Q()
        for field in skill_fields:
            skill_filter |= Q(**{f"{field}__icontains": skill_query})
        jobs = jobs.filter(skill_filter)

    experience = str(filters.get("experience", "")).strip()
    try:
        experience_years = float(experience) if experience else None
    except ValueError:
        experience_years = None
    if experience_years is not None and experience_years >= 0:
        jobs = jobs.filter(
            required_experience_years__lte=experience_years
        ).filter(
            Q(max_experience_years__isnull=True)
            | Q(max_experience_years=0)
            | Q(max_experience_years__gte=experience_years)
        )

    posted_within = str(filters.get("posted_within", "")).strip()
    if posted_within in {"7", "30"}:
        jobs = jobs.filter(
            created_at__gte=now - timedelta(days=int(posted_within))
        )

    try:
        minimum_salary = float(filters.get("min_salary", "") or 0) or None
        maximum_salary = float(filters.get("max_salary", "") or 0) or None
    except (TypeError, ValueError):
        minimum_salary = maximum_salary = None
    if (
        minimum_salary is not None
        and maximum_salary is not None
        and maximum_salary < minimum_salary
    ):
        return jobs.none().order_by("-created_at", "pk")
    if minimum_salary is not None or maximum_salary is not None:
        matching_ids = []
        for job in jobs.only("id", "salary").iterator():
            bounds = _annual_salary_bounds(job.salary)
            if bounds is None:
                continue
            lower, upper = bounds
            if minimum_salary is not None and upper < minimum_salary:
                continue
            if maximum_salary is not None and lower > maximum_salary:
                continue
            matching_ids.append(job.pk)
        jobs = jobs.filter(pk__in=matching_ids)

    sort_order = str(filters.get("sort", "newest")).casefold()
    if sort_order == "oldest":
        jobs = jobs.order_by("created_at", "pk")
    elif sort_order == "title":
        jobs = jobs.order_by("title", "-created_at")
    elif sort_order == "relevant" and search_text:
        normalized_query = normalize(search_text)
        tokens = re.findall(r"[a-z0-9+#.]+", normalized_query)
        relevance = Case(
            When(title__iexact=search_text, then=Value(100)),
            When(title__icontains=search_text, then=Value(50)),
            default=Value(0),
            output_field=IntegerField(),
        )
        for token in tokens:
            matching_fields = Q()
            for field in (
                "title", "required_skills", "preferred_skills", "category",
            ):
                matching_fields |= Q(**{f"{field}__icontains": token})
            relevance = relevance + Case(
                When(matching_fields, then=Value(10)),
                default=Value(0),
                output_field=IntegerField(),
            )
        jobs = jobs.annotate(_search_relevance=relevance).order_by(
            "-_search_relevance", "-created_at", "pk"
        )
    else:
        jobs = jobs.order_by("-created_at", "pk")
    return jobs.select_related("recruiter").only(
        "id", "recruiter_id", "title", "company", "company_logo",
        "location", "employment_type", "experience_level",
        "required_experience_years", "max_experience_years", "salary",
        "category", "work_mode", "required_skills", "preferred_skills",
        "description", "responsibilities", "created_at", "deadline",
        "expires_at",
    )


def _experience_requirement(record, bundle):
    try:
        years = float(record.get("required_experience_years") or 0)
    except (TypeError, ValueError):
        years = 0
    if years:
        return years
    level = str(record.get("experience_level", "")).casefold()
    for label, threshold in (bundle or {}).get("experience_level_years", {}).items():
        if label in level and threshold:
            return float(threshold)
    return 0

def _maximum_experience_requirement(record):
    try:
        years = float(record.get("max_experience_years") or 0)
    except (TypeError, ValueError):
        years = 0
    return years if years > 0 else 0

def _ranking_weights():
    configured = getattr(settings, "JOBPILOT_RANKING_WEIGHTS", {})
    features = ("text_similarity", "skill_f1", "experience", "education")
    if not isinstance(configured, dict):
        raise ImproperlyConfigured("JOBPILOT_RANKING_WEIGHTS must be a dictionary.")
    weights = {}
    for feature in features:
        try:
            weight = float(configured[feature])
        except (KeyError, TypeError, ValueError) as exc:
            raise ImproperlyConfigured(
                f"JOBPILOT_RANKING_WEIGHTS must define a numeric {feature!r} weight."
            ) from exc
        if not math.isfinite(weight) or weight < 0:
            raise ImproperlyConfigured(
                f"JOBPILOT_RANKING_WEIGHTS[{feature!r}] must be finite and non-negative."
            )
        weights[feature] = weight
    total = sum(weights.values())
    if total <= 0:
        raise ImproperlyConfigured("At least one recommendation weight must be positive.")
    return {feature: weight / total for feature, weight in weights.items()}

def _weighted_match_score(components, weights):
    normalized = {
        feature: min(1.0, max(0.0, float(components[feature])))
        for feature in weights
    }
    score = sum(weights[feature] * normalized[feature] for feature in weights)
    return round(score * 100, 1), {
        feature: round(value * 100, 1)
        for feature, value in normalized.items()
    }


def _resume_skill_evidence(text, skills):
    evidence = []
    for line in _resume_evidence_text(text).splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+", line.strip()):
            sentence = sentence.strip(" \t-*•")
            if not sentence:
                continue
            sentence_skills = set(extract_skills(sentence))
            for skill in sorted(sentence_skills & skills):
                evidence.append({
                    "skill": skill,
                    "text": sentence[:240],
                })
    return evidence


def _similarity_scores(vectorizer, text, documents):
    """Fallback TF‑IDF similarity (kept for backward compatibility)."""
    if not documents:
        return np.zeros(0)
    if vectorizer is None:
        return np.zeros(len(documents))
    try:
        from sklearn.metrics.pairwise import linear_kernel
        return linear_kernel(vectorizer.transform([text]), vectorizer.transform(documents)).ravel()
    except (ValueError, TypeError, AttributeError) as exc:
        logger.warning("Job text similarity unavailable: %s", exc, exc_info=True)
        return np.zeros(len(documents))


def _dataset_similarity_scores(vectorizer, text, records):
    global _dataset_similarity_matrix, _dataset_similarity_matrix_key
    if not records or not _dataset_records_cache:
        return None
    if any(record.get("source") != "dataset" for record in records):
        return None
    try:
        indices = [int(record["dataset_index"]) for record in records]
    except (KeyError, TypeError, ValueError):
        return None
    if any(index < 0 or index >= len(_dataset_records_cache) for index in indices):
        return None

    cache_key = (id(vectorizer), id(_dataset_records_cache))
    try:
        from sklearn.metrics.pairwise import linear_kernel

        with _similarity_matrix_lock:
            if _dataset_similarity_matrix_key != cache_key:
                documents = [
                    " ".join(str(record.get(key, "") or "") for key in (
                        "job_title", "category", "industry", "required_skills",
                        "preferred_skills", "education_requirements", "qualifications",
                        "description", "responsibilities",
                    ))
                    for record in _dataset_records_cache
                ]
                _dataset_similarity_matrix = vectorizer.transform(documents)
                _dataset_similarity_matrix_key = cache_key
            matrix = _dataset_similarity_matrix

        return linear_kernel(
            vectorizer.transform([text]),
            matrix[indices],
        ).ravel()
    except (ValueError, TypeError, AttributeError, IndexError) as exc:
        logger.warning(
            "Cached dataset text similarity unavailable: %s", exc, exc_info=True
        )
        return None


def _database_similarity_scores(vectorizer, text, documents):
    global _database_similarity_matrix, _database_similarity_matrix_key
    if not documents or vectorizer is None:
        return None
    digest = hashlib.sha256()
    for document in documents:
        digest.update(document.encode("utf-8", errors="replace"))
        digest.update(b"\0")
    cache_key = (id(vectorizer), digest.digest())
    try:
        from sklearn.metrics.pairwise import linear_kernel

        with _similarity_matrix_lock:
            if _database_similarity_matrix_key != cache_key:
                _database_similarity_matrix = vectorizer.transform(documents)
                _database_similarity_matrix_key = cache_key
            matrix = _database_similarity_matrix

        return linear_kernel(
            vectorizer.transform([text]),
            matrix,
        ).ravel()
    except (ValueError, TypeError, AttributeError, IndexError) as exc:
        logger.warning(
            "Cached database text similarity unavailable: %s", exc, exc_info=True
        )
        return None


def _local_embedding_similarities(text, documents):
    """Use a configured local transformer model without downloading model files."""
    global _embed_model, _embed_model_attempted, _embed_model_source
    if not documents:
        return np.zeros(0), ""
    if _embed_model is None:
        model_path = getattr(settings, "JOBPILOT_EMBEDDING_MODEL_PATH", "")
        if not model_path or not Path(model_path).is_dir():
            return np.zeros(len(documents)), ""
        if not _embed_model_attempted:
            _embed_model_attempted = True
            try:
                from sentence_transformers import SentenceTransformer

                _embed_model = SentenceTransformer(
                    str(model_path), local_files_only=True
                )
                _embed_model_source = "local sentence embeddings"
            except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Local sentence embedding model is unavailable at %s: %s",
                    model_path,
                    exc,
                )
                _embed_model = None
                _embed_model_source = ""
    if _embed_model is None:
        return np.zeros(len(documents)), ""
    try:
        vectors = _embed_model.encode(
            [text, *documents], normalize_embeddings=True
        )
        similarities = np.asarray(vectors[1:]) @ np.asarray(vectors[0])
        return np.clip(similarities, 0.0, 1.0), _embed_model_source
    except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
        logger.warning("Local sentence embedding inference failed: %s", exc)
        return np.zeros(len(documents)), ""


_ROLE_PATTERNS = {
    "python developer": (
        r"\bpython\s+(?:developer|engineer|programmer)\b",
        r"\bdjango\s+developer\b",
    ),
    "java developer": (r"\bjava\s+(?:developer|engineer|programmer)\b",),
    "data analyst": (r"\bdata\s+analyst\b",),
    "data scientist": (r"\bdata\s+scientist\b",),
    "machine learning engineer": (
        r"\bmachine learning\s+engineer\b",
        r"\bml\s+engineer\b",
        r"\bai\s*/\s*ml\s+engineer\b",
        r"\bartificial intelligence\s+engineer\b",
        r"\bai\s+engineer\b",
    ),
    "web developer": (
        r"\bweb\s+developer\b",
        r"\bfront[\s-]?end\s+developer\b",
        r"\bback[\s-]?end\s+developer\b",
        r"\bfull[\s-]?stack\s+developer\b",
        r"\breact\s+developer\b",
    ),
}


def _candidate_roles(text, skills, candidate_profile):
    role_evidence = []
    for key in ("headline", "target_job_title", "preferred_roles"):
        role_evidence.extend(
            value.strip()
            for value in re.split(r"[,;\n|]", str(candidate_profile.get(key, "") or ""))
            if value.strip()
        )
    role_evidence.append(_resume_evidence_text(text))
    role_evidence.append(str(candidate_profile.get("predicted_category", "") or ""))
    roles = set()
    for role, patterns in _ROLE_PATTERNS.items():
        if any(
            re.search(pattern, source, flags=re.IGNORECASE)
            for pattern in patterns
            for source in role_evidence
        ):
            roles.add(role)

    skill_set = set(skills)
    inferred = (
        ("data analyst", {"sql", "power bi", "tableau", "excel", "pandas", "data analysis", "statistics"}),
        ("data scientist", {"python", "machine learning", "statistics", "pandas", "scikit-learn", "data science"}),
        ("python developer", {"python", "django", "flask", "fastapi", "rest api"}),
        ("java developer", {"java", "spring boot"}),
        ("machine learning engineer", {"python", "machine learning", "tensorflow", "pytorch", "nlp"}),
        ("web developer", {"javascript", "react", "html", "css", "node.js"}),
    )
    for role, supporting_skills in inferred:
        matches = skill_set & supporting_skills
        minimum = 3 if role in {"data analyst", "data scientist", "web developer"} else 2
        if len(matches) >= minimum:
            roles.add(role)
    return sorted(roles)


def _role_relevance(candidate_roles, job_title, category=""):
    title = normalize(job_title)
    role_text = normalize(f"{job_title} {category}")
    best = 0.0
    for role_value in candidate_roles:
        role = normalize(role_value)
        if role in title:
            best = max(best, 1.0)
        elif role == "python developer" and any(
            term in title for term in (
                "python engineer", "python programmer", "django developer",
            )
        ):
            best = max(best, 0.9)
        elif role == "machine learning engineer" and any(
            term in title for term in (
                "ml engineer", "ai engineer", "ai/ml engineer",
                "artificial intelligence engineer",
            )
        ):
            best = max(best, 0.9)
        elif role == "web developer" and any(
            term in title for term in ("frontend developer", "front-end developer",
                                       "backend developer", "back-end developer",
                                       "full stack developer", "react developer")
        ):
            best = max(best, 0.8)
        elif role in role_text:
            best = max(best, 0.6)
    return best


def _education_compatibility(candidate_education, requirement):
    if not requirement:
        return None
    if not candidate_education:
        return None
    candidate = {normalize(value) for value in candidate_education}
    required = {normalize(value) for value in extract_education(requirement)}
    if candidate & required:
        return 1.0
    degree_families = {
        "bachelor": {"be", "btech", "bsc", "bca"},
        "master": {"mtech", "msc", "mca"},
        "doctorate": {"phd"},
    }

    def families(values, source):
        result = {
            family for family, members in degree_families.items()
            if values & members
        }
        source = normalize(source)
        if re.search(r"\b(?:bachelor|undergraduate)\b", source):
            result.add("bachelor")
        if re.search(r"\b(?:master|postgraduate)\b", source):
            result.add("master")
        if re.search(r"\b(?:ph\.?d|doctorate)\b", source):
            result.add("doctorate")
        return result

    candidate_families = families(candidate, " ".join(candidate_education))
    required_families = families(required, requirement)
    if candidate_families & required_families:
        return 0.85
    if candidate & {"computer science", "information technology", "data science",
                    "artificial intelligence"} and (
        required & {"computer science", "information technology", "data science",
                    "artificial intelligence"}
    ):
        return 0.8
    return 0.25


def _rank_dataset_records(text, records, skills, education, experience_years, bundle, top_n):
    """Rank dataset jobs using a combination of embedding similarity and skill/experience scores.
    If the embedding model is unavailable we gracefully fall back to the original TF‑IDF path.
    """
    if not records:
        return []
    vectorizer = bundle.get("tfidf_vectorizer")
    matrix = bundle.get("job_matrix")
    skill_matrix = bundle.get("job_skill_matrix")
    labels = bundle.get("skill_labels", [])
    frame = bundle.get("jobs")
    if _embed_model is None or vectorizer is None or matrix is None or skill_matrix is None or frame is None:
        # Fallback to previous TF‑IDF based ranking
        return _rank_records(text, records, skills, education, experience_years, bundle, top_n)
    indices = np.asarray(
        [record["dataset_index"] for record in records], dtype=int
    )
    # ----- Embedding similarity -----
    try:
        resume_vec = _embed_model.encode([text], normalize_embeddings=True)[0]
        job_texts = [
            " ".join([
                str(r.get('job_title','')),
                str(r.get('description','')),
                str(r.get('required_skills','')),
                str(r.get('preferred_skills','')),
            ])
            for r in records
        ]
        job_vecs = _embed_model.encode(job_texts, normalize_embeddings=True)
        # Cosine similarity via dot product (vectors are normalized)
        embedding_scores = np.dot(job_vecs, resume_vec)
    except Exception as exc:
        logger.warning("Embedding similarity failed: %s", exc)
        embedding_scores = np.zeros(len(records))
    # ----- Skill overlap (unchanged) -----
    skill_to_col = bundle.get("skill_to_col", {})
    resume_columns = [
        skill_to_col[skill] for skill in skills if skill in skill_to_col
    ]
    if resume_columns:
        resume_vector = np.zeros(skill_matrix.shape[1], dtype=np.float32)
        resume_vector[resume_columns] = 1
        selected_skills = skill_matrix[indices]
        overlap_counts = np.asarray(selected_skills @ resume_vector).ravel()
        job_skill_counts = np.asarray(selected_skills.getnnz(axis=1)).ravel()
        skill_f1 = np.divide(
            2 * overlap_counts,
            len(resume_columns) + job_skill_counts,
            out=np.zeros(len(records), dtype=float),
            where=(len(resume_columns) + job_skill_counts) > 0,
        )
    else:
        selected_skills = skill_matrix[indices]
        overlap_counts = np.zeros(len(records))
        job_skill_counts = np.asarray(selected_skills.getnnz(axis=1)).ravel()
        skill_f1 = np.zeros(len(records))
    # ----- Experience and education scores (unchanged) -----
    try:
        required_years = frame["required_experience_years"].to_numpy(dtype=float)[indices]
    except (KeyError, TypeError, ValueError):
        required_years = np.zeros(len(records))
    required_years = np.nan_to_num(required_years, nan=0.0, posinf=0.0, neginf=0.0)
    if not np.any(required_years):
        for record_index, record in enumerate(records):
            required_years[record_index] = _experience_requirement(record, bundle)
    experience_scores = np.ones(len(records))
    has_requirement = required_years > 0
    experience_scores[has_requirement] = np.minimum(
        1, experience_years / required_years[has_requirement]
    )
    maximum_years = np.asarray([
        _maximum_experience_requirement(record) for record in records
    ], dtype=float)
    overqualified = (maximum_years > 0) & (experience_years > maximum_years)
    experience_scores[overqualified] = np.minimum(
        experience_scores[overqualified],
        maximum_years[overqualified] / experience_years,
    )
    education_text = [
        f"{record.get('education_requirements', '')} {record.get('qualifications', '')}".casefold()
        for record in records
    ]
    education_matches = np.asarray([
        any(item.casefold() in requirements for item in education)
        for requirements in education_text
    ], dtype=float)
    education_scores = np.asarray([
        education_matches[index] if requirements.strip() else 1.0
        for index, requirements in enumerate(education_text)
    ], dtype=float)
    # ----- Combine with weighting -----
    weights = _ranking_weights()
    # Replace "text_similarity" weight with embedding similarity
    scores = 100 * (
        weights["text_similarity"] * embedding_scores +
        weights["skill_f1"] * skill_f1 +
        weights["experience"] * experience_scores +
        weights["education"] * education_scores
    )
    candidate_count = min(len(records), max(top_n * 5, top_n))
    if candidate_count < len(records):
        candidates = np.argpartition(scores, -candidate_count)[-candidate_count:]
    else:
        candidates = np.arange(len(records))
    ranked = []
    for candidate in candidates:
        record = records[int(candidate)]
        row_skills = selected_skills.getrow(int(candidate)).indices
        job_skills = {labels[column] for column in row_skills if column < len(labels)}
        matched = sorted(set(skills) & job_skills)
        requirement = required_years[candidate]
        maximum = maximum_years[candidate]
        score, score_components = _weighted_match_score({
            "text_similarity": embedding_scores[candidate],
            "skill_f1": skill_f1[candidate],
            "experience": experience_scores[candidate],
            "education": education_scores[candidate],
        }, weights)
        compatibility = (
            f"Requires {requirement:g} years; your resume shows {experience_years:g}"
            if requirement and experience_years < requirement
            else f"Role targets up to {maximum:g} years; your resume shows {experience_years:g}"
            if maximum and experience_years > maximum
            else f"Fits the {requirement:g}-{maximum:g}-year experience range"
            if maximum
            else f"Meets the {requirement:g}-year requirement"
            if requirement
            else "No specific experience requirement"
        )
        reasons = []
        if matched:
            reasons.append("Matches " + ", ".join(matched[:4]))
        if requirement and experience_years >= requirement:
            reasons.append("Meets the experience requirement")
        if education_matches[candidate]:
            reasons.append("Education matches job requirements")
        if embedding_scores[candidate] > 0:
            reasons.append("Resume content is semantically similar to the job description")
        ranked.append({
            **record,
            "match_score": score,
            "score_components": score_components,
            "matching_skills": matched,
            "missing_skills": sorted(job_skills - set(skills)),
            "skills": sorted(job_skills),
            "experience_compatibility": compatibility,
            "why_match": "; ".join(reasons) or "Role details are available to review",
        })
    ranked.sort(key=lambda record: (-record["match_score"], record["job_title"].casefold()))
    return ranked[:candidate_count]
    indices = np.asarray([record["dataset_index"] for record in records], dtype=int)
    try:
        from sklearn.metrics.pairwise import linear_kernel
        text_scores = linear_kernel(
            vectorizer.transform([text]), matrix[indices]
        ).ravel()
    except (ValueError, TypeError, AttributeError, IndexError) as exc:
        logger.warning("Dataset recommendation similarity unavailable: %s", exc, exc_info=True)
        text_scores = np.zeros(len(records))

    skill_to_col = bundle.get("skill_to_col", {})
    resume_columns = [
        skill_to_col[skill] for skill in skills if skill in skill_to_col
    ]
    if resume_columns:
        resume_vector = np.zeros(skill_matrix.shape[1], dtype=np.float32)
        resume_vector[resume_columns] = 1
        selected_skills = skill_matrix[indices]
        overlap_counts = np.asarray(selected_skills @ resume_vector).ravel()
        job_skill_counts = np.asarray(selected_skills.getnnz(axis=1)).ravel()
        skill_f1 = np.divide(
            2 * overlap_counts,
            len(resume_columns) + job_skill_counts,
            out=np.zeros(len(records), dtype=float),
            where=(len(resume_columns) + job_skill_counts) > 0,
        )
    else:
        selected_skills = skill_matrix[indices]
        overlap_counts = np.zeros(len(records))
        job_skill_counts = np.asarray(selected_skills.getnnz(axis=1)).ravel()
        skill_f1 = np.zeros(len(records))

    try:
        required_years = frame["required_experience_years"].to_numpy(dtype=float)[indices]
    except (KeyError, TypeError, ValueError):
        required_years = np.zeros(len(records))
    required_years = np.nan_to_num(required_years, nan=0.0, posinf=0.0, neginf=0.0)
    if not np.any(required_years):
        for record_index, record in enumerate(records):
            required_years[record_index] = _experience_requirement(record, bundle)
    experience_scores = np.ones(len(records))
    has_requirement = required_years > 0
    experience_scores[has_requirement] = np.minimum(
        1, experience_years / required_years[has_requirement]
    )
    maximum_years = np.asarray(
        [_maximum_experience_requirement(record) for record in records], dtype=float
    )
    overqualified = (maximum_years > 0) & (experience_years > maximum_years)
    experience_scores[overqualified] = np.minimum(
        experience_scores[overqualified],
        maximum_years[overqualified] / experience_years,
    )
    education_text = [
        f"{record.get('education_requirements', '')} {record.get('qualifications', '')}"
        .casefold()
        for record in records
    ]
    education_matches = np.asarray([
        any(item.casefold() in requirements for item in education)
        for requirements in education_text
    ], dtype=float)
    education_scores = np.asarray([
        education_matches[index] if requirements.strip() else 1.0
        for index, requirements in enumerate(education_text)
    ], dtype=float)
    weights = _ranking_weights()
    scores = 100 * (
        weights["text_similarity"] * text_scores
        + weights["skill_f1"] * skill_f1
        + weights["experience"] * experience_scores
        + weights["education"] * education_scores
    )
    candidate_count = min(len(records), max(top_n * 5, top_n))
    if candidate_count < len(records):
        candidates = np.argpartition(scores, -candidate_count)[-candidate_count:]
    else:
        candidates = np.arange(len(records))
    ranked = []
    for candidate in candidates:
        record = records[int(candidate)]
        row_skills = selected_skills.getrow(int(candidate)).indices
        job_skills = {labels[column] for column in row_skills if column < len(labels)}
        matched = sorted(set(skills) & job_skills)
        requirement = required_years[candidate]
        maximum = maximum_years[candidate]
        score, score_components = _weighted_match_score({
            "text_similarity": text_scores[candidate],
            "skill_f1": skill_f1[candidate],
            "experience": experience_scores[candidate],
            "education": education_scores[candidate],
        }, weights)
        compatibility = (
            f"Requires {requirement:g} years; your resume shows {experience_years:g}"
            if requirement and experience_years < requirement
            else f"Role targets up to {maximum:g} years; your resume shows {experience_years:g}"
            if maximum and experience_years > maximum
            else f"Fits the {requirement:g}-{maximum:g}-year experience range"
            if maximum
            else f"Meets the {requirement:g}-year requirement"
            if requirement
            else "No specific experience requirement"
        )
        reasons = []
        if matched:
            reasons.append("Matches " + ", ".join(matched[:4]))
        if requirement and experience_years >= requirement:
            reasons.append("Meets the experience requirement")
        if education_matches[candidate]:
            reasons.append("Education matches job requirements")
        if text_scores[candidate] > 0:
            reasons.append("Resume content is similar to the job description")
        ranked.append({
            **record,
            "match_score": score,
            "score_components": score_components,
            "matching_skills": matched,
            "missing_skills": sorted(job_skills - set(skills)),
            "skills": sorted(job_skills),
            "experience_compatibility": compatibility,
            "why_match": "; ".join(reasons) or "Role details are available to review",
        })
    ranked.sort(key=lambda record: (-record["match_score"], record["job_title"].casefold()))
    return ranked[:candidate_count]

def _rank_records(
    text, records, skills, education, experience_years, bundle, top_n,
    candidate_profile=None,
):
    if not records:
        return []
    candidate_profile = candidate_profile or {}
    resume_skills = set(extract_skills(", ".join(skills or [])))
    education = list(education or [])
    evidence_text = _resume_evidence_text(text)
    experience_known = bool(
        candidate_profile.get("experience_known")
        or experience_years
        or re.search(
            r"\b0(?:\.0)?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:professional\s+)?experience\b",
            evidence_text,
            re.I,
        )
        or re.search(
            r"\b(?:fresher|no professional experience)\b",
            evidence_text,
            re.I,
        )
    )
    candidate_roles = _candidate_roles(text, resume_skills, candidate_profile)
    resume_evidence = _resume_skill_evidence(text, resume_skills)
    vectorizer = (bundle or {}).get("tfidf_vectorizer")
    similarities = None
    similarity_source = ""
    if vectorizer is not None:
        similarities = _dataset_similarity_scores(
            vectorizer, evidence_text, records
        )
    if similarities is None:
        documents = [
            " ".join(str(record.get(key, "") or "") for key in (
                "job_title", "category", "industry", "required_skills",
                "preferred_skills", "education_requirements", "qualifications",
                "description", "responsibilities",
            ))
            for record in records
        ]
        similarities, similarity_source = _local_embedding_similarities(
            evidence_text, documents
        )
        if not similarity_source:
            if vectorizer is None:
                similarities = np.zeros(len(documents))
                similarity_source = "text similarity unavailable"
            else:
                similarities = (
                    _database_similarity_scores(
                        vectorizer, evidence_text, documents
                    )
                    if all(record.get("source") == "database" for record in records)
                    else None
                )
                if similarities is None:
                    similarities = _similarity_scores(
                        vectorizer, evidence_text, documents
                    )
                similarity_source = "TF-IDF text similarity"
    else:
        similarity_source = "TF-IDF text similarity"

    weights = {
        "skills": 0.40,
        "title_relevance": 0.25,
        "experience": 0.15,
        "education": 0.10,
        "preferences": 0.10,
    }
    preferred_roles = [
        item.strip() for item in re.split(
            r"[,;\n|]", str(candidate_profile.get("preferred_roles", "") or "")
        ) if item.strip()
    ]
    preferred_locations = [
        item.strip().casefold() for item in re.split(
            r"[,;\n|]", str(candidate_profile.get("preferred_locations", "") or "")
        ) if item.strip()
    ]
    employment_preference = str(
        candidate_profile.get("preferred_employment_type", "") or ""
    ).strip().casefold()
    category_preference = normalize(candidate_profile.get("preferred_category", ""))
    work_mode_preference = normalize(
        candidate_profile.get("preferred_work_mode", "")
    )
    salary_min = candidate_profile.get("preferred_salary_min")
    salary_max = candidate_profile.get("preferred_salary_max")
    if salary_min == "":
        salary_min = None
    if salary_max == "":
        salary_max = None
    candidate_certifications = [
        normalize(value) for value in candidate_profile.get("certifications", [])
    ]
    explicitly_fresher = bool(re.search(
        r"\b(?:fresher|no professional experience)\b",
        evidence_text,
        re.I,
    ))
    ranked = []
    for index, record in enumerate(records):
        required_skills = set(extract_skills(record.get("required_skills", "")))
        preferred_skills = set(extract_skills(record.get("preferred_skills", "")))
        if not required_skills:
            required_skills = _record_skill_names(record, bundle)
        job_skills = required_skills | preferred_skills
        matched_required = resume_skills & required_skills
        matched_preferred = resume_skills & preferred_skills
        matching_skills = sorted(matched_required | matched_preferred)
        required_coverage = (
            len(matched_required) / len(required_skills) if required_skills else None
        )
        preferred_coverage = (
            len(matched_preferred) / len(preferred_skills)
            if preferred_skills else None
        )
        skill_score = None
        if resume_skills and (required_coverage is not None or preferred_coverage is not None):
            skill_parts = []
            if required_coverage is not None:
                skill_parts.append((0.8, required_coverage))
            if preferred_coverage is not None:
                skill_parts.append((0.2, preferred_coverage))
            skill_score = sum(weight * value for weight, value in skill_parts) / sum(
                weight for weight, _ in skill_parts
            )

        title_score = _role_relevance(
            candidate_roles,
            record.get("job_title", ""),
            record.get("category", ""),
        )
        keyword_score = float(similarities[index])
        title_keyword_score = (
            0.85 * title_score + 0.15 * keyword_score
            if candidate_roles else keyword_score
        )
        requirement = _experience_requirement(record, bundle)
        maximum = _maximum_experience_requirement(record)
        experience_score = None
        if requirement or maximum:
            if not experience_known:
                experience_score = 0.5
            else:
                experience_score = 1.0
                if requirement:
                    experience_score = min(
                        1.0, experience_years / requirement
                    ) if experience_years else 0.0
                if maximum and experience_years > maximum:
                    experience_score = min(
                        experience_score, maximum / experience_years
                    )

        education_requirement = " ".join(str(record.get(key, "") or "") for key in (
            "education_requirements", "qualifications",
        )).strip()
        education_score = _education_compatibility(education, education_requirement)
        education_match = (
            education_score >= 0.8 if education_score is not None else None
        )

        preference_signals = []
        if preferred_roles:
            preference_signals.append(float(
                any(_role_relevance([role], record.get("job_title", ""), record.get("category", "")) >= 0.6
                    for role in preferred_roles)
            ))
        if preferred_locations:
            location = str(record.get("location", "") or "").casefold()
            preference_signals.append(float(
                any(value in location for value in preferred_locations)
            ))
        if employment_preference:
            preference_signals.append(float(
                employment_preference in str(record.get("employment_type", "")).casefold()
            ))
        if category_preference:
            preference_signals.append(float(
                category_preference in normalize(record.get("category", ""))
            ))
        if work_mode_preference:
            preference_signals.append(float(
                work_mode_preference == normalize(record.get("work_mode", ""))
            ))
        if salary_min is not None or salary_max is not None:
            salary_bounds = _annual_salary_bounds(record.get("salary", ""))
            if salary_bounds is None:
                preference_signals.append(0.5)
            else:
                lower, upper = salary_bounds
                preference_signals.append(float(
                    (salary_max is None or lower <= float(salary_max))
                    and (salary_min is None or upper >= float(salary_min))
                ))
        preference_score = (
            sum(preference_signals) / len(preference_signals)
            if preference_signals else None
        )

        components = {
            "skills": skill_score,
            "title_relevance": title_keyword_score if title_score or keyword_score else None,
            "experience": experience_score,
            "education": education_score,
            "preferences": preference_score,
        }
        available_weight = sum(
            weights[name] for name, value in components.items() if value is not None
        )
        if not available_weight:
            continue
        score = round(100 * sum(
            weights[name] * value
            for name, value in components.items()
            if value is not None
        ) / available_weight, 1)
        score_components = {
            name: round(value * 100, 1) if value is not None else None
            for name, value in components.items()
        }
        score_components["required_skills"] = (
            round(required_coverage * 100, 1)
            if required_coverage is not None else None
        )
        score_components["preferred_skills"] = (
            round(preferred_coverage * 100, 1)
            if preferred_coverage is not None else None
        )
        score_components["semantic_text_similarity"] = round(
            float(similarities[index]) * 100, 1
        )
        score_components["similarity_source"] = similarity_source

        if (
            requirement
            and experience_known
            and experience_years < requirement
        ):
            continue
        experience_level = normalize(record.get("experience_level", ""))
        if (explicitly_fresher or (experience_known and experience_years <= 0)) and any(
            level in experience_level for level in ("senior", "lead", "principal")
        ):
            continue
        certification_text = " ".join(str(record.get(key, "") or "") for key in (
            "qualifications", "description", "responsibilities",
        ))
        certification_requirements = re.findall(
            r"(?:required certifications?|certifications? required)\s*[:\-]?\s*([^.;\n]+)",
            certification_text,
            flags=re.IGNORECASE,
        )
        certification_requirements.extend(re.findall(
            r"\b([A-Za-z0-9+#. -]{2,40}?)\s+certifications?\s+"
            r"(?:is\s+)?required\b",
            certification_text,
            flags=re.IGNORECASE,
        ))
        certification_requirements.extend(re.findall(
            r"\b(?:required|mandatory)\s+([A-Za-z0-9+#. -]{2,40}?)"
            r"\s+certifications?\b",
            certification_text,
            flags=re.IGNORECASE,
        ))
        missing_certifications = [
            term.strip()
            for item in certification_requirements
            for term in re.split(r"[,/]", item)
            if term.strip()
            and not any(
                term.strip().casefold() in certification
                or certification in term.strip().casefold()
                for certification in candidate_certifications
            )
        ]
        if missing_certifications:
            continue

        relevant_evidence = [
            item for item in resume_evidence if item["skill"] in matching_skills
        ]
        if (
            required_skills
            and resume_skills
            and not matched_required
            and not matched_preferred
            and title_score < 0.6
        ):
            continue
        material_match = bool(
            matching_skills or title_score >= 0.6 or keyword_score >= 0.2
        )
        if not material_match:
            continue
        if required_coverage is not None:
            reasons = [
                f"Matches {len(matched_required)} of {len(required_skills)} required skills"
            ]
        else:
            reasons = []
        if matching_skills:
            reasons.insert(0, "Matches " + ", ".join(matching_skills[:4]))
        if title_score >= 0.6:
            reasons.append("Relevant role: " + ", ".join(candidate_roles[:3]))
        if relevant_evidence:
            reasons.append(
                "Resume evidence: "
                + " | ".join(item["text"] for item in relevant_evidence[:2])
            )
        if education_match:
            reasons.append("Education matches job requirements")
        if experience_score is not None and requirement:
            reasons.append(
                f"Meets the {requirement:g}-year minimum experience requirement"
            )
        if similarity_source == "local sentence embeddings" and keyword_score > 0:
            reasons.append("Resume projects and experience are semantically similar")
        elif keyword_score > 0:
            reasons.append("Resume wording is relevant to the job description")
        if not experience_known and (requirement or maximum):
            compatibility = "Your resume does not state professional experience"
        elif requirement:
            compatibility = (
                f"Requires {requirement:g} years; your resume shows "
                f"{experience_years:g}"
            )
        elif maximum:
            compatibility = (
                f"Role targets up to {maximum:g} years; your resume shows "
                f"{experience_years:g}"
            )
        else:
            compatibility = "No specific experience requirement"
        ranked.append({
            **record,
            "match_score": score,
            "score_components": score_components,
            "matching_skills": matching_skills,
            "missing_skills": sorted(required_skills - resume_skills),
            "required_skills_count": len(required_skills),
            "matched_required_skills_count": len(matched_required),
            "missing_preferred_skills": sorted(preferred_skills - resume_skills),
            "missing_certifications": missing_certifications,
            "relevant_resume_evidence": relevant_evidence,
            "learning_project_suggestions": [
                {
                    "skill": skill,
                    "suggestion": (
                        f"Build a portfolio project using {skill} for "
                        f"{record.get('job_title', 'this role')} and document "
                        "your approach and results."
                    ),
                }
                for skill in sorted(required_skills - resume_skills)[:3]
            ],
            "skills": sorted(job_skills),
            "candidate_roles": candidate_roles,
            "education_match": education_match,
            "experience_score": (
                round(experience_score * 100, 1)
                if experience_score is not None else None
            ),
            "experience_compatibility": compatibility,
            "why_match": "; ".join(reasons) or "Role details are available to review",
            "_ranking_key": (
                score,
                round(title_score, 4),
                round(required_coverage or 0.0, 4),
                round(float(similarities[index]), 4),
            ),
        })
    ranked.sort(key=lambda record: (
        tuple(-value for value in record["_ranking_key"]),
        record["job_title"].casefold(),
    ))
    return ranked[:top_n]


def explain_job_match(text, job, candidate_profile=None):
    candidate_profile = candidate_profile or {}
    bundle = get_bundle()
    record = _database_records([job])[0] if hasattr(job, "_meta") else job
    skills = candidate_profile.get("skills") or extract_resume_skills(text)
    education = candidate_profile.get("education") or extract_education(text)
    experience_years = candidate_profile.get("experience_years")
    if experience_years is None:
        experience_years = extract_experience(text)
    ranked = _rank_records(
        text,
        [record],
        skills,
        education,
        experience_years,
        bundle,
        1,
        candidate_profile,
    )
    return ranked[0] if ranked else None


def recommend_jobs_for_text(
    text, db_jobs=None, top_n=10, include_dataset=None, user=None,
    candidate_profile=None,
):
    bundle = get_bundle()
    candidate_profile = dict(candidate_profile or {})
    skills = candidate_profile.get("skills") or extract_resume_skills(text)
    education = candidate_profile.get("education") or extract_education(text)
    experience_years = candidate_profile.get("experience_years")
    if experience_years is None:
        experience_years = extract_experience(text)
    experience_years = float(experience_years or 0)
    profile = {
        **candidate_profile,
        "skills": sorted(set(extract_skills(", ".join(skills)))),
        "education": list(education),
        "experience_years": experience_years,
    }
    profile["experience_known"] = bool(
        profile.get("experience_known")
        or experience_years
        or re.search(
            r"\b(?:fresher|no professional experience)\b", text or "", re.I
        )
    )
    profile["suitable_roles"] = _candidate_roles(
        text, profile["skills"], profile
    )
    user_profile = None
    if user is not None and user.is_authenticated:
        from .models import UserProfile

        user_profile = UserProfile.objects.filter(user=user).first()
        if user_profile:
            profile["preferred_roles"] = (
                profile.get("preferred_roles") or user_profile.preferred_roles
            )
            profile["preferred_locations"] = (
                profile.get("preferred_locations")
                or user_profile.preferred_locations
            )
            profile["headline"] = profile.get("headline") or user_profile.headline
            for name in (
                "preferred_category",
                "preferred_employment_type",
                "preferred_work_mode",
                "preferred_salary_min",
                "preferred_salary_max",
            ):
                if not profile.get(name):
                    profile[name] = getattr(user_profile, name)
            profile["experience_known"] = bool(
                profile["experience_known"] or user_profile.experience_years
            )
            profile["experience_years"] = (
                profile["experience_years"]
                or float(user_profile.experience_years or 0)
            )
            profile["suitable_roles"] = _candidate_roles(
                text, profile["skills"], profile
            )
    from django.utils import timezone
    now = timezone.now()
    if include_dataset is None:
        include_dataset = db_jobs is None
    if db_jobs is None:
        from .models import Job
        from django.db.models import Q
        db_jobs = Job.objects.filter(is_active=True, status="open").filter(
            Q(deadline__isnull=True) | Q(deadline__gt=now)
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        ).select_related("recruiter")
    database_records = [
        record for record in _database_records(db_jobs)
        if _record_is_available(record, now)
    ]
    seen = set()
    unique_database_records = []
    for record in database_records:
        fingerprint = _job_fingerprint(record)
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique_database_records.append(record)
    database_ranked = _rank_records(
        text, unique_database_records, profile["skills"], education,
        experience_years, bundle, top_n, profile,
    )
    if include_dataset:
        dataset_records = [
            record for record in _dataset_records()
            if _record_is_available(record, now)
            and _job_fingerprint(record) not in seen
        ]
        dataset_ranked = _rank_records(
            text, dataset_records, profile["skills"], education,
            experience_years, bundle or {}, top_n, profile,
        )
        database_ranked.extend(dataset_ranked)
    if user is not None and user.is_authenticated:
        from .models import JobRecommendationFeedback
        preferred_roles = [
            role.strip()
            for role in re.split(
                r"[,;\n]", profile.get("preferred_roles", "") or ""
            )
            if role.strip()
        ]
        preferred_locations = [
            location.strip().casefold()
            for location in re.split(
                r"[,;\n]", profile.get("preferred_locations", "") or ""
            )
            if location.strip()
        ]
        feedback = {
            (item.source, item.source_key): item.is_relevant
            for item in JobRecommendationFeedback.objects.filter(user=user)
        }
        personalized = []
        for record in database_ranked:
            key = (record["source"], str(record["source_key"]))
            if feedback.get(key) is False:
                continue
            preferred_role = bool(
                preferred_roles
                and _role_relevance(
                    preferred_roles, record["job_title"], record.get("category", "")
                ) >= 0.6
            )
            location = str(record.get("location", "")).casefold()
            preferred_location = any(
                preference in location for preference in preferred_locations
            )
            score = (
                int(feedback.get(key) is True) * 2
                + int(preferred_role)
                + int(preferred_location)
            )
            if preferred_role:
                record["why_match"] += "; matches your preferred role"
            if preferred_location:
                record["why_match"] += "; matches your preferred location"
            if feedback.get(key) is True:
                record["why_match"] += "; you marked this role relevant"
            record["_personalization_score"] = score
            personalized.append(record)
        database_ranked = personalized
    database_ranked.sort(key=lambda record: (
        tuple(-value for value in record.get(
            "_ranking_key", (0.0, 0.0, 0.0, record["match_score"])
        )),
        -record.get("_personalization_score", 0),
        -record["match_score"],
        record["job_title"].casefold(),
    ))
    for record in database_ranked:
        record.pop("_ranking_key", None)
        record.pop("_personalization_score", None)
    return database_ranked[:top_n], profile
def recommend_candidates_for_job(job, top_n=None):
    from django.db.models import Q
    from .models import Resume
    b=get_bundle()
    active=Resume.objects.filter(
        is_active=True, user__profile__role="seeker"
    ).filter(
        Q(user__profile__discoverable=True)
        | Q(
            user__applications__job=job,
            user__applications__consent_to_share=True,
        )
    ).select_related("user","user__profile").distinct()
    rows=[]
    req=set(extract_skills(job.required_skills+" "+job.description))
    vec=b.get("tfidf_vectorizer") if b else None
    weights = _ranking_weights()
    job_text = " ".join((
        job.title, job.description, job.required_skills,
        job.education_requirements, job.qualifications,
    ))
    education_requirements = normalize(
        f"{job.education_requirements} {job.qualifications}"
    )
    job_requirements = {
        "required_experience_years": job.required_experience_years,
        "max_experience_years": job.max_experience_years,
        "experience_level": job.experience_level,
    }
    for resume in active:
        text=resume.extracted_text or ""
        cand_skills=set(resume.extracted_skills or extract_skills(text))
        inter=req&cand_skills
        precision=len(inter)/len(req) if req else 0
        recall=len(inter)/len(cand_skills) if cand_skills else 0
        f1=2*precision*recall/(precision+recall) if precision+recall else 0
        similarity = float(_similarity_scores(vec, job_text, [text])[0])
        experience_years = float(resume.experience_years or 0)
        required_years = _experience_requirement(job_requirements, b)
        maximum_years = _maximum_experience_requirement(job_requirements)
        experience_score = (
            min(1.0, experience_years / required_years)
            if required_years else 1.0
        )
        if maximum_years and experience_years > maximum_years:
            experience_score = min(experience_score, maximum_years / experience_years)
        candidate_education = resume.extracted_education or extract_education(text)
        education_match = bool(
            education_requirements
            and any(item.casefold() in education_requirements for item in candidate_education)
        )
        education_score = (
            float(education_match) if education_requirements.strip() else 1.0
        )
        score, score_components = _weighted_match_score({
            "text_similarity": similarity,
            "skill_f1": f1,
            "experience": experience_score,
            "education": education_score,
        }, weights)
        reasons = []
        if inter:
            reasons.append("Matches " + ", ".join(sorted(inter)[:4]))
        if education_match:
            reasons.append("Education matches job requirements")
        if required_years and experience_years >= required_years:
            reasons.append("Meets the experience requirement")
        if similarity > 0:
            reasons.append("Resume content is similar to the job description")
        compatibility = (
            f"Requires {required_years:g} years; resume shows {experience_years:g}"
            if required_years and experience_years < required_years
            else f"Role targets up to {maximum_years:g} years; resume shows {experience_years:g}"
            if maximum_years and experience_years > maximum_years
            else f"Fits the {required_years:g}-{maximum_years:g}-year experience range"
            if maximum_years
            else f"Meets the {required_years:g}-year requirement"
            if required_years
            else "No specific experience requirement"
        )
        rows.append({
            "resume": resume,
            "user": resume.user,
            "skills": sorted(cand_skills),
            "matching_skills": sorted(inter),
            "missing_skills": sorted(req-cand_skills),
            "education_match": education_match,
            "experience_compatibility": compatibility,
            "why_match": "; ".join(reasons) or "Role details are available to review",
            "score_components": score_components,
            "match_score": score,
        })
    ranked=sorted(rows,key=lambda x:x["match_score"],reverse=True)
    return ranked[:top_n] if top_n is not None else ranked
