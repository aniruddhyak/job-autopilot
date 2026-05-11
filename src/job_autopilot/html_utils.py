"""HTML cleaning utilities for job descriptions."""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

# Block tags that produce paragraph breaks.
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "table", "thead", "tbody",
    "section", "article", "header", "footer",
}
_LIST_ITEM_TAGS = {"li"}
_DROP_TAGS = {"script", "style", "noscript"}


class _PlainTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs):  # noqa: ARG002
        tag = tag.lower()
        if tag in _DROP_TAGS:
            self._drop_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")
        if tag in _LIST_ITEM_TAGS:
            self._parts.append("\u2022 ")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in _DROP_TAGS and self._drop_depth > 0:
            self._drop_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str):
        if self._drop_depth > 0:
            return
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str | None, *, max_blank_lines: int = 1) -> str:
    """Convert HTML to clean plain text. Handles double-encoded entities."""
    if not html:
        return ""

    text = html
    for _ in range(2):
        decoded = _html.unescape(text)
        if decoded == text:
            break
        text = decoded

    parser = _PlainTextExtractor()
    try:
        parser.feed(text)
        parser.close()
        text = parser.get_text()
    except Exception:
        text = re.sub(r"<[^>]+>", "", text)

    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(
        r"\n{" + str(max_blank_lines + 2) + r",}",
        "\n" * (max_blank_lines + 1),
        text,
    )
    return text.strip()


# ----------------------------------------------------------------------
# JD section filtering
# ----------------------------------------------------------------------

# Section headings we KEEP (job-relevant content).
_KEEP_HEADINGS = [
    "job summary",
    "summary",
    "meet the team",
    "your impact",
    "the role",
    "your role",
    "about the role",
    "about the team",
    "about this role",
    "overview",
    "job description",
    "responsibilities",
    "what you'll do",
    "what you will do",
    "key responsibilities",
    "essential responsibilities",
    "duties",
    "qualifications",
    "requirements",
    "what you'll bring",
    "what you will bring",
    "what we're looking for",
    "what we are looking for",
    "minimum qualifications",
    "basic qualifications",
    "preferred qualifications",
    "additional qualifications",
    "skills",
    "required skills",
    "preferred skills",
    "experience",
    "technical skills",
    "nice to have",
    "education",
]

# Section headings we DROP (boilerplate, marketing, metadata, legal).
_DROP_HEADINGS = [
    # Company / marketing
    "the company",
    "our company",
    "about us",
    "about the company",
    "who we are",
    "company description",
    "company overview",
    "why cisco",
    "why paypal",
    "why nvidia",
    "why salesforce",
    "why amazon",
    "why join",
    "why work",
    "why us",
    # Benefits / compensation
    "our benefits",
    "benefits",
    "perks",
    "compensation",
    "pay range",
    "salary range",
    "pay transparency",
    "annual bonus",
    "stock units",
    "primary location",
    "additional location",
    "subsidiary",
    "travel percent",
    "travel requirements",
    "work authorization",
    # Legal / EEO
    "commitment to diversity",
    "equal employment",
    "eeo",
    "diversity and inclusion",
    "belonging at",
    "non-discrimination",
    "accessibility",
    "accommodations",
    "applicants with disabilities",
    "fraud notice",
    "scam",
    "talent community",
    "for more information",
    "to apply",
    "how to apply",
    # Posting / process meta
    "message to applicants",
    "applying to work in the u.s",
    "u.s. and/or canada",
    "our hiring process",
    "the hiring process",
    "interview process",
    "next steps",
    "important notice",
    "legal notice",
    "fair chance",
    "application window",
]

