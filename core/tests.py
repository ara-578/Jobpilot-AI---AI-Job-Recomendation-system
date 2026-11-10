import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from docx import Document
import pymupdf

from .models import (
    Application, ApplicationStatusHistory, Job, Resume, SavedJob, UserProfile,
    JobRecommendationFeedback, Notification, RecommendationRun,
)
from .forms import ResumeForm
from .ai_engine import read_resume


class ResumeTextExtractionTests(TestCase):
    def empty_pdf_path(self, name):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / name
        path.touch()
        return str(path)

    @patch("core.ai_engine.pymupdf.open")
    def test_scanned_pdf_page_is_read_with_ocr(self, open_pdf):
        page = MagicMock()
        page.get_text.side_effect = ["", "Experienced Python developer with Django and SQL experience."]
        page.get_textpage_ocr.return_value = object()
        open_pdf.return_value.__enter__.return_value = [page]

        text = read_resume(self.empty_pdf_path("scanned.pdf"))

        self.assertIn("Experienced Python developer", text)
        page.get_textpage_ocr.assert_called_once_with(
            language="eng", dpi=150, full=True
        )

    @patch("core.ai_engine.pymupdf.open")
    @patch("core.ai_engine.shutil.which", return_value=None)
    def test_scanned_pdf_finds_standard_windows_tesseract_install(
        self, which_tesseract, open_pdf
    ):
        page = MagicMock()
        page.get_text.side_effect = [
            "",
            "Experienced Python developer with Django and SQL experience.",
        ]
        page.get_textpage_ocr.return_value = object()
        open_pdf.return_value.__enter__.return_value = [page]

        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory) / "Tesseract-OCR"
            tessdata = install_dir / "tessdata"
            tessdata.mkdir(parents=True)
            (install_dir / "tesseract.exe").touch()
            (tessdata / "eng.traineddata").touch()
            with patch.dict(os.environ, {"ProgramFiles": directory, "PATH": ""}):
                text = read_resume(self.empty_pdf_path("scanned.pdf"))
                self.assertEqual(os.environ["PATH"], str(install_dir))

        self.assertIn("Experienced Python developer", text)
        page.get_textpage_ocr.assert_called_once_with(
            language="eng", dpi=150, full=True
        )
        which_tesseract.assert_called_once_with("tesseract")

    @patch("core.ai_engine.pymupdf.open")
    def test_scanned_pdf_retries_at_lower_dpi_after_ocr_memory_error(self, open_pdf):
        page = MagicMock()
        page.get_text.side_effect = [
            "",
            "Experienced Python developer with Django and SQL experience.",
        ]
        page.get_textpage_ocr.side_effect = [
            pymupdf.mupdf.FzErrorGeneric(
                "Director error: <class 'MemoryError'>"
            ),
            object(),
        ]
        open_pdf.return_value.__enter__.return_value = [page]

        text = read_resume(self.empty_pdf_path("large-scan.pdf"))

        self.assertIn("Experienced Python developer", text)
        self.assertEqual(
            page.get_textpage_ocr.call_args_list,
            [
                call(language="eng", dpi=150, full=True),
                call(language="eng", dpi=96, full=True),
            ],
        )

    @patch("core.ai_engine.pymupdf.open")
    def test_scanned_pdf_reports_memory_error_after_lower_dpi_retry(self, open_pdf):
        page = MagicMock()
        page.get_text.return_value = ""
        page.get_textpage_ocr.side_effect = pymupdf.mupdf.FzErrorGeneric(
            "Director error: <class 'MemoryError'>"
        )
        open_pdf.return_value.__enter__.return_value = [page]

        with self.assertRaisesMessage(ValueError, "smaller or lower-resolution PDF"):
            read_resume(self.empty_pdf_path("large-scan.pdf"))

        self.assertEqual(page.get_textpage_ocr.call_count, 2)

    @patch("core.ai_engine.pymupdf.open")
    def test_scanned_pdf_explains_when_tesseract_is_unavailable(self, open_pdf):
        page = MagicMock()
        page.get_text.return_value = ""
        page.get_textpage_ocr.side_effect = RuntimeError(
            "No OCR support: Tesseract not installed or not in PATH"
        )
        open_pdf.return_value.__enter__.return_value = [page]

        with self.assertRaisesMessage(ValueError, "Install Tesseract OCR"):
            read_resume(self.empty_pdf_path("scanned.pdf"))


