"""Verify the resume loader. Delete after."""
from pathlib import Path

from job_autopilot.score import Resume, ResumeNotFoundError, load_resume

# 1. Load the real resume
try:
    resume = load_resume("data/resume.md")
except ResumeNotFoundError as e:
    print(f"❌ {e}")
    raise SystemExit(1)

print("=== Resume loaded ===")
print(f"  Path:             {resume.path}")
print(f"  Char count:       {resume.char_count}")
print(f"  Estimated tokens: {resume.estimated_tokens}")
print(f"  SHA-256 (first 12): {resume.sha256[:12]}...")
print(f"  Truncated:        {resume.truncated}")
print()
print("=== First 300 chars ===")
print(resume.text[:300])
print("..." if resume.char_count > 300 else "")
print()

# 2. Cost projection for full scoring run
import json
with open("data/raw_jobs.json", encoding="utf-8") as f:
    jobs = json.load(f)

avg_jd_chars = sum(len(j.get("description_text") or "") for j in jobs) // max(len(jobs), 1)
avg_jd_tokens = avg_jd_chars // 4

# gpt-4o-mini pricing as of 2026: ~$0.15 / 1M input, ~$0.60 / 1M output
# Each scoring call: resume + JD as input, ~300 tokens output
input_per_call = resume.estimated_tokens + avg_jd_tokens + 200  # 200 for prompt overhead
output_per_call = 300
n_jobs = len(jobs)

cost_in = (input_per_call * n_jobs / 1_000_000) * 0.15
cost_out = (output_per_call * n_jobs / 1_000_000) * 0.60
total = cost_in + cost_out

print("=== Cost projection (gpt-4o-mini) ===")
print(f"  Jobs to score:        {n_jobs}")
print(f"  Avg JD tokens:        {avg_jd_tokens}")
print(f"  Resume tokens:        {resume.estimated_tokens}")
print(f"  Tokens per call:      ~{input_per_call} in + ~{output_per_call} out")
print(f"  Total input cost:     ${cost_in:.4f}")
print(f"  Total output cost:    ${cost_out:.4f}")
print(f"  TOTAL for full run:   ${total:.4f}")