import csv
import re
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from core.models import Job, UserProfile


class Command(BaseCommand):
    help = "Import jobs from a CSV or Excel dataset into the searchable Django database."

    def add_arguments(self, parser):
        parser.add_argument("dataset_path")
        parser.add_argument(
            "--recruiter",
            help="Existing account username to own imported jobs.",
        )
        parser.add_argument(
            "--sheet",
            default=0,
            help="Excel worksheet name or zero-based index (default: first sheet).",
        )

    @staticmethod
    def _normalize_row(row):
        return {
            "".join(character for character in str(key).casefold() if character.isalnum()):
            value
            for key, value in row.items()
        }

    @staticmethod
    def _value(row, *aliases):
        for alias in aliases:
            value = row.get(
                "".join(character for character in alias.casefold() if character.isalnum())
            )
            if value is not None and str(value).strip().casefold() not in {
                "", "nan", "none", "null"
            }:
                return str(value).strip()
        return ""

    @staticmethod
    def _number(value):
        match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
        return float(match.group()) if match else None

    @staticmethod
    def _rows(path, sheet):
        suffix = path.suffix.casefold()
        if suffix == ".csv":
            with path.open(newline="", encoding="utf-8-sig", errors="replace") as source:
                yield from csv.DictReader(source)
            return
        if suffix in {".xlsx", ".xlsm"}:
            try:
                import pandas as pd
            except ImportError as exc:
                raise CommandError(
                    "Excel import requires pandas and openpyxl; install requirements.txt."
                ) from exc
            try:
                sheet_name = (
                    int(sheet)
                    if isinstance(sheet, str) and sheet.isdecimal()
                    else sheet
                )
                frame = pd.read_excel(path, sheet_name=sheet_name, dtype=object)
            except (ImportError, ValueError, OSError) as exc:
                raise CommandError(f"Could not read Excel dataset {path}: {exc}") from exc
            yield from frame.to_dict(orient="records")
            return
        raise CommandError("Dataset must be a .csv, .xlsx, or .xlsm file.")

    def handle(self, *args, **options):
        path = Path(options["dataset_path"])
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        recruiter_name = options["recruiter"]
        if recruiter_name:
            try:
                recruiter = User.objects.get(username=recruiter_name)
            except User.DoesNotExist as exc:
                raise CommandError(f"Recruiter account not found: {recruiter_name}") from exc
        else:
            recruiter, created = User.objects.get_or_create(
                username="jobpilot_dataset_recruiter",
                defaults={"email": "dataset-recruiter@example.com"},
            )
            if created:
                recruiter.set_unusable_password()
                recruiter.save(update_fields=["password"])
            UserProfile.objects.get_or_create(
                user=recruiter,
                defaults={"role": "recruiter", "company_name": "Imported listings"},
            )

        batch_size = 250
        batch = []
        skipped = 0
        duplicates = 0
        attempted = 0
        known_jobs = set(Job.objects.values_list("title", "company", "location"))
        for raw_row in self._rows(path, options["sheet"]):
            row = self._normalize_row(raw_row)
            title = self._value(row, "job_title", "title", "position", "role")
            if not title:
                skipped += 1
                continue
            company = self._value(
                row, "company", "company_name", "employer", "organization"
            ) or "Company not specified"
            location = self._value(row, "location", "job_location", "city", "region")
            identity = (title[:180], company[:180], location[:120])
            if identity in known_jobs:
                duplicates += 1
                continue
            known_jobs.add(identity)
            required_skills = self._value(
                row, "required_skills", "skills", "skill_set",
                "technical_skills", "skills_desc",
            )
            description = self._value(
                row, "job_text", "description", "job_description", "summary"
            ) or required_skills or title
            salary = self._value(row, "salary", "compensation", "pay")
            if not salary:
                salary = self._value(row, "normalized_salary", "max_salary", "min_salary")
            remote_allowed = self._value(row, "remote_allowed").casefold()
            required_experience = self._number(self._value(
                row, "required_experience_years", "minimum_experience_years",
                "min_experience_years", "years_experience",
            )) or 0
            maximum_experience = self._number(self._value(
                row, "max_experience_years", "maximum_experience_years",
                "experience_max",
            ))
            if maximum_experience is not None and maximum_experience < required_experience:
                maximum_experience = None
            defaults = {
                "recruiter": recruiter,
                "title": title[:180],
                "company": company[:180],
                "location": location[:120],
                "industry": self._value(row, "industry", "sector")[:120],
                "category": self._value(
                    row, "category", "job_category", "industry", "sector"
                )[:120],
                "experience_level": self._value(
                    row, "experience_level", "formatted_experience_level", "seniority"
                )[:60] or "Entry level",
                "required_skills": required_skills,
                "preferred_skills": self._value(
                    row, "preferred_skills", "desired_skills"
                ),
                "education_requirements": self._value(
                    row, "education_requirements", "education", "degree"
                ),
                "qualifications": self._value(
                    row, "qualifications", "qualification", "requirements"
                ),
                "description": description,
                "responsibilities": self._value(row, "responsibilities", "duties"),
                "employment_type": self._value(
                    row, "employment_type", "formatted_work_type", "job_type", "type"
                )[:60] or "Full-time",
                "work_mode": (
                    "remote" if remote_allowed in {"true", "1", "yes"}
                    else "on_site"
                ),
                "application_url": self._value(
                    row, "application_url", "apply_url", "application_link", "url"
                ),
                "salary": salary,
                "required_experience_years": required_experience,
                "max_experience_years": maximum_experience,
                "status": "open",
                "is_active": True,
            }
            batch.append(Job(**defaults))
            if len(batch) == batch_size:
                Job.objects.bulk_create(batch, batch_size=batch_size, ignore_conflicts=True)
                attempted += len(batch)
                batch.clear()
        if batch:
            Job.objects.bulk_create(batch, batch_size=batch_size, ignore_conflicts=True)
            attempted += len(batch)

        self.stdout.write(self.style.SUCCESS(
            f"Imported {attempted} jobs from {path.name}; skipped {duplicates} "
            f"duplicate jobs and {skipped} rows without a title."
        ))