# Boilerplate lines to drop wherever they appear.
_BOILERPLATE_PATTERNS = [
    # EEO / legal
    r"^.*equal opportunity employer.*$",
    r"^.*does not discriminate.*$",
    r"^.*reasonable accommodation.*$",
    r"^.*pursuant to.*fair chance.*$",
    r"^.*paypal does not charge candidates.*$",
    r"^.*if you suspect fraudulent activity.*$",
    r"^.*join our talent community.*$",
    r"^.*click here to learn more.*$",
    r"^.*to learn more about.*how to identify.*$",
    r"^.*we know the confidence gap.*$",
    r"^.*don.t hesitate to apply.*$",
    r"^.*official.*email domains.*$",
    # Cisco / generic marketing copy
    r"^.*revolutionizing how data.*$",
    r"^.*innovating fearlessly.*$",
    r"^.*we power the future.*$",
    r"^.*fueled by the depth.*$",
    r"^.*worldwide network of doers.*$",
    r"^.*power starts with you.*$",
    r"^.*we are cisco.*$",
    r"^.*at cisco.*revolutionizing.*$",
    # Cisco posting timing
    r"^.*application window is expected to close.*$",
    r"^.*job posting may be removed.*$",
    r"^.*hybrid days.*at the discretion.*$",
    r"^.*at the discretion of the team.*$",
    # Salary / compensation
    r"^.*starting salary range posted.*$",
    r"^.*reflects the projected salary range.*$",
    r"^.*individual pay is determined.*$",
    r"^.*full salary range.*$",
    r"^.*for locations not listed below.*$",
    r"^.*employees are offered benefits.*$",
    r"^.*subject to.*plan eligibility rules.*$",
    r"^.*paid time away.*$",
    r"^.*paid holidays per full calendar year.*$",
    r"^.*paid vacation time.*$",
    r"^.*sick time off.*$",
    r"^.*incentive target for each.*$",
    r"^.*incentive compensation.*$",
    r"^.*non-quota-based.*$",
    r"^.*quota-based incentive.*$",
    r"^.*for non-sales roles.*$",
    r"^.*employees on sales plans.*$",
    r"^.*employees in illinois.*$",
    r"^\s*\$[\d,]+(\.\d{2})?\s*-\s*\$[\d,]+(\.\d{2})?.*$",   # salary ranges line
    r"^\s*\$[\d,]+(\.\d{2})?\s*$",                            # bare dollar amount
    # Generic noise
    r"^\s*-\s*$",
    r"^\s*\*\s*$",
]


def _is_heading_line(line: str) -> bool:
    """Detect heading lines. Allows '?' and abbreviation dots ('U.S.', 'Inc.')."""
    s = line.strip().rstrip(":").rstrip("?").strip()
    if not s:
        return False
    word_count = len(s.split())
    # Headings are short
    if word_count > 14 or len(s) > 120:
        return False
    # Reject obvious sentences: period followed by space + lowercase
    if re.search(r"\.\s+[a-z]", s):
        return False
    # Reject exclamation (very rare in headings)
    if "!" in s:
        return False
    return True


def _heading_action(line: str) -> str | None:
    """Classify a heading line as 'keep', 'drop', or None."""
    s = line.strip().rstrip(":").rstrip("?").strip().lower()
    if not s:
        return None
    for pat in _DROP_HEADINGS:
        if pat in s:
            return "drop"
    for pat in _KEEP_HEADINGS:
        if pat in s:
            return "keep"
    return None


def extract_relevant_sections(text: str) -> str:
    """Filter a plain-text JD down to job-relevant sections."""
    if not text:
        return ""

    lines = text.split("\n")

    # Strip standalone boilerplate fragments regardless of section.
    boilerplate_res = [re.compile(p, re.IGNORECASE) for p in _BOILERPLATE_PATTERNS]
    cleaned: list[str] = []
    for ln in lines:
        if any(r.search(ln) for r in boilerplate_res):
            continue
        cleaned.append(ln)
    lines = cleaned

    state = "include"
    out: list[str] = []
    seen_any_heading = False

    for ln in lines:
        if _is_heading_line(ln):
            action = _heading_action(ln)
            if action == "drop":
                state = "skip"
                seen_any_heading = True
                continue
            if action == "keep":
                state = "include"
                seen_any_heading = True
                out.append(ln)
                continue
            # Unknown heading — be lenient: keep including
            state = "include"
            out.append(ln)
            continue

        if state == "include":
            out.append(ln)

    result = "\n".join(out).strip()
    # Defensive: if we lost everything, return original
    if not result and not seen_any_heading:
        return text

    # Collapse extra blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def clean_jd(html: str | None) -> str:
    """Full pipeline: HTML -> plain text -> relevant sections only."""
    if not html:
        return ""
    text = html_to_text(html)
    return extract_relevant_sections(text)