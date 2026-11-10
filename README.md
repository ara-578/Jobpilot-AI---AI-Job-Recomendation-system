# JobPilot AI 

A Django recruitment application with a supplied recommendation bundle and a separate BiLSTM training notebook.

## Features
- Seeker/recruiter registration and role-aware dashboards
- Resume upload (PDF/DOCX/TXT), text extraction, skill/education/experience extraction
- Resume project/certification extraction, per-upload ATS completeness scores, and upload history
- Separate professional and internship experience extraction for recommendation eligibility
- Category prediction when `category_model` exists in the PKL
- Advanced, paginated database-job search by title, skill, company, location, salary, experience, employment type, work mode, posting age, and relevance
- Ranked resume recommendations across database jobs and the supplied model dataset
- Admin job management with status controls and full job details; recruiter job controls remain available
- Idempotent sample job seeding across software, data, AI, web, and business roles
- Applications to database and dataset recommendations, with a status/history tracker
- Candidate ranking from uploaded resume profiles
- Candidate search/filtering, recruiter-owned company profiles, and recruiter job edit/close/delete controls (jobs with applications are archived by closing)
- Recruiter discovery requires an explicit candidate opt-in; resume downloads require an authorized session
- Verified-email OTP for registration and password sign-in
- OCR text extraction for scanned PDF resumes (requires Tesseract OCR)
- Job search with work-mode filters and pagination, saved jobs, applications and status history
- Personalized seeker dashboard with resume status, profile completion, recommendations, latest jobs, saved jobs, recent applications, recently viewed jobs, and status summaries
- Employer company pages linked to jobs and editable only through the owning recruiter's profile
- Structured job details with resume-based match explanations, saved/apply actions, and a neutral fallback logo when no verified company image exists
- Profile management with completion guidance and an optional private profile photo
- ATS resume builder with five print layouts, live editor preview, and owner-protected generated PDF preview/download
- Recruiter-owned application review with interview scheduling and constrained status transitions
- Resume comparison against selected live job postings with matching and missing required skills
- Resume review and correction for extracted skills, education, experience, projects, certifications, and professional summary
- Paginated seeker and recruiter application lists with role-scoped access
- Password reset by email, working contact form, and Privacy/Terms pages
- Django admin and statistics
- Responsive purple-blue theme with a persistent light/dark switch, custom career illustration, branded favicon and social footer

## 1. Create a virtual environment
Windows PowerShell:
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 2. Put your trained model and datasets in place
Create `saved_models/` in the project root. Copy the PKL bundle exported by your notebook to:
`saved_models/ai_job_recommendation_system.pkl`

The Django app loads a trusted scikit-learn `.pkl` bundle containing keys such as `tfidf_vectorizer`, `job_matrix`, `jobs`, and optionally `category_model`. The app combines its job DataFrame with active Django database jobs for search and resume recommendations; the vectorizer supplies text similarity, while the category model predicts resume categories. New recruiter/admin database jobs are read on each request and need no retraining. If there is one `.pkl` in `saved_models/`, the app discovers it automatically; otherwise set `JOBPILOT_MODEL_PATH` to the intended bundle. The included `ai_job_recommendation_system (2).pkl` is the current inference bundle.

The bundled artifact contains a TF-IDF category-classification pipeline and job/resume feature data; it is not a BiLSTM. The separate `JobPilot_AI_BiLSTM_Attention.ipynb` trains a BiLSTM-with-attention **resume-category classifier** from labeled resume text and exports the Keras model, tokenizer, label encoder, preprocessing configuration, and held-out classification metrics. It is not a supervised resume-to-job relevance model, and its `.keras` artifact does not replace the Django `.pkl` inference bundle. To run training without changing the Django environment, use a separate environment with the notebook's pinned dependencies:

```powershell
py -m venv .venv-ml
.\.venv-ml\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install tensorflow==2.18.0 pandas==2.2.3 numpy==1.26.4 scikit-learn==1.6.1 PyMuPDF==1.25.3 openpyxl==3.1.5 joblib==1.4.2 jupyter
$env:JOBPILOT_DATA_ROOT = (Get-Location).Path
$env:JOBPILOT_ARTIFACT_DIR = Join-Path (Get-Location).Path "saved_models"
jupyter lab
```

