"""Persist small secrets on disk so local restarts keep Bling/JohnDrop connected."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

_DIR = Path(__file__).resolve().parent / ".secrets"


def read(name: str) -> Optional[dict[str, Any]]:
    path = _DIR / f"{name}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write(name: str, data: dict[str, Any]) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    path = _DIR / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def delete(name: str) -> None:
    path = _DIR / f"{name}.json"
    if path.exists():
        path.unlink()