class RecommendationEngineTests(TestCase):
    def test_model_status_can_check_availability_without_loading_bundle(self):
        from .ai_engine import model_status

        with (
            patch("core.ai_engine._bundle", None),
            patch("core.ai_engine._bundle_error", ""),
            patch("core.ai_engine._available_model_path", return_value=Path("model.pkl")),
            patch("core.ai_engine.get_bundle") as get_bundle_mock,
        ):
            status = model_status(load=False)

        self.assertTrue(status["ready"])
        get_bundle_mock.assert_not_called()

    def test_dataset_similarity_cache_preserves_original_tfidf_scores(self):
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer

        from .ai_engine import (
            _dataset_similarity_scores,
            _similarity_scores,
        )

        records = [
            {
                "source": "dataset",
                "dataset_index": 0,
                "job_title": "Python Engineer",
                "required_skills": "Python, Django",
                "description": "Build Python services.",
            },
            {
                "source": "dataset",
                "dataset_index": 1,
                "job_title": "Data Analyst",
                "required_skills": "SQL, Power BI",
                "description": "Create analytical dashboards.",
            },
        ]
        documents = [
            " ".join(str(record.get(key, "") or "") for key in (
                "job_title", "category", "industry", "required_skills",
                "preferred_skills", "education_requirements", "qualifications",
                "description", "responsibilities",
            ))
            for record in records
        ]
        vectorizer = TfidfVectorizer().fit(documents)
        with (
            patch("core.ai_engine._dataset_records_cache", records),
            patch("core.ai_engine._dataset_similarity_matrix", None),
            patch("core.ai_engine._dataset_similarity_matrix_key", None),
        ):
            cached = _dataset_similarity_scores(
                vectorizer, "Python developer", records
            )
            expected = _similarity_scores(
                vectorizer, "Python developer", documents
            )

        np.testing.assert_allclose(cached, expected)

    def test_database_similarity_cache_preserves_scores_and_reuses_matrix(self):
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer

        from .ai_engine import _database_similarity_scores, _similarity_scores

        documents = [
            "Python Engineer Django APIs and cloud systems",
            "Data Analyst SQL reporting and business intelligence",
        ]
        vectorizer = TfidfVectorizer().fit(documents)
        expected = _similarity_scores(
            vectorizer, "Python Django developer", documents
        )
        with (
            patch("core.ai_engine._database_similarity_matrix", None),
            patch("core.ai_engine._database_similarity_matrix_key", None),
            patch.object(vectorizer, "transform", wraps=vectorizer.transform) as transform,
        ):
            first = _database_similarity_scores(
                vectorizer, "Python Django developer", documents
            )
            second = _database_similarity_scores(
                vectorizer, "Python APIs engineer", documents
            )

        np.testing.assert_allclose(first, expected)
        self.assertEqual(len(second), len(documents))
        self.assertEqual(transform.call_count, 3)
    def test_extract_skills_matches_multiword_and_punctuated_aliases(self):
        from .ai_engine import extract_skills

        skills = extract_skills(
            "Built Machine Learning, REST API, Power BI, C++ and Node.js systems."
        )

        self.assertEqual(
            skills,
            ["c++", "machine learning", "node.js", "power bi", "rest api"],
        )

    def test_skill_aliases_normalize_resume_terms(self):
        from .ai_engine import extract_resume_skills, extract_skills

        self.assertEqual(
            extract_skills(
                "Python Programming, ML, NLP, AI and Django Development"
            ),
            [
                "artificial intelligence",
                "django",
                "machine learning",
                "natural language processing",
                "python",
            ],
        )
        self.assertEqual(
            extract_resume_skills(
                "Technical Skills\nPython\nProjects\nBuilt with Django.\n"
                "References\nJava developer at Example Corp."
            ),
            ["django", "python"],
        )

    def test_project_and_internship_years_do_not_become_professional_experience(self):
        from .ai_engine import extract_experience, extract_internship_experience

        resume = (
            "Internships\n2 years of experience with Python.\n"
            "Academic Projects\nA 5 years experience ML research project."
        )
        self.assertEqual(extract_experience(resume), 0.0)
        self.assertEqual(extract_internship_experience(resume), 2.0)
        self.assertEqual(
            extract_experience(
                "Work Experience\nSoftware Engineer 2020-2023\n"
                "Projects\nResearch project from 2010-2018."
            ),
            3.0,
        )

    def test_fresher_is_not_recommended_for_mandatory_senior_experience(self):
        from .ai_engine import _rank_records, extract_skills

        resume = "Fresher Python Developer\nSkills: Python, Django"
        records = [
            {
                "job_title": "Senior Python Developer",
                "required_skills": "Python, Django",
                "required_experience_years": 5,
                "experience_level": "Senior",
            },
            {
                "job_title": "Junior Python Developer",
                "required_skills": "Python, Django, SQL",
                "preferred_skills": "Docker",
                "required_experience_years": 0,
                "experience_level": "Entry level",
            },
            {
                "job_title": "Java Developer",
                "required_skills": "Java, Spring Boot",
                "required_experience_years": 0,
            },
        ]

        ranked = _rank_records(
            resume, records, extract_skills(resume), [], 0, {}, 10
        )

        self.assertEqual(
            [record["job_title"] for record in ranked],
            ["Junior Python Developer"],
        )
        self.assertEqual(ranked[0]["missing_skills"], ["sql"])
        self.assertEqual(ranked[0]["missing_preferred_skills"], ["docker"])
        self.assertEqual(
            set(ranked[0]["score_components"]),
            {
                "skills", "title_relevance", "experience", "education",
                "preferences", "required_skills", "preferred_skills",
                "semantic_text_similarity", "similarity_source",
            },
        )

    def test_unknown_experience_is_not_treated_as_zero_eligibility(self):
        from .ai_engine import _rank_records, extract_skills

        record = {
            "job_title": "Python Developer",
            "required_skills": "Python",
            "required_experience_years": 4,
        }
        ranked = _rank_records(
            "Python developer",
            [record],
            extract_skills("Python"),
            [],
            0,
            {},
            10,
            {"experience_known": False},
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["score_components"]["experience"], 50.0)
        self.assertIn("does not state", ranked[0]["experience_compatibility"])

    def test_candidate_preferences_affect_score_only_when_provided(self):
        from .ai_engine import _rank_records, extract_skills

        records = [
            {
                "job_title": "Python Developer",
                "required_skills": "Python, SQL",
                "category": "Engineering",
                "location": "Remote",
                "employment_type": "Full-time",
                "work_mode": "remote",
                "salary": "INR 10-15 LPA",
            },
            {
                "job_title": "Python Developer",
                "required_skills": "Python, Java",
                "category": "Operations",
                "location": "Mumbai",
                "employment_type": "Contract",
                "work_mode": "on_site",
                "salary": "INR 5 LPA",
            },
        ]
        candidate = {
            "preferred_roles": "Python Developer",
            "preferred_locations": "Remote",
            "preferred_category": "Engineering",
            "preferred_employment_type": "Full-time",
            "preferred_work_mode": "remote",
            "preferred_salary_min": 1000000,
            "preferred_salary_max": 1500000,
        }

        ranked = _rank_records(
            "Python Developer",
            records,
            extract_skills("Python"),
            [],
            0,
            {},
            10,
            candidate,
        )

        self.assertEqual(ranked[0]["location"], "Remote")
        self.assertEqual(ranked[0]["score_components"]["preferences"], 100.0)
        self.assertEqual(ranked[1]["score_components"]["preferences"], 16.7)

    def test_local_embedding_loader_never_resolves_a_remote_model_name(self):
        import sys
        import types
        import numpy as np

        from .ai_engine import _local_embedding_similarities

        model = MagicMock()
        model.encode.return_value = np.asarray([
            [1.0, 0.0],
            [0.6, 0.8],
        ])
        sentence_transformers = types.SimpleNamespace(
            SentenceTransformer=MagicMock(return_value=model)
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                override_settings(JOBPILOT_EMBEDDING_MODEL_PATH=directory),
                patch.dict(sys.modules, {
                    "sentence_transformers": sentence_transformers
                }),
                patch("core.ai_engine._embed_model", None),
                patch("core.ai_engine._embed_model_attempted", False),
                patch("core.ai_engine._embed_model_source", ""),
            ):
                similarities, source = _local_embedding_similarities(
                    "candidate projects", ["job requirements"]
                )

        sentence_transformers.SentenceTransformer.assert_called_once_with(
            directory, local_files_only=True
        )
        self.assertEqual(similarities.tolist(), [0.6])
        self.assertEqual(source, "local sentence embeddings")

    def test_explicitly_required_certification_is_an_eligibility_check(self):
        from .ai_engine import _rank_records, extract_skills

        record = {
            "job_title": "Python Developer",
            "required_skills": "Python",
            "qualifications": "AWS certification required.",
        }
        without_certificate = _rank_records(
            "Python Developer",
            [record],
            extract_skills("Python"),
            [],
            0,
            {},
            10,
        )
        with_certificate = _rank_records(
            "Python Developer",
            [record],
            extract_skills("Python"),
            [],
            0,
            {},
            10,
            {"certifications": ["AWS Certified Developer"]},
        )

        self.assertEqual(without_certificate, [])
        self.assertEqual(len(with_certificate), 1)

    def test_evaluation_report_keeps_validation_and_test_results_separate(self):
        import json
        from io import StringIO

        from django.core.management import call_command
        from core.management.commands.evaluate_recommendations import (
            _candidate_metrics,
        )

        metrics = _candidate_metrics(
            ["database:1", "database:2"],
            ["database:2", "database:3"],
        )
        self.assertEqual(metrics["precision_at_5"], 0.2)
        self.assertEqual(metrics["precision_at_10"], 0.1)
        self.assertEqual(metrics["recall_at_10"], 0.5)
        self.assertEqual(metrics["mean_reciprocal_rank"], 0.5)

        labels = {
            "candidates": [
                {
                    "candidate_id": "validation-1",
                    "split": "validation",
                    "resume_text": "Python Developer",
                    "relevant_job_keys": ["database:1"],
                },
                {
                    "candidate_id": "test-1",
                    "split": "test",
                    "resume_text": "Data Analyst",
                    "relevant_job_keys": ["database:1"],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.json"
            path.write_text(json.dumps(labels), encoding="utf-8")
            output = StringIO()
            with patch(
                "core.management.commands.evaluate_recommendations."
                "recommend_jobs_for_text",
                return_value=(
                    [{"source": "database", "source_key": "1"}],
                    {},
                ),
            ):
                call_command(
                    "evaluate_recommendations",
                    labels=str(path),
                    stdout=output,
                )

        report = json.loads(output.getvalue())
        self.assertEqual(report["validation_candidates"], 1)
        self.assertEqual(report["test_candidates"], 1)
        self.assertEqual(report["metrics"]["test"]["ndcg_at_10"], 1.0)

    def test_recommendation_exposes_resume_lines_supporting_skill_matches(self):
        from .ai_engine import _rank_records

        resume = (
            "Experience\nBuilt REST API services with Python and Django.\n"
            "Education\nBachelor of Science."
        )
        record = {
            "job_title": "Python Developer",
            "required_skills": "Python, Django, SQL",
            "preferred_skills": "REST API",
            "description": "Build Python services.",
            "required_experience_years": 0,
        }
        results = _rank_records(
            resume,
            [record],
            ["python", "django", "rest api"],
            ["bsc"],
            0,
            {},
            10,
        )

        self.assertEqual(
            results[0]["matching_skills"], ["django", "python", "rest api"]
        )
        self.assertEqual(results[0]["missing_skills"], ["sql"])
        self.assertEqual(results[0]["required_skills_count"], 3)
        self.assertEqual(results[0]["matched_required_skills_count"], 2)
        self.assertEqual(results[0]["score_components"]["required_skills"], 66.7)
        self.assertEqual(
            results[0]["relevant_resume_evidence"],
            [{
                "skill": "django",
                "text": "Built REST API services with Python and Django.",
            }, {
                "skill": "python",
                "text": "Built REST API services with Python and Django.",
            }, {
                "skill": "rest api",
                "text": "Built REST API services with Python and Django.",
            }],
        )
        self.assertEqual(
            results[0]["learning_project_suggestions"][0]["skill"], "sql"
        )

    @patch("core.ai_engine._rank_records", return_value=[{"match_score": 50}])
    @patch("core.ai_engine.get_bundle", return_value={})
    def test_job_match_uses_corrected_resume_skills(self, get_bundle, rank_records):
        from .ai_engine import explain_job_match

        profile = {
            "skills": ["python", "django"],
            "education": ["bsc"],
            "experience_years": 3,
        }

        result = explain_job_match(
            "Python developer", {"job_title": "Backend developer"}, profile
        )

        self.assertEqual(result["match_score"], 50)
        self.assertEqual(rank_records.call_args.args[2], ["python", "django"])
        self.assertEqual(rank_records.call_args.args[3], ["bsc"])
        self.assertEqual(rank_records.call_args.args[4], 3)

    def test_dataset_embedding_ranker_uses_each_record_dataset_index(self):
        import numpy as np
        import pandas as pd
        from scipy.sparse import csr_matrix
        from .ai_engine import _rank_dataset_records

        record = {
            "dataset_index": 1,
            "job_title": "Python and Django Developer",
            "company": "Example Systems",
            "description": "Build Django applications with Python.",
            "required_skills": "Python, Django",
            "preferred_skills": "",
            "required_experience_years": 2,
            "max_experience_years": 0,
            "education_requirements": "",
            "qualifications": "",
        }
        bundle = {
            "tfidf_vectorizer": object(),
            "job_matrix": csr_matrix((2, 1)),
            "job_skill_matrix": csr_matrix([[0, 1], [1, 1]]),
            "skill_labels": ["python", "django"],
            "skill_to_col": {"python": 0, "django": 1},
            "jobs": pd.DataFrame({"required_experience_years": [0, 2]}),
        }
        embedding_model = MagicMock()
        embedding_model.encode.side_effect = [
            np.asarray([[1.0, 0.0]]),
            np.asarray([[0.8, 0.6]]),
        ]

        with patch("core.ai_engine._embed_model", embedding_model):
            ranked = _rank_dataset_records(
                "Python Django developer",
                [record],
                ["python"],
                [],
                5,
                bundle,
                1,
            )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["matching_skills"], ["python"])
        self.assertEqual(ranked[0]["missing_skills"], ["django"])
        self.assertEqual(ranked[0]["score_components"]["text_similarity"], 80.0)

    def test_manual_role_scenarios_rank_job_specific_matches_first(self):
        from math import log2
        from sklearn.feature_extraction.text import TfidfVectorizer
        from .ai_engine import (
            _rank_records, extract_education, extract_experience, extract_skills,
        )

        records = [
            {
                "job_title": "Python Developer",
                "required_skills": "Python, Django, REST API",
                "preferred_skills": "AWS",
                "description": "Build Django REST APIs with Python.",
                "education_requirements": "BTech Computer Science",
                "required_experience_years": 2,
                "max_experience_years": 5,
                "skills": ["python", "django", "rest api", "aws"],
            },
            {
                "job_title": "Data Analyst",
                "required_skills": "SQL, Power BI, Excel",
                "preferred_skills": "Python",
                "description": "Analyze business data and create Power BI reports.",
                "education_requirements": "",
                "required_experience_years": 1,
                "max_experience_years": 5,
                "skills": ["sql", "power bi", "excel", "python"],
            },
            {
                "job_title": "Web Developer",
                "required_skills": "JavaScript, React, HTML, CSS",
                "preferred_skills": "REST API",
                "description": "Build responsive web applications and interfaces.",
                "education_requirements": "",
                "required_experience_years": 1,
                "max_experience_years": 5,
                "skills": ["javascript", "react", "html", "css", "rest api"],
            },
            {
                "job_title": "Data Scientist",
                "required_skills": "Python, SQL, Machine Learning, Statistics",
                "preferred_skills": "Pandas, scikit-learn",
                "description": "Build statistical models and machine-learning pipelines.",
                "education_requirements": "MSc Data Science",
                "required_experience_years": 2,
                "max_experience_years": 6,
                "skills": [
                    "python", "sql", "machine learning", "statistics",
                    "pandas", "scikit-learn",
                ],
            },
            {
                "job_title": "AI/ML Engineer",
                "required_skills": "Python, TensorFlow, PyTorch, Machine Learning",
                "preferred_skills": "NLP, Docker",
                "description": "Develop machine-learning systems and NLP models.",
                "education_requirements": "MTech Computer Science",
                "required_experience_years": 3,
                "max_experience_years": 8,
                "skills": [
                    "python", "tensorflow", "pytorch", "machine learning",
                    "nlp", "docker",
                ],
            },
            {
                "job_title": "Marketing Manager",
                "required_skills": "Marketing, Communication",
                "preferred_skills": "Excel",
                "description": "Manage marketing campaigns and customer communications.",
                "education_requirements": "MBA",
                "required_experience_years": 3,
                "max_experience_years": 10,
                "skills": ["communication", "excel"],
            },
        ]
        profiles = [
            (
                "Python Developer",
                "Python Developer. BTech Computer Science. Python, Django, REST API. "
                "3 years experience building web services.",
            ),
            (
                "Data Analyst",
                "Data Analyst. SQL, Power BI, Excel, Python. BSc Statistics. "
                "2 years analyzing data and dashboards.",
            ),
            (
                "Web Developer",
                "Web Developer. JavaScript, React, HTML, CSS. BTech Computer Science. "
                "2 years building websites.",
            ),
            (
                "Data Scientist",
                "Data Scientist. Python, SQL, pandas, scikit-learn, machine learning, "
                "statistics. MSc Data Science. 4 years experience.",
            ),
            (
                "AI/ML Engineer",
                "AI/ML Engineer. Python, TensorFlow, PyTorch, machine learning, NLP. "
                "MTech Computer Science. 4 years experience.",
            ),
        ]
        documents = [
            " ".join(str(record.get(key, "")) for key in (
                "job_title", "required_skills", "preferred_skills",
                "education_requirements", "description",
            ))
            for record in records
        ]
        vectorizer = TfidfVectorizer().fit(documents)
        hits_at_10 = 0
        reciprocal_gain = 0.0
        evaluated = 0
        for expected_title, resume_text in profiles:
            ranked = _rank_records(
                resume_text,
                records,
                extract_skills(resume_text),
                extract_education(resume_text),
                extract_experience(resume_text),
                {"tfidf_vectorizer": vectorizer},
                10,
            )
            self.assertTrue(ranked, expected_title)
            self.assertEqual(ranked[0]["job_title"], expected_title)
            self.assertIn("matching_skills", ranked[0])
            self.assertIn("missing_skills", ranked[0])
            self.assertTrue(ranked[0]["why_match"])
            relevant_ranks = [
                position
                for position, record in enumerate(ranked[:10], start=1)
                if record["job_title"] == expected_title
            ]
            hits_at_10 += bool(relevant_ranks)
            reciprocal_gain += (
                1 / log2(relevant_ranks[0] + 1) if relevant_ranks else 0
            )
            evaluated += 1

        precision_at_10 = hits_at_10 / (evaluated * 10)
        recall_at_10 = hits_at_10 / evaluated
        mean_ndcg_at_10 = reciprocal_gain / evaluated
        self.assertEqual(precision_at_10, 0.1)
        self.assertEqual(recall_at_10, 1.0)
        self.assertEqual(mean_ndcg_at_10, 1.0)


class ResumeStructuredParserTests(TestCase):
    def test_parser_normalizes_section_aliases_and_persists_candidate_info(self):
        from .models import ResumeParsingResult
        from .resume_parser import parse_resume, persist_parsing_result

        text = (
            "Jane Doe\njane@example.com\n\n"
            "TECHNICAL SKILLS:\nPython, Machine Learning, Power BI\n"
            "WORK EXPERIENCE:\n5 years of experience building analytics tools.\n"
            "EDUCATION:\nBTech Computer Science\n"
            "PERSONAL PROJECTS:\nBuilt a forecasting pipeline.\n"
            "LICENSES AND CERTIFICATIONS:\nAWS Certified Cloud Practitioner\n"
        )
        user = User.objects.create_user(username="parser_candidate")
        resume = Resume.objects.create(
            user=user,
            file=SimpleUploadedFile("resume.txt", text.encode("utf-8")),
        )
        self.addCleanup(resume.file.delete, save=False)

        parsed = parse_resume(resume)
        stored = persist_parsing_result(resume, parsed)

        self.assertEqual(
            [skill.name for skill in parsed.normalized_skills],
            ["machine learning", "power bi", "python"],
        )
        self.assertEqual(parsed.sections[1].name, "skills")
        self.assertEqual(parsed.sections[2].name, "experience")
        self.assertEqual(stored.candidate_info["name"], "Jane Doe")
        self.assertEqual(stored.candidate_info["email"], "jane@example.com")
        self.assertEqual(ResumeParsingResult.objects.count(), 1)


class SMTPSettingsTests(TestCase):
    def test_default_email_backend_never_writes_otp_to_the_terminal(self):
        from jobpilot import settings as project_settings

        self.assertEqual(
            project_settings.EMAIL_BACKEND,
            "django.core.mail.backends.smtp.EmailBackend",
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="smtp.test.example",
    EMAIL_HOST_USER="sender@example.com",
    EMAIL_HOST_PASSWORD="test-only-not-a-real-credential",
    DEFAULT_FROM_EMAIL="JobPilot <test@example.com>",
)
class EmailOTPTests(TestCase):
    def test_registration_requires_email_otp_before_account_activation(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "newseeker",
                "first_name": "New",
                "last_name": "Seeker",
                "email": "seeker@example.com",
                "role": "seeker",
                "password1": "SecurePass987!",
                "password2": "SecurePass987!",
            },
        )

        self.assertRedirects(response, reverse("verify_otp"))
        user = User.objects.get(username="newseeker")
        self.assertFalse(user.is_active)
        self.assertEqual(user.get_full_name(), "New Seeker")
        self.assertEqual(user.email, "seeker@example.com")
        self.assertEqual(mail.outbox[-1].to, ["seeker@example.com"])
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

        response = self.client.post(reverse("verify_otp"), {"otp": code})

        self.assertRedirects(response, reverse("dashboard"))
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertTrue(UserProfile.objects.filter(user=user, role="seeker").exists())

    def test_invalid_signup_does_not_create_a_partial_account(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "invalidsignup",
                "email": "not-an-email",
                "role": "seeker",
                "password1": "SecurePass987!",
                "password2": "DifferentPass987!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="invalidsignup").exists())
        self.assertContains(response, "Enter a valid email address")

    def test_sign_in_sends_and_requires_a_one_time_code(self):
        user = User.objects.create_user(
            username="returning",
            email="returning@example.com",
            password="SecurePass987!",
        )
        UserProfile.objects.create(user=user, role="seeker")

        response = self.client.post(
            reverse("login"),
            {"username": "returning", "password": "SecurePass987!"},
        )

        self.assertRedirects(response, reverse("verify_otp"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(mail.outbox[-1].to, ["returning@example.com"])
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

        response = self.client.post(reverse("verify_otp"), {"otp": code})

        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_incorrect_code_is_rejected(self):
        self.client.post(
            reverse("register"),
            {
                "username": "wrongcode",
                "email": "wrongcode@example.com",
                "role": "recruiter",
                "password1": "SecurePass987!",
                "password2": "SecurePass987!",
            },
        )

        response = self.client.post(reverse("verify_otp"), {"otp": "000000"})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(User.objects.get(username="wrongcode").is_active)

    @patch("core.auth_views.send_mail", return_value=0)
    def test_registration_reports_a_backend_that_did_not_deliver(self, send_mail_mock):
        response = self.client.post(
            reverse("register"),
            {
                "username": "mailfailure",
                "email": "mailfailure@example.com",
                "role": "seeker",
                "password1": "SecurePass987!",
                "password2": "SecurePass987!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="mailfailure").exists())
        self.assertContains(response, "couldn&#x27;t send the verification email")
        send_mail_mock.assert_called_once()

    @override_settings(
        EMAIL_HOST="smtp.gmail.com",
        EMAIL_HOST_PASSWORD="not-a-valid-app-password",
    )
    @patch("core.auth_views.send_mail")
    def test_registration_explains_invalid_gmail_app_password(self, send_mail_mock):
        response = self.client.post(
            reverse("register"),
            {
                "username": "invalidgmailapp",
                "email": "invalidgmailapp@example.com",
                "role": "seeker",
                "password1": "SecurePass987!",
                "password2": "SecurePass987!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="invalidgmailapp").exists())
        self.assertContains(response, "fresh 16-character Google App Password")
        send_mail_mock.assert_not_called()

    @patch("core.views.recommend_jobs_for_text", return_value=([], {}))
    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_dashboard_renders_resume_skill_distribution(self, recommend_jobs):
        user = User.objects.create_user(
            username="analyst",
            email="analyst@example.com",
            password="SecurePass987!",
        )
        UserProfile.objects.create(user=user, role="seeker")
        resume = Resume.objects.create(
            user=user,
            file="resumes/analyst.txt",
            extracted_text="Python Django SQL machine learning",
            extracted_skills=["python", "django", "sql", "machine learning"],
            ats_score=70,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Skills by discipline")
        self.assertContains(response, "Data &amp; AI")
        self.assertEqual(len(response.context["skill_chart"]), 4)
        recommend_jobs.assert_called_once()

    @patch("core.views.recommend_jobs_for_text")
    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_dashboard_reuses_recommendations_but_refreshes_application_state(
        self, recommend_jobs
    ):
        user = User.objects.create_user(username="cached_seeker")
        profile = UserProfile.objects.create(user=user, role="seeker")
        recruiter = User.objects.create_user(username="cached_recruiter")
        UserProfile.objects.create(user=recruiter, role="recruiter")
        resume_text = "Python developer with Django experience."
        resume = Resume.objects.create(
            user=user,
            file=SimpleUploadedFile(
                "cached-seeker.txt", resume_text.encode("utf-8")
            ),
            extracted_text=resume_text,
            extracted_skills=["python", "django"],
        )
        self.addCleanup(resume.file.delete, save=False)
        job = Job.objects.create(
            recruiter=recruiter,
            title="Python Developer",
            company="Cache Test Co",
            description="Build Django applications.",
            required_skills="Python, Django",
        )
        recommendation = {
            "source": "database",
            "source_key": str(job.pk),
            "job": job,
            "job_title": job.title,
        }
        recommend_jobs.return_value = ([recommendation], {})
        self.client.force_login(user)

        first = self.client.get(reverse("dashboard"))
        self.assertEqual(first.status_code, 200)
        run = resume.recommendation_run
        self.assertEqual(run.status, "ready", run.error_message)
        self.assertEqual(recommend_jobs.call_args.kwargs["top_n"], 50)
        self.assertFalse(first.context["recommendations"][0]["has_applied"])

        Application.objects.create(
            job=job, recruiter=recruiter, applicant=user
        )
        second = self.client.get(reverse("dashboard"))
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.context["recommendations"][0]["has_applied"])
        recommend_jobs.assert_called_once()

        profile.preferred_roles = "Python Developer"
        profile.save(update_fields=["preferred_roles"])
        self.client.get(reverse("dashboard"))
        self.assertEqual(recommend_jobs.call_count, 2)

    @patch("core.tasks.generate_recommendations.apply_async")
    def test_recommendation_poll_restarts_stale_processing_run(self, apply_async):
        from datetime import timedelta
        from django.utils import timezone

        user = User.objects.create_user(username="stale_recommendation_seeker")
        UserProfile.objects.create(user=user, role="seeker")
        resume_text = "Python developer with Django experience."
        resume = Resume.objects.create(
            user=user,
            file=SimpleUploadedFile(
                "stale-recommendation.txt", resume_text.encode("utf-8")
            ),
            extracted_text=resume_text,
            extracted_skills=["python", "django"],
        )
        self.addCleanup(resume.file.delete, save=False)
        run = RecommendationRun.objects.create(
            user=user,
            resume=resume,
            input_hash="stale-input",
            job_version="stale-jobs",
            status="processing",
            task_id="abandoned-task",
        )
        RecommendationRun.objects.filter(pk=run.pk).update(
            updated_at=timezone.now() - timedelta(minutes=6)
        )
        self.client.force_login(user)

        response = self.client.get(reverse("recommendation_updates"))

        self.assertEqual(response.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, "queued")
        self.assertNotEqual(run.task_id, "abandoned-task")
        apply_async.assert_called_once()
        self.client.get(reverse("recommendation_updates"))
        apply_async.assert_called_once()

    @patch("core.tasks.generate_recommendations.apply_async")
    def test_recommendation_poll_retries_legacy_broker_failure(self, apply_async):
        from core.views import LEGACY_RECOMMENDATION_BROKER_ERROR

        user = User.objects.create_user(username="legacy_recommendation_seeker")
        UserProfile.objects.create(user=user, role="seeker")
        resume_text = "Python developer with Django experience."
        resume = Resume.objects.create(
            user=user,
            file=SimpleUploadedFile(
                "legacy-recommendation.txt", resume_text.encode("utf-8")
            ),
            extracted_text=resume_text,
            extracted_skills=["python", "django"],
        )
        self.addCleanup(resume.file.delete, save=False)
        run = RecommendationRun.objects.create(
            user=user,
            resume=resume,
            input_hash="legacy-input",
            job_version="legacy-jobs",
            status="failed",
            error_message=LEGACY_RECOMMENDATION_BROKER_ERROR,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("recommendation_updates"))

        self.assertEqual(response.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, "queued")
        self.assertEqual(run.error_message, "")
        apply_async.assert_called_once()

    @patch("core.views.model_status")
    def test_home_does_not_load_the_recommendation_model(self, model_status):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        model_status.assert_not_called()

    def test_dashboard_and_resume_upload_do_not_eager_load_model_without_ai_work(
        self,
    ):
        user = User.objects.create_user(username="lazy_model_seeker")
        UserProfile.objects.create(user=user, role="seeker")
        self.client.force_login(user)

        with patch("core.ai_engine.get_bundle") as get_bundle_mock:
            dashboard = self.client.get(reverse("dashboard"))
            upload = self.client.get(reverse("upload_resume"))

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(upload.status_code, 200)
        get_bundle_mock.assert_not_called()

    @patch("core.tasks.generate_recommendations.apply_async")
    @patch("core.views.recommend_jobs_for_text")
    def test_dashboard_queues_recommendations_without_ranking_in_request(
        self, recommend_jobs, apply_async
    ):
        user = User.objects.create_user(username="queued_recommendations")
        UserProfile.objects.create(user=user, role="seeker")
        resume_text = "Python developer with Django experience."
        resume = Resume.objects.create(
            user=user,
            file=SimpleUploadedFile(
                "queued-resume.txt", resume_text.encode("utf-8")
            ),
            extracted_text=resume_text,
            extracted_skills=["python", "django"],
        )
        self.addCleanup(resume.file.delete, save=False)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["recommendation_status"], "queued")
        self.assertContains(response, "recommendation-skeletons")
        apply_async.assert_called_once()
        recommend_jobs.assert_not_called()

    @patch(
        "core.views.threading.Thread"
    )
    @patch(
        "core.tasks.generate_recommendations.apply_async",
        side_effect=OSError("Redis unavailable"),
    )
    @patch("core.views.recommend_jobs_for_text")
    def test_dashboard_generates_recommendations_when_broker_is_unavailable(
        self, recommend_jobs, apply_async, thread_class
    ):
        from django.core.cache import cache

        user = User.objects.create_user(username="local_worker_recommendations")
        UserProfile.objects.create(user=user, role="seeker")
        resume_text = "Python developer with Django experience."
        resume = Resume.objects.create(
            user=user,
            file=SimpleUploadedFile(
                "local-worker-resume.txt", resume_text.encode("utf-8")
            ),
            extracted_text=resume_text,
            extracted_skills=["python", "django"],
        )
        self.addCleanup(resume.file.delete, save=False)
        recruiter = User.objects.create_user(username="local_worker_recruiter")
        job = Job.objects.create(
            recruiter=recruiter,
            title="Python Developer",
            company="Local Worker Co",
            required_skills="Python, Django",
            description="Build Python applications.",
        )
        recommend_jobs.return_value = ([{
            "source": "database",
            "source_key": str(job.pk),
            "job": job,
            "job_title": job.title,
            "company": job.company,
            "location": job.location,
            "description": job.description,
            "required_skills": job.required_skills,
            "match_score": 90,
            "matching_skills": ["python", "django"],
            "missing_skills": [],
            "score_components": {},
        }], {})
        cache.clear()

        def run_local_worker_for_test():
            arguments = thread_class.call_args.kwargs
            arguments["target"](*arguments["args"])

        thread_class.return_value.start.side_effect = run_local_worker_for_test
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["recommendation_status"], "ready")
        self.assertEqual(
            response.context["recommendations"][0]["job"].pk, job.pk
        )
        self.assertContains(response, "Python Developer")
        self.assertNotContains(response, "temporarily unavailable")
        apply_async.assert_called_once()
        thread_class.assert_called_once()
        thread_class.return_value.start.assert_called_once()
        recommend_jobs.assert_called_once()

    @patch("core.views.search_database_jobs")
    def test_job_search_cache_reuses_results_but_reads_saved_jobs_live(
        self, search_database_jobs
    ):
        from django.core.cache import cache

        user = User.objects.create_user(username="cached_job_seeker")
        UserProfile.objects.create(user=user, role="seeker")
        recruiter = User.objects.create_user(username="cached_job_recruiter")
        job = Job.objects.create(
            recruiter=recruiter,
            title="Cached Python Developer",
            company="Cache Test Co",
            description="Build Python applications.",
            required_skills="Python",
        )
        search_database_jobs.return_value = Job.objects.filter(
            pk=job.pk
        ).order_by("-created_at", "pk")
        cache.clear()
        self.client.force_login(user)

        first = self.client.get(reverse("job_list"))
        self.assertEqual(first.status_code, 200)
        second = self.client.get(reverse("job_list"))
        self.assertEqual(second.status_code, 200)
        search_database_jobs.assert_called_once()

        SavedJob.objects.create(user=user, job=job)
        third = self.client.get(reverse("job_list"))
        self.assertIn(job.pk, third.context["saved_job_ids"])
        search_database_jobs.assert_called_once()

        Job.objects.filter(pk=job.pk).update(is_active=False)
        fourth = self.client.get(reverse("job_list"))
        self.assertEqual(list(fourth.context["jobs"]), [])
        search_database_jobs.assert_called_once()

        Job.objects.filter(pk=job.pk).update(is_active=True)
        job.company = "Updated Cache Test Co"
        job.save(update_fields=["company", "updated_at"])
        self.client.get(reverse("job_list"))
        self.assertEqual(search_database_jobs.call_count, 2)


class RecruiterWorkflowTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.recruiter = User.objects.create_user(
            username="teamlead",
            email="teamlead@example.com",
            password="SecurePass987!",
        )
        UserProfile.objects.create(user=self.recruiter, role="recruiter")
        self.job = Job.objects.create(
            recruiter=self.recruiter,
            title="Python Engineer",
            company="Northstar",
            required_skills="Python, Django",
            description="Build useful web products.",
        )
        self.client.force_login(self.recruiter)

    def test_recruiter_can_edit_and_close_owned_job(self):
        response = self.client.get(reverse("edit_job", args=[self.job.pk]))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("toggle_job_status", args=[self.job.pk]))

        self.assertRedirects(response, reverse("manage_jobs"))
        self.job.refresh_from_db()
        self.assertFalse(self.job.is_active)
        response = self.client.get(reverse("manage_jobs"))
        self.assertContains(response, "Closed")

    def test_recruiter_can_create_and_manage_an_owned_job(self):
        response = self.client.post(
            reverse("create_job"),
            {
                "title": "Product Data Analyst",
                "company": "Northstar",
                "description": "Analyze product usage.",
                "required_skills": "SQL, Python",
                "required_experience_years": "2",
                "location": "Remote",
                "experience_level": "Entry level",
                "employment_type": "Full-time",
                "work_mode": "remote",
            },
        )

        self.assertRedirects(response, reverse("manage_jobs"))
        job = Job.objects.get(title="Product Data Analyst")
        self.assertEqual(job.recruiter, self.recruiter)
        self.assertEqual(job.status, "open")
        self.assertContains(self.client.get(reverse("manage_jobs")), job.title)

    def test_recruiter_can_move_application_through_interview_to_selection(self):
        from .models import InterviewSchedule

        applicant = User.objects.create_user(
            username="pipeline_candidate", email="pipeline@example.com"
        )
        UserProfile.objects.create(user=applicant, role="seeker")
        application = Application.objects.create(
            job=self.job,
            applicant=applicant,
            applicant_name="Pipeline Candidate",
            applicant_email=applicant.email,
        )
        ApplicationStatusHistory.objects.create(
            application=application,
            status="submitted",
            changed_by=applicant,
            description="Application submitted.",
        )
        status_url = reverse("update_application_status", args=[application.pk])

        for status in ("reviewing", "shortlisted"):
            response = self.client.post(status_url, {"status": status})
            self.assertRedirects(response, reverse("recruiter_applications"))
            application.refresh_from_db()
            self.assertEqual(application.status, status)

        response = self.client.post(
            status_url,
            {
                "status": "interview_scheduled",
                "interview_at": "2030-01-15T10:00",
                "interview_mode": "phone",
                "interview_details": "Initial technical discussion",
            },
        )
        self.assertRedirects(response, reverse("recruiter_applications"))
        application.refresh_from_db()
        interview = InterviewSchedule.objects.get(application=application)
        self.assertEqual(application.status, "interview_scheduled")
        self.assertEqual(interview.mode, "phone")
        self.assertEqual(interview.details, "Initial technical discussion")
        self.assertTrue(
            Notification.objects.filter(
                recipient=applicant,
                application=application,
                message__contains="interview",
            ).exists()
        )

        response = self.client.post(status_url, {"status": "selected"})
        self.assertRedirects(response, reverse("recruiter_applications"))
        application.refresh_from_db()
        self.assertEqual(application.status, "selected")
        self.assertEqual(
            list(application.status_history.values_list("status", flat=True)),
            [
                "submitted", "reviewing", "shortlisted",
                "interview_scheduled", "selected",
            ],
        )
        self.assertContains(self.client.get(reverse("dashboard")), applicant.username)

    def test_application_review_uses_authorized_resume_route_and_status_form(self):
        applicant = User.objects.create_user(
            username="review_candidate", email="review@example.com",
            password="SecurePass987!",
        )
        UserProfile.objects.create(user=applicant, role="seeker")
        resume = Resume.objects.create(
            user=applicant, file="resumes/review.txt",
            extracted_text="Python Django",
        )
        Application.objects.create(job=self.job, applicant=applicant, resume=resume)

        response = self.client.get(reverse("recruiter_applications"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("application_resume_download", args=[Application.objects.get(resume=resume).pk]),
        )
        self.assertContains(response, "Update status")

    @patch(
        "core.views.recommend_candidates_for_job",
        return_value=[],
    )
    @patch("core.views.model_status", return_value={"ready": True, "message": ""})
    def test_candidate_search_and_match_score_filter(self, model_status_mock, recommend_mock):
        candidate = User.objects.create_user(
            username="pythoncandidate",
            email="python@example.com",
            password="SecurePass987!",
        )
        UserProfile.objects.create(
            user=candidate, role="seeker", skills="Python", discoverable=True
        )
        resume = Resume.objects.create(
            user=candidate,
            file="resumes/python.txt",
            extracted_text="Python Django",
            extracted_skills=["python", "django"],
        )
        recommend_mock.return_value = [
            {
                "user": candidate,
                "resume": resume,
                "skills": ["python", "django"],
                "matching_skills": ["python"],
                "missing_skills": [],
                "match_score": 80,
            }
        ]

        response = self.client.get(
            reverse("job_candidates", args=[self.job.pk]),
            {"q": "python", "min_score": "70"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pythoncandidate")
        recommend_mock.assert_called_once_with(self.job)
        model_status_mock.assert_called_once()

    @override_settings(JOBPILOT_RANKING_WEIGHTS={
        "text_similarity": 2,
        "skill_f1": 2,
        "experience": 1,
        "education": 1,
    })
    @patch("core.ai_engine.get_bundle", return_value={"tfidf_vectorizer": None})
    def test_candidate_ranking_uses_normalized_weights_and_explanations(self, bundle_mock):
        from core.ai_engine import recommend_candidates_for_job

        candidate = User.objects.create_user(
            username="qualifiedcandidate",
            email="qualified@example.com",
            password="test-only-password",
        )
        UserProfile.objects.create(
            user=candidate, role="seeker", discoverable=True
        )
        Resume.objects.create(
            user=candidate,
            file="resumes/qualified.txt",
            extracted_text="Python Django BTech software developer",
            extracted_skills=["python", "django"],
            extracted_education=["btech"],
            experience_years=4,
            is_active=True,
        )
        self.job.required_experience_years = 3
        self.job.max_experience_years = 6
        self.job.education_requirements = "BTech in Computer Science"
        self.job.save()

        candidates = recommend_candidates_for_job(self.job)

        self.assertEqual(len(candidates), 1)
        candidate_match = candidates[0]
        self.assertEqual(candidate_match["match_score"], 66.7)
        self.assertTrue(candidate_match["education_match"])
        self.assertIn("Education matches", candidate_match["why_match"])
        self.assertEqual(
            candidate_match["experience_compatibility"],
            "Fits the 3-6-year experience range",
        )
        self.assertEqual(candidate_match["score_components"]["experience"], 100.0)
        bundle_mock.assert_called_once()

        self.job.education_requirements = ""
        self.job.save(update_fields=["education_requirements"])
        candidate_without_education_requirement = recommend_candidates_for_job(self.job)[0]
        self.assertFalse(candidate_without_education_requirement["education_match"])
        self.assertEqual(
            candidate_without_education_requirement["score_components"]["education"],
            100.0,
        )


class SeekerApplyWorkflowTests(TestCase):
    def test_user_without_a_profile_cannot_apply_as_a_seeker(self):
        recruiter = User.objects.create_user(
            username="securedrecruiter",
            email="secured-hiring@example.com",
            password="SecurePass987!",
        )
        job = Job.objects.create(
            recruiter=recruiter,
            title="Secure role",
            company="Northstar",
            required_skills="Python",
            description="Build secure systems.",
        )
        user_without_profile = User.objects.create_user(
            username="noprofile",
            email="no-profile@example.com",
            password="SecurePass987!",
        )
        self.client.force_login(user_without_profile)

        response = self.client.get(reverse("apply_job", args=[job.pk]))

        self.assertEqual(response.status_code, 403)

    @patch("core.views.recommend_jobs_for_text")
    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_recommended_posted_job_has_real_apply_action(self, recommend_mock):
        recruiter = User.objects.create_user(
            username="realrecruiter",
            email="hiring@example.com",
            password="SecurePass987!",
        )
        UserProfile.objects.create(user=recruiter, role="recruiter")
        job = Job.objects.create(
            recruiter=recruiter,
            title="Backend Developer",
            company="Northstar",
            required_skills="Python, Django",
            description="Build web applications.",
        )
        seeker = User.objects.create_user(
            username="realapplicant",
            email="candidate@example.com",
            password="SecurePass987!",
        )
        UserProfile.objects.create(user=seeker, role="seeker")
        Resume.objects.create(
            user=seeker,
            file="resumes/candidate.txt",
            extracted_text="Python Django",
            extracted_skills=["python", "django"],
        )
        recommend_mock.return_value = (
            [
                {
                    "job": job,
                    "job_title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "salary": job.salary,
                    "experience_level": job.experience_level,
                    "match_score": 82,
                    "matching_skills": ["python", "django"],
                    "missing_skills": [],
                }
            ],
            {},
        )
        self.client.force_login(seeker)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("apply_job", args=[job.pk])}"')
        self.assertContains(response, "Apply now")

        response = self.client.get(reverse("job_list"))
        self.assertContains(response, "Apply now")
        response = self.client.get(reverse("apply_job", args=[job.pk]))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("apply_job", args=[job.pk]),
            {
                "resume": Resume.objects.get(user=seeker).pk,
                "applicant_name": "Real Applicant",
                "applicant_email": "candidate@example.com",
                "applicant_phone": "+1 555 0100",
                "applicant_location": "Remote",
                "cover_letter": "I have experience building Django applications.",
                "consent_to_share": "on",
            },
        )
        application = Application.objects.get(job=job, applicant=seeker)
        self.assertRedirects(
            response,
            reverse("application_confirmation", args=[application.reference]),
        )
        self.assertTrue(Application.objects.filter(job=job, applicant=seeker).exists())

        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Track application")
        self.assertNotContains(response, "Apply now")

    @patch("core.views.recommend_jobs_for_text", return_value=([], {}))
    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_application_submission_recruiter_update_and_candidate_tracker(self, recommend):
        recruiter = User.objects.create_user(username="tracking_recruiter")
        UserProfile.objects.create(user=recruiter, role="recruiter")
        seeker = User.objects.create_user(username="tracking_candidate")
        UserProfile.objects.create(user=seeker, role="seeker")
        job = Job.objects.create(
            recruiter=recruiter,
            title="Backend Engineer",
            company="Northstar",
            required_skills="Python",
            description="Build backend services.",
        )
        resume = Resume.objects.create(
            user=seeker,
            file=SimpleUploadedFile("tracking.txt", b"Python developer"),
            extracted_text="Python developer",
        )
        self.addCleanup(resume.file.delete, save=False)
        self.client.force_login(seeker)

        response = self.client.post(
            reverse("apply_job", args=[job.pk]),
            {
                "resume": resume.pk,
                "applicant_name": "Tracking Candidate",
                "applicant_email": "tracking@example.com",
                "applicant_phone": "+1 555 0100",
                "applicant_location": "Remote",
                "consent_to_share": "on",
            },
        )

        application = Application.objects.get(job=job, applicant=seeker)
        self.assertRedirects(
            response,
            reverse("application_confirmation", args=[application.reference]),
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=recruiter,
                application=application,
                title="New job application",
            ).exists()
        )
        self.assertEqual(
            list(application.status_history.values_list("status", flat=True)),
            ["submitted"],
        )

        self.client.force_login(recruiter)
        response = self.client.post(
            reverse("update_application_status", args=[application.pk]),
            {"status": "reviewing"},
        )
        self.assertRedirects(response, reverse("recruiter_applications"))
        application.refresh_from_db()
        self.assertEqual(application.status, "reviewing")
        self.assertTrue(
            Notification.objects.filter(
                recipient=seeker,
                application=application,
                title__startswith="Application update:",
            ).exists()
        )

        self.client.force_login(seeker)
        tracker = self.client.get(reverse("my_applications"))
        self.assertContains(tracker, "Under review")
        self.assertContains(tracker, "Status history")
        notifications = self.client.get(reverse("notifications"))
        self.assertContains(notifications, "now under review")
        self.assertFalse(
            Notification.objects.filter(
                recipient=seeker, read_at__isnull=False
            ).exists()
        )
        self.client.post(reverse("mark_notifications_read"))
        self.assertTrue(
            Notification.objects.filter(
                recipient=seeker, read_at__isnull=False
            ).exists()
        )
        recommend.assert_called_once()


class SeekerPrivacyAndApplicationTests(TestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(
            username="privacy_recruiter", email="privacy-recruiter@example.com",
            password="SecurePass987!",
        )
        UserProfile.objects.create(user=self.recruiter, role="recruiter")
        self.seeker = User.objects.create_user(
            username="privacy_seeker", email="privacy-seeker@example.com",
            password="SecurePass987!",
        )
        self.profile = UserProfile.objects.create(user=self.seeker, role="seeker")
        self.resume = Resume.objects.create(
            user=self.seeker,
            file=SimpleUploadedFile(
                "private.txt", b"Python Django SQL resume text",
                content_type="text/plain",
            ),
            extracted_text="Python Django SQL",
        )
        self.job = Job.objects.create(
            recruiter=self.recruiter, title="Developer", company="Northstar",
            required_skills="Python", description="Build software.",
        )

    def test_resume_download_is_owner_only_until_discovery_is_enabled(self):
        self.client.force_login(self.recruiter)
        url = reverse("resume_download", args=[self.resume.pk])
        self.assertEqual(self.client.get(url).status_code, 403)

        self.profile.discoverable = True
        self.profile.save(update_fields=["discoverable"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("attachment;"))

    def test_only_opening_the_application_resume_marks_it_viewed(self):
        application = Application.objects.create(
            job=self.job,
            recruiter=self.recruiter,
            applicant=self.seeker,
            resume=self.resume,
        )
        self.client.force_login(self.recruiter)

        listing = self.client.get(reverse("recruiter_applications"))
        application.refresh_from_db()
        self.assertEqual(application.status, "submitted")
        self.assertEqual(application.status_history.count(), 0)
        self.assertEqual(listing.status_code, 200)

        response = self.client.get(
            reverse("application_resume_download", args=[application.pk])
        )
        response.close()
        application.refresh_from_db()
        self.assertEqual(application.status, "resume_viewed")
        viewed_event = application.status_history.get(status="resume_viewed")
        self.assertEqual(viewed_event.previous_status, "submitted")
        self.assertIsNotNone(viewed_event.changed_at)

        second_response = self.client.get(
            reverse("application_resume_download", args=[application.pk])
        )
        second_response.close()
        self.assertEqual(
            application.status_history.filter(status="resume_viewed").count(), 1
        )

    def test_saving_a_job_is_unique_and_can_be_removed(self):
        self.client.force_login(self.seeker)
        url = reverse("toggle_saved_job", args=[self.job.pk])
        self.client.post(url, {"next": reverse("saved_jobs")})
        self.client.post(url, {"next": reverse("saved_jobs")})
        self.assertFalse(SavedJob.objects.filter(user=self.seeker, job=self.job).exists())
        response = self.client.post(url, {"next": "https://example.invalid"})
        self.assertRedirects(response, reverse("saved_jobs"))
        self.assertEqual(SavedJob.objects.filter(user=self.seeker, job=self.job).count(), 1)

    def test_saved_job_and_job_detail_pages_render(self):
        SavedJob.objects.create(user=self.seeker, job=self.job)
        self.client.force_login(self.seeker)

        saved_response = self.client.get(reverse("saved_jobs"))
        detail_response = self.client.get(reverse("job_detail", args=[self.job.pk]))

        self.assertEqual(saved_response.status_code, 200)
        self.assertContains(saved_response, self.job.title)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Job overview")
        self.assertContains(detail_response, "Required technical skills")
        self.assertContains(detail_response, "About the company")
        self.assertContains(detail_response, "data-copy-job-link")
        self.assertContains(detail_response, "default_company.svg")
        self.assertContains(detail_response, 'aria-label="Remove saved job"')

    def test_resume_upload_rejects_a_non_pdf_file_named_pdf(self):
        form = ResumeForm(
            {},
            {
                "file": SimpleUploadedFile(
                    "fake.pdf", b"not a PDF", content_type="application/pdf"
                )
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("valid PDF", str(form.errors))

    @patch("core.views.predict_category", return_value=("ENGINEERING", 0.91))
    @patch("core.tasks.generate_recommendations.apply_async")
    def test_pdf_and_docx_uploads_store_analysis_and_each_ats_score(self, queue, predict):
        self.client.force_login(self.seeker)
        resume_text = (
            "Python Django SQL developer. Bachelor of Computer Science. "
            "5 years of experience building projects.\nProjects\nBuilt a hiring API\n"
            "Certifications\nAWS Certified Solutions Architect"
        )
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((72, 72), resume_text)
        pdf_bytes = pdf.tobytes()
        pdf.close()
        response = self.client.post(
            reverse("upload_resume"),
            {"file": SimpleUploadedFile("resume.pdf", pdf_bytes, content_type="application/pdf")},
        )
        self.assertRedirects(
            response, reverse("resume_analysis", args=[Resume.objects.get(
                user=self.seeker, is_active=True
            ).pk])
        )
        pdf_resume = Resume.objects.get(user=self.seeker, is_active=True)
        self.addCleanup(pdf_resume.file.delete, save=False)
        self.assertGreater(pdf_resume.ats_score, 0)
        analysis = self.client.get(
            reverse("resume_analysis", args=[pdf_resume.pk])
        )
        self.assertNotContains(analysis, "ATS readiness score")
        self.assertNotContains(analysis, f"{pdf_resume.ats_score}/100")

        document = Document()
        document.add_paragraph(
            "Python Django SQL developer. Bachelor of Computer Science. "
            "5 years of experience."
        )
        document.add_paragraph("Projects")
        document.add_paragraph("Built a hiring API")
        document.add_paragraph("Certifications")
        document.add_paragraph("AWS Certified Solutions Architect")
        docx_bytes = BytesIO()
        document.save(docx_bytes)
        response = self.client.post(
            reverse("upload_resume"),
            {
                "file": SimpleUploadedFile(
                    "resume.docx", docx_bytes.getvalue(),
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        self.assertRedirects(
            response, reverse("resume_analysis", args=[Resume.objects.get(
                user=self.seeker, is_active=True
            ).pk])
        )
        docx_resume = Resume.objects.get(user=self.seeker, is_active=True)
        self.addCleanup(docx_resume.file.delete, save=False)
        self.assertGreater(docx_resume.ats_score, 0)
        self.assertEqual(docx_resume.predicted_category, "ENGINEERING")
        self.assertEqual(docx_resume.category_confidence, 0.91)
        self.assertTrue(docx_resume.extracted_projects)
        self.assertTrue(docx_resume.extracted_certifications)
        self.assertEqual(Resume.objects.filter(user=self.seeker).count(), 3)
        self.assertEqual(Resume.objects.filter(user=self.seeker, ats_score__gt=0).count(), 2)
        self.assertEqual(predict.call_count, 2)

    @patch("core.views.predict_category", return_value=("ENGINEERING", 0.92))
    def test_resume_upload_saves_extracted_details_and_prediction(self, predict):
        self.client.force_login(self.seeker)
        text = (
            "Python Django SQL developer. BTech in computer science. "
            "5 years professional experience.\nInternships\n"
            "2 years experience in Python development."
        )

        response = self.client.post(
            reverse("upload_resume"),
            {
                "file": SimpleUploadedFile(
                    "resume.txt", text.encode("utf-8"), content_type="text/plain"
                )
            },
        )

        resume = Resume.objects.get(user=self.seeker, is_active=True)
        self.assertRedirects(
            response, reverse("resume_analysis", args=[resume.pk])
        )
        self.addCleanup(resume.file.delete, save=False)
        self.assertIn("Python", resume.extracted_text)
        self.assertIn("python", resume.extracted_skills)
        self.assertIn("btech", resume.extracted_education)
        self.assertEqual(resume.experience_years, 5)
        self.assertEqual(resume.internship_experience_years, 2)
        self.assertEqual(resume.predicted_category, "ENGINEERING")
        self.assertEqual(resume.category_confidence, 0.92)
        predict.assert_called_once_with(text)

    @patch("core.views.logger.exception")
    @patch("core.views.read_resume", side_effect=RuntimeError("invalid document"))
    def test_unreadable_resume_is_reported_and_removed(self, read_resume_mock, log_error):
        self.client.force_login(self.seeker)
        response = self.client.post(
            reverse("upload_resume"),
            {
                "file": SimpleUploadedFile(
                    "broken.pdf", b"%PDF-1.4 invalid",
                    content_type="application/pdf",
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Could not analyze this resume")
        self.assertEqual(Resume.objects.filter(user=self.seeker).count(), 1)
        self.assertTrue(Resume.objects.get(user=self.seeker).is_active)
        read_resume_mock.assert_called_once()
        log_error.assert_called_once()

    def test_recruiter_can_only_apply_status_transitions_and_history_is_recorded(self):
        application = Application.objects.create(
            job=self.job, applicant=self.seeker, resume=self.resume
        )
        self.client.force_login(self.recruiter)
        url = reverse("update_application_status", args=[application.pk])

        response = self.client.post(url, {"status": "reviewing"})
        self.assertRedirects(response, reverse("recruiter_applications"))
        application.refresh_from_db()
        self.assertEqual(application.status, "reviewing")
        self.assertEqual(
            list(application.status_history.values_list("status", flat=True)),
            ["reviewing"],
        )

        self.client.post(url, {"status": "selected"})
        application.refresh_from_db()
        self.assertEqual(application.status, "reviewing")
        self.assertEqual(ApplicationStatusHistory.objects.count(), 1)

    def test_candidate_remarks_are_audited_but_private_notes_are_not_shared(self):
        application = Application.objects.create(
            job=self.job,
            applicant=self.seeker,
            resume=self.resume,
        )
        self.client.force_login(self.recruiter)

        self.client.post(
            reverse("update_application_status", args=[application.pk]),
            {
                "status": "reviewing",
                "candidate_remarks": "We will contact you next week.",
                "recruiter_notes": "Internal: discuss compensation with hiring panel.",
            },
        )

        event = ApplicationStatusHistory.objects.get(application=application)
        self.assertEqual(event.previous_status, "submitted")
        self.assertEqual(event.recruiter_remarks, "We will contact you next week.")
        self.client.force_login(self.seeker)
        tracker = self.client.get(reverse("my_applications"))
        self.assertContains(tracker, "We will contact you next week.")
        self.assertNotContains(tracker, "Internal: discuss compensation")

    def test_recruiter_cannot_download_resume_of_an_unrelated_candidate(self):
        other_recruiter = User.objects.create_user(
            username="other_recruiter", email="other@example.com",
            password="SecurePass987!",
        )
        UserProfile.objects.create(user=other_recruiter, role="recruiter")
        self.client.force_login(other_recruiter)

        response = self.client.get(reverse("resume_download", args=[self.resume.pk]))

        self.assertEqual(response.status_code, 403)

    def test_recruiter_can_still_view_the_resume_attached_to_an_application(self):
        application = Application.objects.create(
            job=self.job, applicant=self.seeker, resume=self.resume
        )
        self.resume.is_active = False
        self.resume.save(update_fields=["is_active"])
        self.client.force_login(self.recruiter)

        response = self.client.get(reverse("resume_download", args=[self.resume.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(application.resume_id, self.resume.pk)


class ApplicationTimelineTests(TestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(username="timeline_recruiter")
        self.applicant = User.objects.create_user(username="timeline_applicant")
        UserProfile.objects.create(user=self.recruiter, role="recruiter")
        UserProfile.objects.create(user=self.applicant, role="seeker")
        self.job = Job.objects.create(
            recruiter=self.recruiter,
            title="Timeline Engineer",
            company="Timeline Systems",
            required_skills="Python",
            description="Build software.",
        )
        self.application = Application.objects.create(
            job=self.job,
            recruiter=self.recruiter,
            applicant=self.applicant,
            status="rejected",
        )
        ApplicationStatusHistory.objects.create(
            application=self.application,
            status="submitted",
            changed_by=self.applicant,
            description="Application submitted.",
        )
        ApplicationStatusHistory.objects.create(
            application=self.application,
            status="rejected",
            previous_status="submitted",
            changed_by=self.recruiter,
            description="Application declined.",
        )

    def test_terminal_timeline_only_marks_recorded_stages_complete_with_dates(self):
        self.client.force_login(self.applicant)
        response = self.client.get(reverse("my_applications"))
        steps = response.context["applications"][0].timeline_steps
        by_status = {step["status"]: step for step in steps}

        self.assertTrue(by_status["submitted"]["reached"])
        self.assertTrue(by_status["submitted"]["changed_at"])
        self.assertFalse(by_status["resume_viewed"]["reached"])
        self.assertFalse(by_status["reviewing"]["reached"])
        self.assertTrue(by_status["rejected"]["reached"])
        self.assertTrue(by_status["rejected"]["current"])
        self.assertTrue(by_status["rejected"]["changed_at"])
        self.assertFalse(by_status["selected"]["reached"])
        self.assertEqual(response.context["applications"][0].progress_percent, 17)

    def test_candidate_application_tracker_is_paginated(self):
        for index in range(20):
            job = Job.objects.create(
                recruiter=self.recruiter,
                title=f"Timeline Engineer {index}",
                company="Timeline Systems",
                required_skills="Python",
                description="Build software.",
            )
            Application.objects.create(
                job=job,
                recruiter=self.recruiter,
                applicant=self.applicant,
            )

        self.client.force_login(self.applicant)
        response = self.client.get(reverse("my_applications"), {"page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["applications"]), 1)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertContains(response, "Page 2 of 2")

    def test_recruiter_application_list_is_paginated(self):
        for index in range(20):
            applicant = User.objects.create_user(
                username=f"timeline_applicant_{index}"
            )
            Application.objects.create(
                job=self.job,
                recruiter=self.recruiter,
                applicant=applicant,
                status="rejected",
            )

        self.client.force_login(self.recruiter)
        response = self.client.get(
            reverse("recruiter_applications"),
            {"status": "rejected", "page": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["applications"]), 1)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertContains(response, "Page 2 of 2")
        self.assertContains(response, "status=rejected")

    def test_history_keeps_prior_status_description_and_recruiter_remarks(self):
        declined = self.application.status_history.get(status="rejected")

        self.assertEqual(declined.previous_status, "submitted")
        self.assertEqual(declined.description, "Application declined.")
        self.assertEqual(declined.recruiter_remarks, "")


class ApplicationAdminWorkflowTests(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from .admin import ApplicationAdmin

        self.admin_user = User.objects.create_superuser(
            username="application_admin",
            email="application_admin@example.com",
            password="StrongAdminPass123!",
        )
        self.recruiter = User.objects.create_user(username="adminflow_recruiter")
        self.applicant = User.objects.create_user(username="adminflow_applicant")
        self.job = Job.objects.create(
            recruiter=self.recruiter,
            title="Admin Workflow Engineer",
            company="Admin Workflow Co",
            required_skills="Python",
            description="Build software.",
        )
        self.application = Application.objects.create(
            job=self.job,
            recruiter=self.recruiter,
            applicant=self.applicant,
        )
        self.request = RequestFactory().post("/admin/core/application/")
        self.request.user = self.admin_user
        self.model_admin = ApplicationAdmin(Application, AdminSite())

    def test_admin_status_change_records_transition_and_notifies_candidate(self):
        from .models import Notification

        self.application.status = "reviewing"

        self.model_admin.save_model(
            self.request, self.application, form=None, change=True
        )

        event = ApplicationStatusHistory.objects.get(application=self.application)
        self.assertEqual(event.previous_status, "submitted")
        self.assertEqual(event.status, "reviewing")
        self.assertEqual(event.changed_by, self.admin_user)
        self.assertIn("Administrator changed status", event.description)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.applicant, application=self.application
            ).exists()
        )

    def test_admin_cannot_bypass_application_transition_rules(self):
        from django.core.exceptions import ValidationError

        self.application.status = "selected"

        with self.assertRaises(ValidationError):
            self.model_admin.save_model(
                self.request, self.application, form=None, change=True
            )

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "submitted")
        self.assertFalse(
            ApplicationStatusHistory.objects.filter(
                application=self.application
            ).exists()
        )

    def test_application_history_admin_prevents_mutating_audit_records(self):
        from django.contrib import admin

        model_admin = admin.site._registry[ApplicationStatusHistory]

        self.assertFalse(model_admin.has_add_permission(self.request))
        self.assertTrue(model_admin.has_change_permission(self.request))
        self.assertFalse(model_admin.has_delete_permission(self.request))
        self.assertTrue({
            "application", "previous_status", "status", "changed_by",
            "changed_at", "description", "recruiter_remarks",
        }.issubset(set(model_admin.get_readonly_fields(self.request))))


class UnifiedJobsAndAdminTests(TestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(
            username="unified_recruiter", email="unified-recruiter@example.com",
            password="TestOnlyPass987!",
        )
        UserProfile.objects.create(user=self.recruiter, role="recruiter")
        self.seeker = User.objects.create_user(
            username="unified_seeker", email="unified-seeker@example.com",
            password="TestOnlyPass987!",
        )
        UserProfile.objects.create(user=self.seeker, role="seeker")
        self.resume = Resume.objects.create(
            user=self.seeker,
            file=SimpleUploadedFile(
                "unified.txt",
                b"Python Django SQL developer with 5 years experience.",
                content_type="text/plain",
            ),
            extracted_text="Python Django SQL developer with 5 years experience.",
            extracted_skills=["python", "django", "sql"],
            experience_years=5,
        )
        self.addCleanup(self.resume.file.delete, save=False)
        self.database_job = Job.objects.create(
            recruiter=self.recruiter,
            title="Python Django Engineer",
            company="Northstar",
            location="Remote",
            category="Engineering",
            required_skills="Python, Django",
            description="Build Python Django services.",
            required_experience_years=2,
            employment_type="Full-time",
        )

    def test_combined_search_filters_both_sources_and_prefers_database_duplicates(self):
        from core.ai_engine import search_jobs

        duplicate = {
            "job": None,
            "source": "dataset",
            "source_label": "Dataset",
            "source_key": "90",
            "job_title": self.database_job.title,
            "company": self.database_job.company,
            "location": self.database_job.location,
            "required_skills": self.database_job.required_skills,
            "preferred_skills": "",
            "description": self.database_job.description,
            "category": "Engineering",
            "industry": "",
            "experience_level": "Mid Level",
            "required_experience_years": 2,
            "education_requirements": "",
            "qualifications": "",
            "employment_type": "Full-time",
            "work_mode": "remote",
            "salary": "",
            "responsibilities": "",
            "application_url": "",
            "deadline": "",
            "created_at": None,
            "skills": ["python", "django"],
        }
        dataset_only = {
            **duplicate,
            "source_key": "91",
            "job_title": "Dataset Python Analyst",
            "company": "Data Works",
            "location": "Remote",
            "required_skills": "Python, SQL",
            "description": "Analyze Python data.",
            "skills": ["python", "sql"],
        }
        with patch("core.ai_engine._dataset_records_cache", [duplicate, dataset_only]):
            results = search_jobs({
                "skills": "python",
                "category": "engineering",
                "location": "remote",
                "experience": "5",
                "type": "full-time",
            })
            filtered = search_jobs({"title": "dataset python", "experience": "2"})

        self.assertEqual([record["source"] for record in results], ["database", "dataset"])
        self.assertEqual(
            [record["job_title"] for record in results],
            [self.database_job.title, "Dataset Python Analyst"],
        )
        self.assertEqual([record["source"] for record in filtered], ["dataset"])

    def test_unfiltered_search_does_not_extract_skills_for_every_job(self):
        from core.ai_engine import search_jobs

        with (
            patch("core.ai_engine._dataset_records", return_value=[]),
            patch(
                "core.ai_engine.extract_skills",
                side_effect=AssertionError("Unfiltered search must not parse skills"),
            ),
        ):
            results = search_jobs({})

        self.assertIn(self.database_job.pk, {
            record["job"].pk for record in results if record["job"]
        })

    def test_salary_filters_understand_lpa_and_intersect_with_other_filters(self):
        from core.ai_engine import search_jobs

        self.database_job.salary = "INR 10-15 LPA"
        self.database_job.save(update_fields=["salary"])
        with patch("core.ai_engine._dataset_records", return_value=[]):
            eligible = search_jobs({
                "title": "Python Django Engineer",
                "min_salary": "1200000",
                "max_salary": "1600000",
            })
            below_range = search_jobs({"min_salary": "1600000"})

        self.assertIn(self.database_job.pk, {
            record["job"].pk for record in eligible if record["job"]
        })
        self.assertNotIn(
            self.database_job.pk,
            {record["job"].pk for record in below_range if record["job"]},
        )

    def test_python_developer_search_prioritizes_title_and_related_roles(self):
        from core.ai_engine import search_jobs

        dataset_records = [
            {
                "job": None,
                "source": "dataset",
                "source_label": "Dataset",
                "source_key": "python-developer",
                "job_title": "Python Developer",
                "company": "Dataset Systems",
                "location": "Remote",
                "required_skills": "Python, Django",
                "preferred_skills": "",
                "description": "Build Python applications.",
                "category": "Engineering",
                "created_at": None,
            },
            {
                "job": None,
                "source": "dataset",
                "source_label": "Dataset",
                "source_key": "data-analyst",
                "job_title": "Data Analyst",
                "company": "Analytics Co",
                "location": "Remote",
                "required_skills": "SQL, Power BI",
                "preferred_skills": "",
                "description": "Analyze Python scripts and business reports.",
                "category": "Analytics",
                "created_at": None,
            },
        ]
        with patch("core.ai_engine._dataset_records", return_value=dataset_records):
            results = search_jobs({"q": "Python Developer"})

        self.assertGreaterEqual(len(results), 2)
        self.assertEqual(results[0]["job_title"], "Python Developer")
        self.assertNotIn("Data Analyst", [record["job_title"] for record in results])

    def test_search_recognizes_title_synonyms_and_experience_levels(self):
        from core.ai_engine import search_jobs

        ai_engine_record = {
            "job": None,
            "source": "dataset",
            "source_label": "Dataset",
            "source_key": "ai-engineer",
            "job_title": "AI Engineer",
            "company": "Applied AI",
            "location": "Remote",
            "category": "Artificial Intelligence",
            "experience_level": "Mid Level",
            "required_skills": "Python, TensorFlow",
            "preferred_skills": "",
            "description": "Develop machine learning systems.",
            "created_at": None,
        }
        data_analyst_record = {
            **ai_engine_record,
            "source_key": "data-analyst",
            "job_title": "Data Analyst",
            "company": "Analytics Group",
            "category": "Analytics",
            "experience_level": "Senior",
            "required_skills": "SQL, Power BI",
            "description": "Create analytical reports.",
        }
        with (
            patch(
                "core.ai_engine._dataset_records",
                return_value=[ai_engine_record, data_analyst_record],
            ),
            patch(
                "core.ai_engine._TITLE_SYNONYM_LOOKUP",
                {"machine learning engineer": {
                    "machine learning engineer", "ai engineer"
                }},
            ),
        ):
            synonym_results = search_jobs({"q": "Machine Learning Engineer"})
            mid_results = search_jobs({"experience_level": "mid"})

        self.assertEqual(
            [record["job_title"] for record in synonym_results], ["AI Engineer"]
        )
        self.assertIn("AI Engineer", [record["job_title"] for record in mid_results])
        self.assertNotIn("Data Analyst", [record["job_title"] for record in mid_results])

    def test_named_role_searches_prioritize_each_exact_title(self):
        from core.ai_engine import search_jobs

        titles = [
            "Python Developer",
            "Data Analyst",
            "Web Developer",
            "Data Scientist",
            "AI/ML Engineer",
        ]
        records = [
            {
                "job": None,
                "source": "dataset",
                "source_label": "Dataset",
                "source_key": str(index),
                "job_title": title,
                "company": f"Scenario Company {index}",
                "location": "Remote",
                "category": "Role Scenario Test",
                "required_skills": "Python, SQL",
                "description": (
                    "Work on " + ", ".join(other for other in titles if other != title)
                ),
                "created_at": None,
            }
            for index, title in enumerate(titles)
        ]
        with (
            patch("core.ai_engine._dataset_records", return_value=records),
            patch("core.ai_engine._TITLE_SYNONYM_LOOKUP", {}),
        ):
            for title in titles:
                with self.subTest(title=title):
                    results = search_jobs({
                        "q": title,
                        "category": "Role Scenario Test",
                    })
                    self.assertEqual(results[0]["job_title"], title)

    def test_search_excludes_expired_and_closed_dataset_jobs_and_deduplicates(self):
        from core.ai_engine import search_jobs

        base = {
            "job": None,
            "source": "dataset",
            "source_label": "Dataset",
            "job_title": "Python Developer",
            "company": "Fresh Systems",
            "location": "Remote",
            "category": "Expiry Filter Test",
            "required_skills": "Python",
            "description": "Build applications with Python.",
            "created_at": None,
        }
        records = [
            {**base, "source_key": "open-one"},
            {
                **base,
                "source_key": "open-duplicate",
                "description": "A duplicate with slightly different source text.",
            },
            {
                **base,
                "source_key": "expired",
                "company": "Expired Systems",
                "deadline": "2020-01-01",
            },
            {
                **base,
                "source_key": "closed",
                "company": "Closed Systems",
                "status": "closed",
            },
        ]
        with (
            patch("core.ai_engine._dataset_records", return_value=records),
            patch("core.ai_engine._TITLE_SYNONYM_LOOKUP", {}),
        ):
            results = search_jobs({
                "title": "Python Developer",
                "category": "Expiry Filter Test",
            })

        self.assertEqual(
            [(record["source_key"], record["company"]) for record in results],
            [("open-one", "Fresh Systems")],
        )

    def test_unrelated_resume_returns_no_recommendations(self):
        from core.ai_engine import recommend_jobs_for_text

        with (
            patch("core.ai_engine.get_bundle", return_value=None),
            patch("core.ai_engine._dataset_records", return_value=[]),
        ):
            recommendations, _ = recommend_jobs_for_text(
                "Classical pianist and music teacher with experience in orchestral performance.",
                db_jobs=[self.database_job],
            )

        self.assertEqual(recommendations, [])

    def test_imported_posting_is_immediately_searchable_and_deduplicated(self):
        from core.ai_engine import search_jobs

        with tempfile.TemporaryDirectory() as directory:
            dataset_path = Path(directory) / "postings.csv"
            dataset_path.write_text(
                "job_id,title,company_name,location,description,skills_desc,"
                "formatted_work_type,formatted_experience_level,application_url,"
                "remote_allowed,normalized_salary\n"
                "test-1,Data Analyst,Analytics Works,Remote,"
                "\"Create dashboards with SQL and Power BI.\","
                "\"SQL, Power BI, Excel\",Full-time,Entry level,"
                "https://example.com/apply,true,75000\n",
                encoding="utf-8",
            )
            call_command("import_jobs", str(dataset_path))
            call_command("import_jobs", str(dataset_path))

        imported = Job.objects.get(
            title="Data Analyst", company="Analytics Works", location="Remote"
        )
        self.assertEqual(imported.required_skills, "SQL, Power BI, Excel")
        self.assertEqual(imported.work_mode, "remote")
        with patch("core.ai_engine._dataset_records", return_value=[]):
            results = search_jobs({"q": "Data Analyst"})
        self.assertEqual(results[0]["job"].pk, imported.pk)
        self.assertEqual(
            Job.objects.filter(
                title="Data Analyst", company="Analytics Works", location="Remote"
            ).count(),
            1,
        )

    def test_excel_import_accepts_numeric_sheet_index(self):
        import pandas as pd

        with tempfile.TemporaryDirectory() as directory:
            dataset_path = Path(directory) / "postings.xlsx"
            with pd.ExcelWriter(dataset_path) as writer:
                pd.DataFrame([{"title": "Ignore This Sheet"}]).to_excel(
                    writer, sheet_name="First", index=False
                )
                pd.DataFrame([{
                    "title": "Excel Sheet Import Test",
                    "company": "Workbook Systems",
                    "location": "Remote",
                    "required_skills": "Python",
                    "description": "Build Python services.",
                }]).to_excel(writer, sheet_name="Second", index=False)

            call_command("import_jobs", str(dataset_path), "--sheet", "1")

        self.assertTrue(Job.objects.filter(
            title="Excel Sheet Import Test",
            company="Workbook Systems",
            location="Remote",
        ).exists())

    def test_dataset_column_aliases_normalize_to_shared_job_shape(self):
        import pandas as pd
        from core.ai_engine import _dataset_records

        dataset = pd.DataFrame([{
            "Position": "Alias Engineer",
            "Employer": "Alias Systems",
            "Job Description": "Build APIs with Python and SQL.",
            "Skill Set": ["Python", "SQL"],
            "Job Category": "Engineering",
            "Job Location": "Remote",
            "Job Type": "Contract",
            "Minimum Experience Years": 2,
            "Maximum Experience Years": 5,
            "Compensation": "INR 10-15 LPA",
            "Application Link": "https://example.com/jobs/alias",
            "Date Posted": "2026-10-01",
        }])
        with (
            patch("core.ai_engine.get_bundle", return_value={"jobs": dataset}),
            patch("core.ai_engine._dataset_records_cache", None),
            patch("core.ai_engine._dataset_jobs_by_key", None),
        ):
            record = _dataset_records()[0]
            from core.ai_engine import search_jobs
            compatible = search_jobs({"title": "Alias Engineer", "experience": "4"})
            over_range = search_jobs({"title": "Alias Engineer", "experience": "6"})

        self.assertEqual(record["source_label"], "Dataset")
        self.assertEqual(record["job_title"], "Alias Engineer")
        self.assertEqual(record["company"], "Alias Systems")
        self.assertEqual(record["required_skills"], "Python, SQL")
        self.assertEqual(record["category"], "Engineering")
        self.assertEqual(record["max_experience_years"], "5")
        self.assertEqual(record["salary"], "INR 10-15 LPA")
        self.assertEqual(record["application_url"], "https://example.com/jobs/alias")
        self.assertEqual([item["job_title"] for item in compatible], ["Alias Engineer"])
        self.assertEqual(over_range, [])

    def test_job_model_rejects_maximum_experience_below_minimum(self):
        from django.core.exceptions import ValidationError

        self.database_job.required_experience_years = 5
        self.database_job.max_experience_years = 4

        with self.assertRaises(ValidationError):
            self.database_job.full_clean()

    def test_sample_job_seed_command_is_repeatable(self):
        from io import StringIO

        output = StringIO()
        call_command(
            "seed_sample_jobs", recruiter=self.recruiter.username, stdout=output
        )
        first_count = Job.objects.filter(recruiter=self.recruiter).count()
        call_command(
            "seed_sample_jobs", recruiter=self.recruiter.username, stdout=output
        )

        self.assertEqual(first_count, 11)
        self.assertEqual(Job.objects.filter(recruiter=self.recruiter).count(), 11)
        self.assertEqual(
            Job.objects.filter(recruiter=self.recruiter, status="open").count(), 11
        )
        self.assertEqual(
            set(Job.objects.filter(
                recruiter=self.recruiter,
                description__startswith="Sample listing for local testing.",
            ).values_list("title", flat=True)),
            {
                "Python Developer", "Django Developer", "Data Scientist",
                "Data Analyst", "ML Engineer", "AI Engineer", "Java Developer",
                "Web Developer", "Business Analyst", "Software Engineer",
            },
        )
        with patch("core.ai_engine._dataset_records", return_value=[]):
            self.assertContains(
                self.client.get(reverse("job_list"), {"title": "Python Developer"}),
                "Python Developer",
            )

    def test_resume_recommendations_rank_database_and_dataset_jobs_together(self):
        import pandas as pd
        from scipy.sparse import csr_matrix
        from sklearn.feature_extraction.text import TfidfVectorizer
        from core.ai_engine import recommend_jobs_for_text

        dataset_rows = [{
            "job_title": "Python Django Developer",
            "company": "DataWorks",
            "location": "Remote",
            "experience_level": "Mid Level",
            "salary": "90000",
            "industry": "Engineering",
            "required_skills": "Python, Django",
            "job_text": "Build Django services using Python.",
            "required_experience_years": 3,
            "skills": ["python", "django"],
        }]
        vectorizer = TfidfVectorizer()
        job_matrix = vectorizer.fit_transform([
            "Python Django Developer DataWorks Engineering Python Django Build Django services using Python.",
            f"{self.database_job.title} {self.database_job.company} "
            f"{self.database_job.category} {self.database_job.required_skills} "
            f"{self.database_job.description}",
        ])
        bundle = {
            "jobs": pd.DataFrame(dataset_rows),
            "tfidf_vectorizer": vectorizer,
            "job_matrix": job_matrix[:1],
            "job_skill_matrix": csr_matrix([[1, 1, 0]]),
            "skill_labels": ["python", "django", "sql"],
            "skill_to_col": {"python": 0, "django": 1, "sql": 2},
            "experience_level_years": {"mid level": 2},
        }
        with (
            patch("core.ai_engine.get_bundle", return_value=bundle),
            patch("core.ai_engine._dataset_records_cache", None),
            patch("core.ai_engine._dataset_jobs_by_key", None),
        ):
            recommendations, profile = recommend_jobs_for_text(
                "Python Django SQL engineer with 5 years experience.",
                top_n=10,
            )

        self.assertEqual(
            {item["source"] for item in recommendations}, {"database", "dataset"}
        )
        self.assertEqual(profile["experience_years"], 5)
        self.assertTrue(all(item["match_score"] > 0 for item in recommendations))
        self.assertTrue(all(item["why_match"] for item in recommendations))
        self.assertTrue(any(item["matching_skills"] for item in recommendations))

    @patch("core.views.recommend_jobs_for_text")
    @patch("core.views.get_dataset_job")
    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_dataset_application_is_recorded_and_visible_in_tracker(
        self, get_dataset_job_mock, recommend_jobs_mock
    ):
        dataset_job = {
            "job_title": "Python Platform Engineer",
            "company": "Dataset Systems",
            "application_url": "https://jobs.example.com/python",
        }
        get_dataset_job_mock.return_value = dataset_job
        recommend_jobs_mock.return_value = (
            [{
                "source": "dataset",
                "source_label": "Dataset",
                "source_key": "42",
                "job_title": "Python Platform Engineer",
                "company": "Dataset Systems",
                "match_score": 84.5,
                "matching_skills": ["python"],
                "missing_skills": [],
            }],
            {},
        )
        self.client.force_login(self.seeker)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(
            dashboard, reverse("apply_dataset_job", args=["42"])
        )
        self.assertContains(dashboard, "Dataset")
        apply_url = reverse("apply_dataset_job", args=["42"])
        response = self.client.post(
            apply_url,
            {
                "resume": self.resume.pk,
                "applicant_name": "Unified Candidate",
                "applicant_email": self.seeker.email,
                "applicant_phone": "+1 555 0100",
                "applicant_location": "Remote",
                "cover_letter": "I build Python services.",
                "consent_to_share": "on",
            },
        )
        application = Application.objects.get(
            applicant=self.seeker, source="dataset", external_job_key="42"
        )
        self.assertRedirects(
            response,
            reverse("application_confirmation", args=[application.reference]),
        )
        self.assertIsNone(application.job)
        self.assertEqual(application.display_title, dataset_job["job_title"])
        self.assertEqual(application.match_score, 84.5)
        self.assertEqual(application.status, "submitted")
        self.assertEqual(application.status_history.count(), 1)
        tracker = self.client.get(reverse("my_applications"))
        self.assertContains(tracker, dataset_job["job_title"])
        self.assertContains(tracker, "Dataset")
        self.assertContains(tracker, "not confirmation that the employer received")

        confirmation = self.client.get(
            reverse("application_confirmation", args=[application.reference])
        )
        self.assertContains(confirmation, "Continue on employer website")

        self.client.post(apply_url, {"resume": self.resume.pk})
        self.assertEqual(
            Application.objects.filter(
                applicant=self.seeker, source="dataset", external_job_key="42"
            ).count(),
            1,
        )

    def test_admin_login_and_full_database_job_management(self):
        password = "ValidAdminPass987!"
        admin = User.objects.create_superuser(
            username="new-job-admin", email="new-job-admin@example.com",
            password=password,
        )
        login = self.client.post(
            reverse("admin:login"),
            {
                "username": admin.username,
                "password": password,
                "next": reverse("administration_jobs"),
            },
        )
        self.assertRedirects(
            login, reverse("administration_jobs"), fetch_redirect_response=False
        )
        self.assertEqual(
            self.client.get(reverse("administration_jobs")).status_code, 200
        )
        payload = {
            "title": "Secure Database Engineer",
            "company": "Admin Created Co",
            "description": "Build and support Python services.",
            "required_skills": "Python, Django",
            "preferred_skills": "PostgreSQL",
            "category": "Engineering",
            "experience_level": "Mid level",
            "required_experience_years": "3",
            "max_experience_years": "6",
            "education_requirements": "Computer science degree",
            "qualifications": "Three years building web services",
            "location": "Remote",
            "employment_type": "Full-time",
            "work_mode": "remote",
            "salary": "100000",
            "responsibilities": "Build secure services.",
            "application_url": "https://jobs.example.com/apply",
            "deadline": "",
            "status": "open",
        }
        with patch("core.ai_engine._dataset_records_cache", []):
            create = self.client.post(
                reverse("administration_job_create"), payload
            )
            self.assertRedirects(create, reverse("administration_jobs"))
            job = Job.objects.get(title=payload["title"])
            from core.ai_engine import search_jobs
            self.assertIn(job.pk, {item["job"].pk for item in search_jobs({})})
            browse = self.client.get(
                reverse("job_list"), {"title": "Secure Database Engineer"}
            )
            self.assertContains(browse, "Secure Database Engineer")
            self.assertContains(browse, "Database")
            self.assertEqual(job.preferred_skills, "PostgreSQL")
            self.assertEqual(job.max_experience_years, 6)
            self.assertEqual(job.education_requirements, "Computer science degree")
            self.assertEqual(job.application_url, payload["application_url"])

            edited = {**payload, "title": "Revised Database Engineer"}
            response = self.client.post(
                reverse("administration_job_edit", args=[job.pk]), edited
            )
            self.assertRedirects(response, reverse("administration_jobs"))
            job.refresh_from_db()
            self.assertEqual(job.title, "Revised Database Engineer")

            response = self.client.post(
                reverse("administration_job_status", args=[job.pk]),
                {"status": "closed"},
            )
            self.assertRedirects(response, reverse("administration_jobs"))
            job.refresh_from_db()
            self.assertFalse(job.is_active)
            self.assertNotIn(job.pk, {item["job"].pk for item in search_jobs({})})

            self.client.post(
                reverse("administration_job_status", args=[job.pk]),
                {"status": "open"},
            )
            job.refresh_from_db()
            self.assertTrue(job.is_active)
            self.client.post(reverse("administration_job_delete", args=[job.pk]))
            self.assertFalse(Job.objects.filter(pk=job.pk).exists())

    @patch("core.views.predict_category", return_value=("ENGINEERING", 0.91))
    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_django_admin_job_is_recommended_after_resume_upload(self, predict):
        import pandas as pd
        from scipy.sparse import csr_matrix
        from sklearn.feature_extraction.text import TfidfVectorizer

        admin = User.objects.create_superuser(
            username="admin-job-entry",
            email="admin-job-entry@example.com",
            password="ValidAdminPass987!",
        )
        self.client.force_login(admin)
        admin_add_url = reverse("admin:core_job_add")
        job_data = {
            "recruiter": self.recruiter.pk,
            "title": "Python Django Database Specialist",
            "company": "Admin Jobs Co",
            "description": "Build Python Django services and SQL databases.",
            "required_skills": "Python, Django, SQL",
            "preferred_skills": "PostgreSQL",
            "category": "Engineering",
            "experience_level": "Mid level",
            "required_experience_years": "2",
            "education_requirements": "Computer science",
            "qualifications": "Backend development experience",
            "responsibilities": "Build and maintain Python services.",
            "location": "Remote",
            "employment_type": "Full-time",
            "work_mode": "remote",
            "salary": "100000",
            "application_url": "https://jobs.example.com/admin-job",
            "deadline": "",
            "status": "open",
            "_save": "Save",
        }
        admin_add = self.client.post(admin_add_url, job_data)
        self.assertEqual(admin_add.status_code, 302)
        job = Job.objects.get(title=job_data["title"])
        self.assertTrue(job.is_active)

        self.client.force_login(self.seeker)
        bundle = {
            "tfidf_vectorizer": TfidfVectorizer().fit([
                "Python Django SQL backend development services"
            ]),
            "job_matrix": csr_matrix((0, 0)),
            "jobs": pd.DataFrame(),
        }
        with (
            patch("core.ai_engine.get_bundle", return_value=bundle),
            patch("core.ai_engine._dataset_records", return_value=[]),
        ):
            uploaded = self.client.post(
                reverse("upload_resume"),
                {
                    "file": SimpleUploadedFile(
                        "admin-job-resume.txt",
                        b"Python Django SQL backend developer with 5 years experience.",
                        content_type="text/plain",
                    )
                },
            )
            self.assertRedirects(uploaded, reverse("dashboard"))
            resume = Resume.objects.filter(user=self.seeker).latest("uploaded_at")
            self.addCleanup(resume.file.delete, save=False)
            dashboard = self.client.get(reverse("dashboard"))

        self.assertContains(dashboard, job.title)
        self.assertContains(dashboard, "Database")
        predict.assert_called_once()

    def test_non_admin_cannot_manage_database_jobs(self):
        self.client.force_login(self.seeker)
        response = self.client.get(reverse("administration_jobs"))
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse("administration_job_create"), {})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Job.objects.filter(title="Unauthorized job").exists())


class RecommendationFeedbackTests(TestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(
            username="feedback_recruiter", password="ValidPass123!",
        )
        UserProfile.objects.create(user=self.recruiter, role="recruiter")
        self.seeker = User.objects.create_user(
            username="feedback_seeker", password="ValidPass123!",
        )
        UserProfile.objects.create(user=self.seeker, role="seeker")
        self.first_job = Job.objects.create(
            recruiter=self.recruiter,
            title="Python Developer",
            company="First Systems",
            location="Remote",
            required_skills="Python, SQL",
            description="Build Python services.",
        )
        self.second_job = Job.objects.create(
            recruiter=self.recruiter,
            title="Python Developer",
            company="Second Systems",
            location="Remote",
            required_skills="Python, SQL",
            description="Build Python services.",
        )
        self.client.force_login(self.seeker)

    def test_candidate_profile_rejects_an_inverted_salary_preference_range(self):
        from .forms import ProfileForm

        form = ProfileForm(
            data={
                "experience_years": "0",
                "preferred_salary_min": "1200000",
                "preferred_salary_max": "900000",
            },
            instance=self.seeker.profile,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("preferred_salary_max", form.errors)

    def test_explicit_feedback_updates_per_user_and_renders_on_job_detail(self):
        detail = reverse("job_detail", args=[self.first_job.pk])
        response = self.client.get(detail)
        self.assertContains(response, "Was this recommendation relevant?")

        feedback_url = reverse(
            "recommendation_feedback",
            args=["database", str(self.first_job.pk)],
        )
        response = self.client.post(
            feedback_url, {"relevant": "true", "next": detail}
        )
        self.assertRedirects(response, detail)
        response = self.client.post(feedback_url, {"relevant": "false"})
        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(
            JobRecommendationFeedback.objects.filter(
                user=self.seeker, job=self.first_job
            ).count(),
            1,
        )
        self.assertFalse(
            JobRecommendationFeedback.objects.get(
                user=self.seeker, job=self.first_job
            ).is_relevant
        )
        feedback = JobRecommendationFeedback.objects.get(
            user=self.seeker, job=self.first_job
        )
        self.assertEqual(feedback.reason, "not_relevant")
        self.client.post(
            feedback_url,
            {"reason": "skills_mismatch", "next": detail},
        )
        feedback.refresh_from_db()
        self.assertEqual(feedback.reason, "skills_mismatch")
        self.assertFalse(feedback.is_relevant)

    def test_feedback_personalizes_results_without_becoming_training_labels(self):
        from core.ai_engine import recommend_jobs_for_text

        JobRecommendationFeedback.objects.create(
            user=self.seeker,
            job=self.first_job,
            source="database",
            source_key=str(self.first_job.pk),
            is_relevant=False,
        )
        JobRecommendationFeedback.objects.create(
            user=self.seeker,
            job=self.second_job,
            source="database",
            source_key=str(self.second_job.pk),
            is_relevant=True,
        )
        with (
            patch("core.ai_engine.get_bundle", return_value=None),
            patch("core.ai_engine._dataset_records", return_value=[]),
        ):
            ranked, _ = recommend_jobs_for_text(
                "Python developer",
                db_jobs=[self.first_job, self.second_job],
                include_dataset=False,
                user=self.seeker,
            )

        self.assertEqual([item["job"].pk for item in ranked], [self.second_job.pk])
        self.assertIn("you marked this role relevant", ranked[0]["why_match"])

    def test_job_detail_displays_resume_evidence_and_skill_gap_ideas(self):
        resume = Resume.objects.create(
            user=self.seeker,
            file=SimpleUploadedFile(
                "feedback-resume.txt",
                b"Built Python applications as a developer.",
                content_type="text/plain",
            ),
            extracted_text="Built Python applications as a developer.",
            extracted_skills=["python"],
        )
        self.addCleanup(resume.file.delete, save=False)
        with patch("core.ai_engine.get_bundle", return_value=None):
            response = self.client.get(
                reverse("job_detail", args=[self.first_job.pk])
            )

        self.assertContains(response, "How your resume matches this role")
        self.assertContains(response, "Missing required: sql")
        self.assertContains(response, "Resume evidence")
        self.assertContains(response, "Ways to build missing skills")
        self.assertContains(response, "not claims about your existing qualifications")


class ContactWorkflowTests(TestCase):
    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="support@example.com",
    )
    def test_contact_form_delivers_to_configured_support_inbox(self):
        response = self.client.post(
            reverse("contact"),
            {"name": "A Candidate", "email": "candidate@example.com", "message": "Hello"},
        )

        self.assertRedirects(response, reverse("contact"))
        self.assertEqual(mail.outbox[-1].to, ["support@example.com"])
        self.assertIn("Hello", mail.outbox[-1].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="JobPilot <support@example.com>",
    )
    def test_password_reset_page_sends_an_email_with_a_token_link(self):
        user = User.objects.create_user(
            username="resetuser", email="reset@example.com",
            password="SecurePass987!",
        )

        response = self.client.post(
            reverse("password_reset"), {"email": user.email}
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(mail.outbox[-1].to, [user.email])
        self.assertIn("reset/", mail.outbox[-1].body)


class ATSResumeBuilderWorkflowTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.seeker = User.objects.create_user(username="resume_builder_candidate")
        UserProfile.objects.create(user=self.seeker, role="seeker")

    def test_submit_previews_and_downloads_a_real_owned_pdf(self):
        self.client.force_login(self.seeker)
        response = self.client.post(
            reverse("resume_builder"),
            {
                "full_name": "A Candidate",
                "email": "candidate@example.com",
                "target_job_title": "Python Developer",
                "technical_skills": "Python, Django, SQL",
                "experience": "Example Co | Engineer | 2022-present",
                "projects": "Built a hiring platform",
            },
        )

        builder = self.seeker.resume_builders.get()
        self.assertEqual(builder.work_experience, "Example Co | Engineer | 2022-present")
        self.assertTrue(builder.generated_pdf)
        self.assertTrue(builder.generated_pdf.storage.exists(builder.generated_pdf.name))
        self.assertRedirects(
            response,
            reverse("resume_builder_preview", args=[builder.pk]),
        )
        preview = self.client.get(
            reverse("resume_builder_preview", args=[builder.pk])
        )
        self.assertContains(preview, "Download PDF")
        self.assertContains(
            preview,
            reverse("resume_builder_pdf", args=[builder.pk]),
        )

        pdf = self.client.get(reverse("resume_builder_pdf", args=[builder.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        self.assertIn("inline", pdf["Content-Disposition"])

        download = self.client.get(
            reverse("resume_builder_download", args=[builder.pk])
        )
        self.assertTrue(download.content.startswith(b"%PDF-"))
        self.assertIn("attachment", download["Content-Disposition"])

    def test_selecting_a_resume_template_is_persisted_and_used_in_preview(self):
        self.client.force_login(self.seeker)
        response = self.client.post(
            reverse("resume_builder"),
            {
                "full_name": "A Candidate",
                "email": "candidate@example.com",
                "template": "developer",
                "technical_skills": "Python, Django",
            },
        )

        builder = self.seeker.resume_builders.get()
        self.assertEqual(builder.template, "developer")
        self.assertRedirects(
            response, reverse("resume_builder_preview", args=[builder.pk])
        )
        preview = self.client.get(
            reverse("resume_builder_preview", args=[builder.pk])
        )
        self.assertIn("private, no-store", self.client.get(
            reverse("resume_builder_pdf", args=[builder.pk])
        )["Cache-Control"])
        self.assertContains(preview, "zoom=100")

    def test_builder_resume_is_not_visible_to_another_candidate(self):
        from .models import ResumeBuilder

        builder = ResumeBuilder.objects.create(
            user=self.seeker, full_name="A Candidate", email="candidate@example.com"
        )
        other = User.objects.create_user(username="other_resume_candidate")
        UserProfile.objects.create(user=other, role="seeker")
        self.client.force_login(other)

        response = self.client.get(
            reverse("resume_builder_pdf", args=[builder.pk])
        )

        self.assertEqual(response.status_code, 404)


class ProfileUpdateWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="profile_update_candidate",
            email="before@example.com",
        )
        self.profile = UserProfile.objects.create(user=self.user, role="seeker")
        self.client.force_login(self.user)

    def test_profile_updates_user_and_profile_fields_and_shows_completion(self):
        response = self.client.post(
            reverse("profile"),
            {
                "first_name": "Taylor",
                "last_name": "Candidate",
                "email": "taylor@example.com",
                "phone": "555-0101",
                "location": "Remote",
                "headline": "Django developer",
                "career_summary": "Builds maintainable web applications.",
                "skills": "Python, Django",
                "education": "BSc Computer Science",
                "experience_years": "3.0",
                "linkedin_url": "https://www.linkedin.com/in/taylor",
                "portfolio_url": "https://example.com",
            },
        )

        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), "Taylor Candidate")
        self.assertEqual(self.user.email, "taylor@example.com")
        self.assertEqual(self.profile.skills, "Python, Django")
        self.assertEqual(self.profile.headline, "Django developer")

        profile_page = self.client.get(reverse("profile"))
        self.assertContains(profile_page, "Manage Your Profile")
        self.assertContains(profile_page, "Profile completeness")
        self.assertGreater(profile_page.context["profile_completion"], 0)

    def test_profile_rejects_an_email_owned_by_another_user(self):
        User.objects.create_user(username="existing_email", email="used@example.com")
        response = self.client.post(
            reverse("profile"),
            {"first_name": "Taylor", "last_name": "Candidate", "email": "used@example.com"},
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "before@example.com")
        self.assertContains(response, "An account already uses this email address.")


class ResumeAnalysisWorkflowTests(TestCase):
    def setUp(self):
        self.seeker = User.objects.create_user(username="analysis_candidate")
        UserProfile.objects.create(user=self.seeker, role="seeker")
        self.resume = Resume.objects.create(
            user=self.seeker,
            file=SimpleUploadedFile(
                "analysis.txt", b"Python developer resume",
                content_type="text/plain",
            ),
            extracted_text=(
                "Python developer. BSc Computer Science. "
                "3 years experience building projects."
            ),
            extracted_skills=["python", "django"],
            extracted_education=["bsc"],
            extracted_projects=["Hiring platform"],
            professional_summary="Full resume summary stays collapsed by default.",
            experience_years=3,
            ats_score=51,
            improvement_tips=["Add more detail about achievements."],
        )
        self.addCleanup(self.resume.file.delete, save=False)

    @patch("core.tasks.generate_recommendations.apply_async")
    def test_analysis_page_shows_recommendations_without_job_selector(self, queue):
        self.client.force_login(self.seeker)

        response = self.client.get(
            reverse("resume_analysis", args=[self.resume.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ATS readiness estimate")
        self.assertContains(response, "Resume details")
        self.assertContains(response, "Education")
        self.assertContains(response, "Projects")
        self.assertContains(response, "Certifications")
        self.assertContains(response, "Hiring platform")
        self.assertContains(response, "Resume category")
        self.assertContains(response, "Total experience")
        self.assertContains(response, "Languages")
        self.assertContains(response, "Technical skills")
        self.assertContains(response, "Internship experience")
        self.assertNotContains(response, "Resume profile")
        self.assertNotContains(response, "JOB-SPECIFIC KEYWORDS")
        self.assertNotContains(response, "compare-job")
        self.assertContains(response, "capped at 80/100")
        self.assertContains(response, "Recommended jobs")
        self.assertContains(response, f'{response.context["ats_score"]}/100')
        self.assertContains(response, "Readable content and section structure")
        self.assertEqual(
            response.context["ats_score"],
            sum(row["score"] for row in response.context["ats_breakdown"]),
        )
        self.resume.refresh_from_db()
        self.assertEqual(self.resume.ats_score, response.context["ats_score"])
        content = response.content.decode()
        self.assertLess(
            content.index("<details>"),
            content.index(self.resume.professional_summary),
        )
        self.assertContains(response, "data-recommendation-results")
        self.assertContains(response, "python")
        self.assertContains(response, "Add more detail about achievements.")
        self.assertIn(
            response.context["recommendation_status"],
            {"queued", "processing", "ready", "failed"},
        )

    def test_analysis_page_does_not_disclose_another_candidates_resume(self):
        other = User.objects.create_user(username="different_analysis_candidate")
        UserProfile.objects.create(user=other, role="seeker")
        self.client.force_login(other)

        response = self.client.get(
            reverse("resume_analysis", args=[self.resume.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_ats_score_uses_documented_evidence_based_rubric(self):
        from .ai_engine import ats_breakdown, ats_estimate

        text = (
            " ".join(["Python developer"] * 80)
            + " BSc Computer Science. 4 years experience. Projects include APIs."
        )
        breakdown = ats_breakdown(text, ["python", "django", "sql", "aws", "git"])

        self.assertEqual(sum(row["maximum"] for row in breakdown), 80)
        self.assertEqual(
            ats_estimate(text, ["python", "django", "sql", "aws", "git"]),
            sum(row["score"] for row in breakdown),
        )
        complete_text = "\n".join((
            "jane@example.com | +1 212 555 0184",
            "Summary",
            " ".join(["experienced professional"] * 160),
            "Skills",
            "Python, Django, SQL, AWS, Docker, React, Git, Linux",
            "Education",
            "Bachelor of Science in Computer Science",
            "Experience",
            "3 years of professional experience",
            "Projects",
            "Built a hiring platform",
            "Created a reporting dashboard",
            "Certifications",
            "AWS certification",
            "Python certification",
        ))
        perfect_breakdown = ats_breakdown(
            complete_text,
            ["python", "django", "sql", "aws", "docker", "react", "git", "linux"],
            education=["bsc"],
            experience_years=3,
            projects=["Built a hiring platform", "Created a reporting dashboard"],
            certifications=["AWS certification", "Python certification"],
        )
        self.assertEqual(sum(row["maximum"] for row in perfect_breakdown), 80)
        self.assertEqual(sum(row["score"] for row in perfect_breakdown), 80)
        self.assertEqual(
            [row["label"] for row in perfect_breakdown],
            [
                "Readable content and section structure",
                "Contact details",
                "Relevant skills identified",
                "Education details",
                "Experience details",
                "Project evidence",
                "Certifications or achievements",
            ],
        )

    def test_project_points_require_detected_project_evidence(self):
        from .ai_engine import ats_breakdown

        mentioned_only = ats_breakdown(
            "I enjoy projects and certifications.", []
        )
        actual_project = ats_breakdown(
            "I built an API.",
            [],
            projects=["Built a hiring API"],
        )

        self.assertEqual(mentioned_only[-2]["score"], 0)
        self.assertEqual(mentioned_only[-1]["score"], 0)
        self.assertEqual(actual_project[-2]["score"], 4)


class NotificationRealtimeTests(TestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(username="notifications_recruiter")
        UserProfile.objects.create(user=self.recruiter, role="recruiter")
        self.seeker = User.objects.create_user(username="notifications_seeker")
        UserProfile.objects.create(user=self.seeker, role="seeker")
        self.other = User.objects.create_user(username="notifications_other")
        UserProfile.objects.create(user=self.other, role="seeker")
        job = Job.objects.create(
            recruiter=self.recruiter,
            title="Notification Engineer",
            company="Northstar",
            required_skills="Python",
            description="Build notification systems.",
        )
        self.application = Application.objects.create(
            job=job, applicant=self.seeker, applicant_name="Candidate"
        )
        self.seeker_notification = Notification.objects.create(
            recipient=self.seeker,
            application=self.application,
            title="Application updated",
            message="Your application is under review.",
        )
        self.recruiter_notification = Notification.objects.create(
            recipient=self.recruiter,
            application=self.application,
            title="New job application",
            message="A candidate applied.",
        )
        Notification.objects.create(
            recipient=self.other,
            title="Private notification",
            message="This belongs to another user.",
        )

    def test_poll_returns_only_recipient_events_and_advances_cursor(self):
        self.client.force_login(self.seeker)
        url = reverse("notification_updates")

        response = self.client.get(url, {"after_id": 0})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["unread_count"], 1)
        self.assertEqual(
            [item["id"] for item in data["notifications"]],
            [self.seeker_notification.pk],
        )
        self.assertEqual(data["cursor"], self.seeker_notification.pk)
        self.assertEqual(
            self.client.get(url, {"after_id": data["cursor"]}).json()["notifications"],
            [],
        )
        self.assertEqual(self.client.get(url, {"after_id": "invalid"}).status_code, 400)

    def test_recruiters_can_view_and_poll_only_their_own_notifications(self):
        self.client.force_login(self.recruiter)

        updates = self.client.get(reverse("notification_updates"), {"after_id": 0})
        self.assertEqual(
            [item["id"] for item in updates.json()["notifications"]],
            [self.recruiter_notification.pk],
        )
        inbox = self.client.get(reverse("notifications"))

        self.assertEqual(inbox.status_code, 200)
        self.assertContains(inbox, "A candidate applied.")
        self.assertNotContains(inbox, "Your application is under review.")
        self.recruiter_notification.refresh_from_db()
        self.seeker_notification.refresh_from_db()
        self.assertIsNone(self.recruiter_notification.read_at)
        self.assertIsNone(self.seeker_notification.read_at)

        self.client.post(reverse("mark_notifications_read"))
        self.recruiter_notification.refresh_from_db()
        self.seeker_notification.refresh_from_db()
        self.assertIsNotNone(self.recruiter_notification.read_at)
        self.assertIsNone(self.seeker_notification.read_at)


class PlatformSecurityTests(TestCase):
    def test_logout_requires_a_post_request(self):
        user = User.objects.create_user(username="logout_candidate")
        UserProfile.objects.create(user=user, role="seeker")
        self.client.force_login(user)

        get_response = self.client.get(reverse("logout"))

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        post_response = self.client.post(reverse("logout"))
        self.assertRedirects(post_response, reverse("home"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

    def test_external_links_only_accept_http_and_https_urls(self):
        from .views import _safe_external_url

        self.assertEqual(
            _safe_external_url("https://jobs.example.com/apply?role=python"),
            "https://jobs.example.com/apply?role=python",
        )
        self.assertEqual(_safe_external_url("javascript:alert(1)"), "")
        self.assertEqual(_safe_external_url("//jobs.example.com/apply"), "")
        self.assertEqual(_safe_external_url("https://user:pass@example.com/"), "")
