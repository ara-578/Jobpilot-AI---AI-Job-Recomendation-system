# JobPilot AI verification report

**Environment:** Windows, Python 3.12.10, Django 5, SQLite

## Checks run

| Check | Result |
| --- | --- |
| `python manage.py test --verbosity 0` | **111 tests passed** in 303.122 seconds |
| Search cache pagination regression test | **1 test passed** |
| `python manage.py check` | No issues |
| `python manage.py makemigrations --check --dry-run` | No model changes pending |
| `python manage.py migrate --check` | No unapplied migrations |
| `python -m compileall -q core jobpilot` | Passed |

Migrations `0026` and `0027` were applied successfully. They add recently
viewed jobs, legacy alert storage, a job relation for notifications, and an
index for open recent jobs. The alert feature has since been removed from the
website; its schema and any existing records remain to avoid deleting user
data. The older `0025` career-tool schema is likewise retained although its
pages and workflows are retired.

Automated coverage includes database-only search filters and sorting, company
pages, seeker dashboard content and removed job-alert routes, application
workflows, recruiter ownership, resume analysis, ATS PDF workflows, and
retired-route 404 responses.

## Job-search timing sample

Measured four requests through Django's test client against the configured
local database, which contained **45,866 active open postings**. The query
used a real job title and company from that database. All four responses
returned HTTP 200:

- First request: **2,993.77 ms**
- Three repeated requests: **18.86 ms**, **16.95 ms**, and **17.01 ms**
- Repeated-request median: **17.93 ms**

This is a local cold/warm cache sample, not a statistically controlled
benchmark. No equivalent pre-change measurement was captured, so no
before/after speedup is claimed. The first uncached request remains slower
than the warm cache result.

## Remaining verification and limitations

- A complete click-by-click browser audit of every authenticated page was not
  performed.
- SMTP delivery and a live Redis/Celery worker were not exercised. Tests cover
  explicit email delivery failures and the local recommendation-worker
  fallback when Redis is unavailable.
- Recommendation relevance has not been evaluated against independently
  labeled resume/job pairs. The bundled model is a resume-category classifier,
  not a supervised resume-to-job relevance model.
- The response-time sample measures the local Django test client and SQLite
  configuration only; it is not representative of a production deployment.
