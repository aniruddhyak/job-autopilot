# Job Autopilot 🚀

A privacy-first, local job discovery, scoring, and application-tracking pipeline.

Job Autopilot scrapes job postings from configured ATS sources, enriches job details, scores roles against a private resume/profile using an LLM-powered rubric, and presents everything in a clean local dashboard so you can quickly decide which opportunities are worth applying to.

**Status**: 🚧 Under active development  
**Current progress**: Phase 1 and Phase 2 are complete. Phase 3 is in progress.

---

## ✅ Completed

### Phase 1 — Discovery + Dashboard
- Workday-based job discovery pipeline
- Configurable company/source setup
- Job filtering by title, location, and role relevance
- Job enrichment from posting details
- Local JSON-based storage
- Minimal local dashboard for browsing discovered jobs
- Company-focused job browsing experience

### Phase 2 — LLM Scoring
- Resume/profile-based job scoring
- Configurable scoring rubric
- Weighted scoring across skills, experience, domain fit, and role fit
- LLM-generated score reasoning
- Scored job output persisted locally
- Dashboard support for reviewing scored opportunities

### Phase 3 — Application Tracking Foundation
- API structure started for dashboard interactions
- Storage-helper direction established for application tracking data
- Application status workflow planned around practical decision states:
  - Interested
  - Applied
  - Interview
  - Offer
  - Rejected
  - Did Not Apply / Do Not Apply
- Notes/reason field planned for tracking why a job was not applied to

---

## ✨ Features

- 🔍 **Job discovery** — scrape jobs from configured ATS sources, starting with Workday
- 🧠 **LLM-based scoring** — rank jobs against a private resume/profile using a configurable rubric
- 📊 **Local dashboard** — minimal, premium UI for browsing discovered and scored jobs
- 🗂 **JSON-first storage** — simple local files instead of a database
- 📝 **Application tracking** — track decisions, application progress, and notes
- 🔒 **Privacy-first workflow** — sensitive files such as resumes, API keys, and personal history stay local
- 🧪 **Iterative pipeline** — discovery, enrichment, scoring, and dashboard phases can be run independently

---

## 🛠 Tech Stack

| Layer | Tools |
|---|---|
| Language | Python 3.11+ |
| HTTP / Scraping | httpx |
| Data validation | pydantic v2 |
| Web framework | FastAPI + uvicorn |
| CLI | Typer |
| Storage | JSON files with safe local writes |
| LLM | OpenAI / Anthropic SDKs |
| Frontend | Vanilla JS + minimal CSS |

---

## 📁 Project Structure

```text
job-autopilot/
├── app/
│   ├── api.py                  # FastAPI endpoints for dashboard/API workflows
│   ├── dashboard/              # Local dashboard UI assets
│   ├── discovery/              # Job discovery and ATS-specific logic
│   ├── scoring/                # LLM scoring and rubric logic
│   └── storage/                # JSON storage helpers
├── data/
│   ├── raw_jobs.json           # Discovered jobs
│   ├── scored_jobs.json        # LLM-scored jobs
│   ├── jobs_history.json       # Historical job/application state
│   ├── discovery_meta.json     # Discovery run metadata
│   └── scoring_meta.json       # Scoring run metadata
├── config/
│   ├── sources.example.json    # Safe sample source configuration
│   └── rubric.example.json     # Safe sample scoring rubric
├── .env.example                # Environment variable template
├── requirements-dev.txt
└── README.md
```

> Exact folders/files may vary as the project evolves, but the current architecture is centered around local JSON storage, FastAPI endpoints, and a lightweight dashboard.

---

## 🚀 Quickstart

### Prerequisites

- Python 3.11 or higher
- Git
- API key for the LLM provider you plan to use

### Setup

```bash
# Clone
git clone https://github.com/aniruddhyak/job-autopilot.git
cd job-autopilot

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements-dev.txt
pip install -e .
```

### Configure Environment

```bash
# Windows PowerShell
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

Then update `.env` with your local configuration and API keys.

> Keep `.env`, real resume files, private scoring profiles, and real application history out of Git.

---

## 🔐 Privacy & Security

This project is designed to keep sensitive job-search data local.

Recommended private files:

```text
.env
resume.md
resume_scoring.md
config/sources.json
config/rubric.json
data/*.json
```

Recommended public-safe files:

```text
.env.example
config/sources.example.json
config/rubric.example.json
README.md
```

---

## 🧭 Roadmap

### Completed
- Phase 1: Discovery + dashboard foundation
- Phase 2: LLM scoring pipeline

### In Progress
- Phase 3: Application tracking
  - Status updates from dashboard
  - Notes/reason field
  - Did Not Apply / Do Not Apply workflow
  - API and storage integration

### Planned
- Analytics dashboard
- Remote vs onsite breakdown
- Best/worst fit companies
- Skill-gap insights
- Discovery velocity
- Application-rate tracking
- Platform/source market share
- Optional AI-generated cover-letter support
- Additional ATS sources such as Greenhouse and Lever

---

## 🧠 Scoring Model

The scoring flow compares each job against a private resume/profile and produces structured output that can be reviewed in the dashboard.

Current rubric direction:

| Category | Weight |
|---|---:|
| Skills match | 35% |
| Experience match | 25% |
| Domain fit | 20% |
| Role fit | 20% |

The scoring rubric is configurable and should remain private if it includes personal resume details.

---

## 📌 Application Statuses

Application tracking is being designed around a simple workflow:

| Status | Meaning |
|---|---|
| Interested | Worth reviewing or applying later |
| Applied | Application submitted |
| Interview | Interview process started |
| Offer | Offer received |
| Rejected | Company rejected or process ended |
| Did Not Apply / Do Not Apply | User decided not to apply |

For jobs marked **Did Not Apply / Do Not Apply**, the dashboard should support a note/reason field so the decision is easy to understand later.

---

## 📄 License
This project is licensed under the MIT License.
See the LICENSE file for details.