Open `JobPilot_AI_BiLSTM_Attention.ipynb` in Jupyter and run all cells from top to bottom. By default it reads datasets from the project directory and writes Keras artifacts under `saved_models/`; override the two environment variables above for other locations. On Colab, the notebook's first cell installs its pinned training packages; upload both datasets and run every cell. The test split is held out from training and the notebook reports the metrics it actually measures. Its job-posting dataset does not contain validated resume-to-job relevance judgments, so ranking Precision@10, Recall@10, and NDCG@10 are intentionally not reported as results. Do not interpret category-classification metrics as job-match accuracy.

Resume recommendations rank current database and dataset jobs using a transparent 40/25/15/10/10 blend: required/optional skills, role/title and text relevance, experience, education, and candidate preferences. Unknown score dimensions are excluded from the weighted denominator; explicit minimum experience and required certifications are eligibility checks. Role suggestions use declared resume titles and combinations of extracted skills rather than a single shared word such as “developer.” The detail/dashboard cards show matching and missing skills, score components, and text evidence source. The score is a job-relevance comparison, not a hiring probability. Jobs without material skill, role, or text evidence are omitted.

Sentence embeddings are optional and must be loaded from a local model directory; inference never downloads model files. To enable them, install `sentence-transformers`, place the model files under `saved_models/all-MiniLM-L6-v2` (or set `JOBPILOT_EMBEDDING_MODEL_PATH` to an existing local model directory), and restart the app. When no local model is available, the matcher uses the bundled TF-IDF vectorizer; if that is unavailable too, it reports text similarity as unavailable rather than fabricating a score. Dataset imports are still matched dynamically with current recruiter database jobs.

Candidate profiles can store preferred roles, locations, category, employment type, work mode, and annual INR salary bounds from the profile page. Job search supports combined role, skill, category, location, employment, experience, work-mode, and parseable annual salary filters. Salary range filtering accepts INR values, LPA/lakh, crore, and monthly salary labels; unknown or non-INR salary formats are excluded when a salary filter is active.

Seekers can mark a job relevant or not relevant from its detail page. Saved jobs and profile role/location preferences are also available. Explicit negative feedback removes that job from the user's recommendations; positive feedback and saved preferences are only tie-break personalization signals and are never automatically added to model training labels. Recruiter-side candidate ranking retains its separately configured `JOBPILOT_RANKING_WEIGHTS` heuristic.

The supplied BiLSTM-with-attention artifact is a resume-category classifier, not a resume-to-job relevance model. The repository does not include independently judged resume/job relevance labels, so no representative ranking metric is claimed. To evaluate against a manually labelled dataset, create a JSON file with separate `validation` and `test` candidates:

```json
{
  "candidates": [
    {
      "candidate_id": "candidate-001",
      "split": "validation",
      "resume_text": "Resume text...",
      "relevant_job_keys": ["database:123"]
    },
    {
      "candidate_id": "candidate-002",
      "split": "test",
      "resume_text": "Resume text...",
      "relevant_job_keys": ["dataset:456"]
    }
  ]
}
```

Job keys use `database:<Job primary key>` or `dataset:<dataset source key>`. Run `python manage.py evaluate_recommendations --labels path\to\labels.json --output path\to\recommendation-report.json` after loading the active jobs; the command reports Precision@5, Precision@10, Recall@10, NDCG@10, and MRR separately for validation and test. Labels must be judged by people and the test split must remain untouched during tuning. The included estimator was serialized with scikit-learn 1.6.1; use the matching version for reproducible inference or retrain and export it with the deployment version.

If you have the exported `job_profiles.csv`, import it after migrating:
```powershell
python manage.py import_jobs saved_models/job_profiles.csv
```
To include the project’s larger source postings CSV as searchable/recommendable database jobs, run:
```powershell
python manage.py import_jobs postings.csv
```
The importer also accepts `.xlsx` and `.xlsm` files (first worksheet by default); pass `--sheet "Sheet Name"` or a zero-based numeric index to select another. Imported listings are read by search and resume recommendations immediately, are not used to retrain the model, and duplicate title/company/location rows are skipped. Closed and confirmed-expired dataset listings are excluded from search, recommendations, details, and applications. The 500 MB postings file can take time and database storage to import. To associate the imported jobs with an existing account, pass `--recruiter USERNAME`.
You can also create jobs from a recruiter account in the UI.

