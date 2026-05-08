"""Atomic JSON read/write helpers with file locking.

This is the single source of truth for filesystem JSON I/O in Job Autopilot.
Every other module reads/writes JSON via this module — never directly.

Key guarantees:
    - Atomic writes (temp file + rename) — files never end up half-written.
    - File locking (filelock) — multiple processes can't corrupt each other.
    - Pretty-printed output (indent=2) for human-readable diffs.
    - Validates against Pydantic models on read/write when one is provided.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from filelock import FileLock
from pydantic import BaseModel

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

DEFAULT_INDENT = 2
"""Indentation for pretty-printed JSON output."""

DEFAULT_LOCK_TIMEOUT = 30.0
"""Seconds to wait for a file lock before giving up."""

T = TypeVar("T", bound=BaseModel)
"""Generic type bound to Pydantic BaseModel — used for typed reads."""


# ----------------------------------------------------------------------
# Lock helpers
# ----------------------------------------------------------------------

def _lock_path(path: Path) -> Path:
    """Return the .lock sibling path for a given JSON file."""
    return path.with_suffix(path.suffix + ".lock")


@contextmanager
def file_lock(path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT):
    """Acquire an exclusive file lock for the given path.

    Usage:
        with file_lock(Path("data/raw_jobs.json")):
            ... # safe read+modify+write here

    The lock is held until the `with` block exits, even on exception.
    """
    lock = FileLock(str(_lock_path(path)), timeout=timeout)
    with lock:
        yield


# ----------------------------------------------------------------------
# Read helpers
# ----------------------------------------------------------------------

def read_json(path: Path | str, default: Any = None) -> Any:
    """Read a JSON file. Returns `default` if the file is missing or empty.

    Raises ``json.JSONDecodeError`` if the file exists but is malformed.
    Use ``default=[]`` for list-shaped data, ``default={}`` for objects.
    """
    p = Path(path)
    if not p.exists():
        return default

    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return default

    return json.loads(text)


def read_json_as(path: Path | str, model: type[T], default_factory=None) -> T:
    """Read a JSON file and validate it against a Pydantic model.

    Example:
        cfg = read_json_as("config/sources.json", SourcesConfig,
                           default_factory=SourcesConfig)
    """
    raw = read_json(path, default=None)
    if raw is None:
        if default_factory is None:
            raise FileNotFoundError(f"{path} not found and no default_factory provided")
        return default_factory()
    return model.model_validate(raw)


def read_json_list_as(path: Path | str, model: type[T]) -> list[T]:
    """Read a JSON file expected to be a list, validate each item.

    Returns ``[]`` if the file is missing or empty.
    """
    raw = read_json(path, default=[])
    if not isinstance(raw, list):
        raise ValueError(f"{path} did not contain a JSON array")
    return [model.model_validate(item) for item in raw]


# ----------------------------------------------------------------------
# Write helpers (atomic)
# ----------------------------------------------------------------------

def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to ``path`` atomically.

    Strategy:
        1. Write to a temp file in the same directory.
        2. Flush + fsync to ensure data is on disk.
        3. Rename (os.replace) onto the target — atomic on all platforms.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # NamedTemporaryFile in same dir => same filesystem => rename is atomic
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # ensure bytes hit disk before rename
        os.replace(tmp_path, path)  # atomic on POSIX and Windows
    except Exception:
        # Clean up temp on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_json(path: Path | str, data: Any, indent: int = DEFAULT_INDENT) -> None:
    """Atomically write JSON-serializable data to a file.

    `data` may be any JSON-serializable Python object (dict, list, str, ...).
    Pydantic models should be converted via ``.model_dump(mode="json")`` first,
    or use ``write_model`` / ``write_models`` instead.
    """
    path = Path(path)
    payload = json.dumps(data, indent=indent, ensure_ascii=False, default=str)
    with file_lock(path):
        _atomic_write_bytes(path, payload.encode("utf-8"))


def write_model(path: Path | str, model: BaseModel, indent: int = DEFAULT_INDENT) -> None:
    """Atomically write a single Pydantic model as JSON."""
    payload = model.model_dump_json(indent=indent)
    path = Path(path)
    with file_lock(path):
        _atomic_write_bytes(path, payload.encode("utf-8"))


def write_models(
    path: Path | str,
    models: Iterable[BaseModel],
    indent: int = DEFAULT_INDENT,
) -> None:
    """Atomically write a list of Pydantic models as a JSON array."""
    items = [m.model_dump(mode="json") for m in models]
    write_json(path, items, indent=indent)


# ----------------------------------------------------------------------
# Upsert (read-modify-write under lock) for list-of-objects files
# ----------------------------------------------------------------------

def upsert_models_by_id(
    path: Path | str,
    new_items: Iterable[BaseModel],
    model: type[T],
    id_field: str = "id",
) -> tuple[int, int]:
    """Merge new items into an existing JSON list, deduplicating by ``id_field``.

    - Existing items are kept.
    - New items with an existing ID overwrite the old entry.
    - New items with a new ID are appended.

    Returns a tuple ``(added_count, updated_count)``.

    All file I/O happens under a single lock — safe for concurrent runs.
    """
    path = Path(path)
    new_list = list(new_items)

    with file_lock(path):
        existing: list[T] = []
        raw = read_json(path, default=[])
        if isinstance(raw, list):
            existing = [model.model_validate(item) for item in raw]

        # Index existing by ID
        index: dict[str, int] = {
            getattr(item, id_field): i for i, item in enumerate(existing)
        }

        added = 0
        updated = 0
        for new in new_list:
            key = getattr(new, id_field)
            if key in index:
                existing[index[key]] = new
                updated += 1
            else:
                index[key] = len(existing)
                existing.append(new)
                added += 1

        payload = json.dumps(
            [m.model_dump(mode="json") for m in existing],
            indent=DEFAULT_INDENT,
            ensure_ascii=False,
            default=str,
        )
        _atomic_write_bytes(path, payload.encode("utf-8"))

    return added, updated