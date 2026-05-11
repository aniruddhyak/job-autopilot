"""Resume loading + token estimation for LLM scoring."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field

# Rough heuristic: ~4 chars per token for English text. Close enough for
# budgeting decisions; we don't need exact tokenization here.
_CHARS_PER_TOKEN = 4

# Bail out below this length — the file probably hasn't been filled in.
_MIN_RESUME_CHARS = 300

# Cap how much resume text we feed the LLM. 8K chars = ~2K tokens
# = plenty for cover-letter-relevance scoring. Truncating ultra-long
# resumes keeps costs predictable.
_MAX_RESUME_CHARS = 8000


class ResumeNotFoundError(Exception):
    """Raised when the resume file is missing, empty, or too short."""


class Resume(BaseModel):
    """A loaded, validated resume ready for LLM consumption."""

    path: Path = Field(..., description="Where the resume was loaded from.")
    text: str = Field(..., min_length=1, description="Resume content (markdown).")
    char_count: int = Field(..., ge=0)
    estimated_tokens: int = Field(..., ge=0)
    sha256: str = Field(..., description="Hash of the text — used to invalidate cached scores when resume changes.")
    truncated: bool = Field(False, description="True if content exceeded _MAX_RESUME_CHARS.")


def estimate_tokens(text: str) -> int:
    """Quick char-based token estimate. Not exact, but good for budgeting."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def load_resume(path: Path | str) -> Resume:
    """Load a markdown resume from disk and prepare it for scoring.

    Raises:
        ResumeNotFoundError: if the file is missing or appears empty.
    """
    p = Path(path)
    if not p.exists():
        raise ResumeNotFoundError(
            f"Resume not found at {p}. "
            f"Create it as markdown — see data/resume.md template."
        )

    text = p.read_text(encoding="utf-8").strip()
    if not text:
        raise ResumeNotFoundError(f"Resume at {p} is empty.")
    if len(text) < _MIN_RESUME_CHARS:
        raise ResumeNotFoundError(
            f"Resume at {p} is too short ({len(text)} chars). "
            f"Likely still the template — please add real content "
            f"(at least {_MIN_RESUME_CHARS} chars)."
        )

    truncated = False
    if len(text) > _MAX_RESUME_CHARS:
        text = text[:_MAX_RESUME_CHARS]
        truncated = True

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    return Resume(
        path=p,
        text=text,
        char_count=len(text),
        estimated_tokens=estimate_tokens(text),
        sha256=sha,
        truncated=truncated,
    )