## 3. Set up the database
```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
# Replace the SMTP placeholders in .env with your sender and a newly-created Gmail app password.
python manage.py check
python manage.py migrate
python manage.py createsuperuser
```
The repository includes the schema migrations. Apply them with `migrate`; do not generate new migrations unless you have changed a model. Migrations `0026` and `0027` add recently viewed jobs, legacy alert storage, job-linked notifications, and an index for open recent job queries. Earlier interview/roadmap and alert records are retained in the database but those features are not exposed in the website.

## 4. Run
```powershell
# Start Redis in another terminal or use a reachable Redis service.
# Set CELERY_BROKER_URL if Redis is not at redis://127.0.0.1:6379/0.
python manage.py migrate
celery -A jobpilot worker --loglevel=INFO --pool=solo
# Start the Django web server in a separate terminal.
python manage.py runserver
```
Open http://127.0.0.1:8000/

Resume/job recommendation ranking runs in a Celery worker so it does not block
dashboard navigation. The dashboard shows an explicit queued, ready, or failed
state and refreshes the results while a worker is processing. Redis must be
available to the web process and worker; if the broker is unreachable, the
dashboard reports the failure and offers a retry rather than silently returning
empty results. On Windows, Celery's `solo` pool is suitable for local development;
use an appropriate production worker pool and process manager for deployment.

Run `python manage.py migrate` after updating the app. Additive migrations store
profile photos, selected resume templates, generated PDFs, and recently viewed
jobs; generated resume files should remain in private media storage and be
served through the authenticated resume-builder views.

## Job search and company pages

The `/jobs/` search page queries published Django database jobs (not the model
dataset) and supports combined role, skill, company, location, salary,
experience, employment type, work mode, posting-age, and relevance filters.
Resume recommendations remain a separate feature and may also use the supplied
model dataset. Job alerts are not available in the website; legacy alert
records remain stored to avoid deleting existing data.

Company pages use the owning recruiter's existing profile and active database
postings. Recruiters manage company details from their own profile; candidate
accounts cannot change company fields. Uploaded company logos are shown when
available, otherwise the app uses its neutral static fallback image.

## Retired career tools

The career-readiness, learning-roadmap, mock-interview, and market-insights
pages have been removed from navigation and the application routes. Their
existing database tables are retained by migrations so previously saved user
records are not deleted.

## Admin login and job management

Create a new administrator account using Django's interactive, password-hashing
command (enter a new username and password at its prompts):

```powershell
.\.venv\Scripts\python.exe manage.py createsuperuser
```

Sign in at `/admin/` with that account, or use the **Manage jobs** link in the
staff dashboard to add, edit, delete, activate, close, or mark database jobs
filled. These management pages require an authenticated staff account with the
corresponding Django job permissions. Do not add a hard-coded admin password
or share an existing user's password.

## Seed sample jobs

After applying migrations and creating an administrator, add ten searchable
sample listings across software, data, AI, web, and business categories:

```powershell
.\.venv\Scripts\python.exe manage.py seed_sample_jobs
```

The command uses the first superuser as the owner and skips existing matching
jobs on repeat runs. To assign the jobs to another existing account, pass
`--recruiter USERNAME`. Seeded external application links use the reserved
`example.com` domain; replace those placeholders before treating sample posts as
real vacancies.

Database jobs are read directly for every search and recommendation request;
new or edited jobs appear immediately without restarting Django. Dataset
postings stay in the bundled model and are combined with database postings in
search and resume recommendations. Applications to either source are recorded
in the seeker's application tracker; existing database-job applications retain
their recruiter-managed status workflow.

