"""Paths that may live on a persistent disk in managed hosting.

Local Windows and Docker installations keep their original paths.  A managed
single-container host can set ``TOTYSHOP_PERSIST_DIR=/app/persist`` and all
small state files (local database, panel secrets, price-table copy and exports)
will remain together on its attached volume.
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent


def _path_from_env(name: str) -> Path | None:
    value = (os.environ.get(name) or "").strip()
    return Path(value).expanduser() if value else None


def persist_dir() -> Path | None:
    """Optional common mounted volume used by managed hosting."""
    return _path_from_env("TOTYSHOP_PERSIST_DIR")


def local_db_file() -> Path:
    """Local database location, preserving the legacy override and default."""
    configured = _path_from_env("LOCAL_DB_FILE")
    if configured:
        return configured
    root = persist_dir()
    return (root / "local_db.json") if root else (BACKEND_DIR / "data" / "local_db.json")


def secrets_dir() -> Path:
    """Directory for encrypted/provider credentials saved through the panel."""
    configured = _path_from_env("TOTYSHOP_SECRETS_DIR")
    if configured:
        return configured
    root = persist_dir()
    return (root / "secrets") if root else (BACKEND_DIR / ".secrets")


def exports_dir() -> Path:
    """Directory for JohnDrop spreadsheet exports and diagnostics."""
    configured = _path_from_env("TOTYSHOP_EXPORTS_DIR")
    if configured:
        return configured
    root = persist_dir()
    return (root / "exports") if root else (BACKEND_DIR / "exports")


def pricing_data_dir() -> Path:
    """Directory for a local copy of the imported price table."""
    configured = _path_from_env("TOTYSHOP_DATA_DIR")
    if configured:
        return configured
    root = persist_dir()
    return (root / "data") if root else (PROJECT_ROOT / "data")
