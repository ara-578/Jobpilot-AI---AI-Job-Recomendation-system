RECRUITER_STATUS_TRANSITIONS = {
    "submitted": {"reviewing", "shortlisted", "rejected"},
    "resume_viewed": {"reviewing", "shortlisted", "rejected"},
    "reviewing": {"shortlisted", "interview_scheduled", "rejected"},
    "shortlisted": {"interview_scheduled", "selected", "rejected"},
    "interview_scheduled": {"selected", "rejected"},
    "selected": set(),
    "rejected": set(),
    "withdrawn": set(),
}

