# 🚀 JobPilot AI – Intelligent Job Recommendation System

### AI-Powered Career Assistance & Recruitment Platform

JobPilot AI is an intelligent, web-based job recommendation and recruitment platform developed using Django, Machine Learning, and Deep Learning. It helps job seekers discover relevant job opportunities based on their resume, skills, education, and experience while providing recruiters with intelligent candidate-matching capabilities.

The platform combines resume parsing, AI-powered job recommendations, ATS evaluation, skill gap analysis, and an integrated chatbot to simplify the recruitment process.

---

## 🎯 Project Objectives

- Automate resume analysis and skill extraction.
- Recommend relevant jobs based on candidate profiles.
- Improve job matching accuracy using AI and ML techniques.
- Evaluate resumes using an ATS scoring mechanism.
- Identify missing skills and suggest improvements.
- Help recruiters identify suitable candidates.
- Provide an interactive chatbot for job-related assistance.
- Track job applications through different recruitment stages.

---

## ✨ Key Features

### 👤 Job Seeker Module
- User registration and secure login.
- Professional profile management.
- Resume upload and analysis.
- Automatic skill, education, and experience extraction.
- AI-based job recommendations.
- Job search and filtering.
- Apply for jobs and save favourite jobs.
- Application status tracking.

### 📄 AI Resume Analyzer
- Supports PDF and DOCX resume formats.
- Extracts technical and professional skills.
- Identifies education and work experience.
- Extracts projects and certifications.
- Resume category prediction.
- ATS score estimation.
- Missing skill identification.
- Personalized resume improvement suggestions.

### 🤖 Intelligent Job Recommendation
- Resume-based job matching.
- Skill-based recommendation.
- Job description analysis.
- Matching and missing skill identification.
- Job ranking based on relevance.
- Top job recommendations.

### 📊 ATS Resume Evaluation
- Resume scoring.
- Skill matching analysis.
- Resume quality assessment.
- Suggestions to improve ATS compatibility.
- Professional resume generation.

### 🏢 Recruiter Module
- Recruiter registration and login.
- Company profile management.
- Post and manage job vacancies.
- View matching candidates.
- Candidate ranking based on matching scores.
- Review candidate profiles and resumes.
- Manage job applications.

### 💬 JobPilot AI Chatbot
- Interactive job assistance.
- Answers questions related to JobPilot AI.
- Provides guidance about platform features.
- Supports job and career-related queries.
- Uses project-specific question-answer knowledge.

### 📌 Application Tracking
Track applications through the following stages:

1. Applied
2. Resume Viewed
3. Under Review
4. Shortlisted
5. Interview Scheduled
6. Selected

---

## 🧠 Machine Learning & Deep Learning

The system incorporates AI techniques for resume understanding and job recommendation.

**Machine Learning:**
- Logistic Regression – Resume/job category prediction.
- TF-IDF – Text feature extraction.
- Cosine Similarity – Resume and job description matching.
- NLP – Text preprocessing and skill extraction.

**Deep Learning:**
- BiLSTM with Attention – Context-aware text analysis and classification (where implemented).

*Note: The algorithms listed should reflect the models actually integrated and trained in the project.*

---

## 🛠️ Technologies Used

| Category | Technologies |
|---|---|
| Frontend | HTML5, CSS3, JavaScript, Bootstrap |
| Backend | Python, Django |
| Database | SQLite |
| Machine Learning | Scikit-learn |
| Deep Learning | TensorFlow / Keras |
| NLP | NLTK, TF-IDF |
| Data Processing | Pandas, NumPy |
| Visualization | Matplotlib |
| Resume Processing | PyPDF, Python-docx |
| Version Control | Git, GitHub |

---

## ⚙️ System Architecture

1. User uploads a resume.
2. The system extracts text from the uploaded document.
3. NLP preprocessing cleans and prepares the extracted text.
4. The resume analyzer identifies skills, education, experience, and projects.
5. The recommendation engine compares candidate information with available job descriptions.
6. Similarity scores are calculated and relevant jobs are ranked.
7. The user receives personalized job recommendations.
8. Recruiters can view and rank suitable candidates.

---

## 📂 Project Structure

```text
JobPilot_AI_Django/
│
├── jobpilot/
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
│
├── core/
│   ├── views.py
│   ├── models.py
│   ├── urls.py
│   ├── ai_engine.py
│   ├── templates/
│   └── static/
│
├── media/
├── static/
├── templates/
├── manage.py
├── requirements.txt
├── .gitignore
└── README.md
```

*The structure may vary depending on the final project configuration.*

---

## 🚀 Installation & Setup

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/JobPilot_AI_Django.git
```

### 2. Navigate to the Project

```bash
cd JobPilot_AI_Django
```

### 3. Create a Virtual Environment

```bash
python -m venv venv
```

### 4. Activate the Environment

Windows:

```bash
venv\Scripts\activate
```

### 5. Install Dependencies

```bash
pip install -r requirements.txt
```

### 6. Apply Database Migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

### 7. Create Admin Account

```bash
python manage.py createsuperuser
```

### 8. Run the Development Server

```bash
python manage.py runserver
```

Open your browser:

```text
http://127.0.0.1:8000/
```

---

## 🔮 Future Enhancements

- Advanced semantic job recommendation.
- Improved resume classification models.
- Real-time job market insights.
- Advanced recruiter analytics dashboard.
- Interview preparation assistant.
- Multilingual chatbot support.
- Cloud deployment and scalability.

---

## 🎓 Project Information

**Project Name:** JobPilot AI  
**Domain:** Artificial Intelligence & Data Science  
**Project Type:** Full-Stack Web Application  
**Backend:** Django  
**Core Concepts:** Machine Learning, Deep Learning, NLP, Recommendation Systems

---

## 👩‍💻 Developer

**Arathi S B**

Artificial Intelligence and Data Science Intern

B.E. Computer Science and Engineering – AI & Data Science

---

⭐ If you find this project interesting, consider giving this repository a star!

**JobPilot AI – Your Skills. Your Opportunities. Your Future.**
