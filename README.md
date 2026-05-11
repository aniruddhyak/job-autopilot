# Job Autopilot 🚀

An AI-powered job discovery and application pipeline. Scrapes job postings from
multiple ATS sources (Workday, Greenhouse, Lever), scores them against your
resume using LLMs, and tracks your applications — all from a local dashboard.

> **Status**: 🚧 Under active development — Phase 1 (Discover + Dashboard) in progress.

---

## ✨ Features

- 🔍 **Multi-source discovery** — Workday today, Greenhouse and Lever coming soon
- 🤖 **LLM-based scoring** — match jobs against your resume with a configurable rubric *(planned)*
- 📊 **Local dashboard** — minimal, premium UI to browse jobs by company
- 📝 **AI cover letters** — tailored per job *(planned)*
- 📈 **Application tracking** — status, follow-ups, analytics *(planned)*
- 🔒 **Privacy-first** — runs entirely on your machine, your data never leaves

---

## 🛠 Tech Stack

| Layer | Tools |
|---|---|
| **Language** | Python 3.11+ |
| **HTTP / Scraping** | `httpx` |
| **Data validation** | `pydantic` v2 |
| **Web framework** | `FastAPI` + `uvicorn` |
| **CLI** | `Typer` |
| **Storage** | JSON files (atomic writes, file-locked) |
| **LLM** | OpenAI / Anthropic SDKs |
| **Frontend** | Vanilla JS + minimal CSS |

---

## 🚀 Quickstart

### Prerequisites
- Python 3.11 or higher
- Git

### Setup

```bash
# Clone
git clone https://github.com/aniruddhyak/job-autopilot.git
cd job-autopilot

# Create + activate virtual environment
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# Install dependencies + the package itself
pip install -r requirements-dev.txt
pip install -e .

# Configure environment
Copy-Item .env.example .env   # Windows
# cp .env.example .env        # macOS/Linux