Apply schema updates before starting after an upgrade:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
```

## 5. Main URLs
- `/` landing page
- `/register/` choose job seeker or recruiter
- `/login/`
- `/dashboard/`
- `/profile/`
- `/resume/upload/`
- `/jobs/`
- `/applications/`
- `/saved-jobs/`
- `/companies/<recruiter_id>/`
- `/password-reset/`
- `/recruiter/jobs/new/`
- `/recruiter/jobs/`
- `/recruiter/applications/`
- `/admin/`

## Important integration notes
1. Use only a trusted PKL file: `joblib.load`/pickle can execute code while loading. Do not load a model from an unknown source.
2. Match scores are heuristic ranking signals, not probabilities of being hired. The resume ATS indicator is a transparent heuristic and not a commercial ATS result.
3. Category confidence is shown only if the saved category pipeline exposes `predict_proba`.
4. Candidate recommendations compare uploaded resumes stored in the app, and only include seekers who enable **Allow recruiters to find your resume** in their profile. Resume files are delivered through a login-protected, permission-checked download route. Never expose `MEDIA_ROOT` as a public static directory in production.
5. Scanned PDFs need OCR; this starter intentionally does not call external APIs.
6. Before deployment set `DEBUG=False`, a strong environment-based `SECRET_KEY`, proper `ALLOWED_HOSTS`, HTTPS, secure cookies, and private file storage. Keep resume files out of public web-server and object-storage paths.
7. Navbar social URLs are placeholders; change them to your own project/company profiles.
8. The contact form sends mail to `EMAIL_HOST_USER`; it does not store message content in the database.

## Email OTP configuration

Registration verifies the email address before activating the account. Sign-in requires a password and a fresh six-digit email code; codes expire after five minutes and are limited to five attempts. OTP delivery **always uses encrypted SMTP**. The console email backend is not configured as a fallback, so missing or invalid SMTP settings produce an explicit delivery error instead of exposing codes in the terminal.

For Gmail, create a **new 16-character** provider app password (never use your regular account password). A credential was pasted into the project conversation; revoke it with Google and do not reuse it. Create `.env` from the ignored template only if it does not already exist, then edit it:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py runserver
```

Set `EMAIL_HOST_USER`, `DEFAULT_FROM_EMAIL`, and `EMAIL_HOST_PASSWORD` in `.env`; `.env` is ignored by Git. Paste the 16-character app password into `EMAIL_HOST_PASSWORD` (spaces in Google's grouped display are removed automatically), and don't paste credentials into source files, issues, or chat. Set `EMAIL_USE_TLS=false` only when using an SSL SMTP endpoint (for example port 465 with `EMAIL_USE_SSL=true`). OTP delivery always uses SMTP; if Gmail credentials are malformed or rejected, sign-up/sign-in reports an actionable delivery error and does not authenticate the user. Contact and password-reset email use the same configured SMTP server.

For production, set `DJANGO_DEBUG=false`, a private `DJANGO_SECRET_KEY`, and `DJANGO_ALLOWED_HOSTS` to your deployed hostnames. Keep SMTP credentials in your hosting provider's secret manager rather than source control or a committed `.env` file. Session and CSRF cookies are marked secure, HTTPS redirection is enabled, and HSTS defaults to one year when debug mode is off. Set `DJANGO_HSTS_SECONDS` only after confirming every hostname is served exclusively over HTTPS. If TLS terminates at a trusted reverse proxy, set `DJANGO_TRUST_X_FORWARDED_PROTO=true` only when that proxy overwrites `X-Forwarded-Proto`; never trust this header from arbitrary clients.

## Scanned resume OCR

Scanned PDFs are processed with PyMuPDF's OCR support at a memory-conscious
resolution, with a lower-resolution retry for unusually large scanned pages.
If OCR still cannot fit in available memory, the upload reports an actionable
message instead of failing with a server error. Install the Tesseract OCR
engine and its English language data on the machine running Django. On Windows,
the app also detects the standard `C:\Program Files\Tesseract-OCR` and
`C:\Program Files (x86)\Tesseract-OCR` install locations when Tesseract is not
already on `PATH`. For a custom installation, add the executable's folder to
`PATH` before starting Django. DOCX, TXT, and text-based PDF resumes do not
require Tesseract.

Match-score bars on the seeker dashboard summarize the share of detected resume
skills by discipline. Job match scores are ranking signals, not probabilities
of being hired. The supplied PKL was serialized with scikit-learn 1.6.1. Use
that version for inference, or retrain and re-export the model with the installed
scikit-learn version; loading across versions may produce warnings or change
prediction behavior.
