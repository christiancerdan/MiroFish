"""Validate record identifiers and keep filesystem access inside configured storage."""

import json
import os
import re
import tempfile
from typing import Any


_STORAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def validate_storage_id(value: Any, field: str = "identifier") -> str:
    """Accept existing generated IDs and simple fixture IDs, never path syntax."""
    if not isinstance(value, str) or not _STORAGE_ID.fullmatch(value):
        raise ValueError(f"Invalid {field}")
    return value


def contained_path(root: str, *parts: str) -> str:
    """Resolve trusted path components and reject existing symlinks outside root.

    The storage tree must remain writable only by the application owner. This
    check does not serialize filesystem mutations by another local process.
    """
    base = os.path.realpath(root)
    path = os.path.realpath(os.path.join(base, *parts))
    if os.path.commonpath((base, path)) != base:
        raise ValueError("Storage path escapes its root")
    return path


def storage_path(root: str, identifier: str, *parts: str) -> str:
    """Return a validated record path without creating directories."""
    validate_storage_id(identifier)
    record_dir = contained_path(root, identifier)
    return contained_path(record_dir, *parts)


def atomic_write_json(path: str, data: Any) -> None:
    """Replace validated JSON storage atomically so readers never see a partial write."""
    fd, temporary = tempfile.mkstemp(prefix=".json-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
