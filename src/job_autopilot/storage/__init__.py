"""Storage layer — atomic JSON I/O with file locking."""

from job_autopilot.storage.applications_store import (
    compute_status_stats,
    delete_application,
    get_application,
    load_applications,
    save_application,
)
from job_autopilot.storage.json_store import (
    file_lock,
    read_json,
    read_json_as,
    read_json_list_as,
    upsert_models_by_id,
    write_json,
    write_model,
    write_models,
)

__all__ = [
    "compute_status_stats",
    "delete_application",
    "file_lock",
    "get_application",
    "load_applications",
    "read_json",
    "read_json_as",
    "read_json_list_as",
    "save_application",
    "upsert_models_by_id",
    "write_json",
    "write_model",
    "write_models",
]