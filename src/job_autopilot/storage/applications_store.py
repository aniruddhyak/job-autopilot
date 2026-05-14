"""Atomic CRUD for application tracking (data/applications.json)."""

from __future__ import annotations

import json
from pathlib import Path

from job_autopilot.models import Application, VALID_STATUSES
from job_autopilot.storage.json_store import (
    _atomic_write_bytes,
    file_lock,
    read_json,
)


# ----------------------------------------------------------------------
# Read
# ----------------------------------------------------------------------

def load_applications(path: Path | str) -> dict[str, Application]:
    """Load applications.json into a dict keyed by job_id.

    Returns ``{}`` if the file doesn't exist. Skips any malformed entries
    rather than failing the whole load.
    """
    raw = read_json(path, default=[])
    if not isinstance(raw, list):
        return {}
    out: dict[str, Application] = {}
    for item in raw:
        try:
            app = Application.model_validate(item)
            out[app.job_id] = app
        except Exception:
            # Skip bad entries; don't break the rest
            continue
    return out


def get_application(path: Path | str, job_id: str) -> Application | None:
    """Read a single application by job_id, or None if not tracked."""
    return load_applications(path).get(job_id)


# ----------------------------------------------------------------------
# Write
# ----------------------------------------------------------------------

def _write_all(path: Path, apps: dict[str, Application]) -> None:
    """Atomic write of the whole apps dict, sorted by updated_at desc."""
    items = sorted(
        apps.values(),
        key=lambda a: a.updated_at,
        reverse=True,
    )
    payload = [a.model_dump(mode="json") for a in items]
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    _atomic_write_bytes(path, text.encode("utf-8"))


def save_application(path: Path | str, app: Application) -> None:
    """Upsert one application into the file. Atomic + file-locked."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(p):
        apps = load_applications(p)
        apps[app.job_id] = app
        _write_all(p, apps)


def delete_application(path: Path | str, job_id: str) -> bool:
    """Remove an application entry. Returns True if it was present."""
    p = Path(path)
    if not p.exists():
        return False
    with file_lock(p):
        apps = load_applications(p)
        if job_id not in apps:
            return False
        del apps[job_id]
        _write_all(p, apps)
        return True


# ----------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------

def compute_status_stats(apps: dict[str, Application]) -> dict[str, int]:
    """Return a count by status, plus a 'total' key.

    Always returns all known status keys, even if zero.
    """
    out: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    out["total"] = 0
    for app in apps.values():
        out[app.status] = out.get(app.status, 0) + 1
        out["total"] += 1
    return out