"""Preferenze utente locali (dinamiche, per-macchina)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .drivers import _app_data_dir


def _prefs_path() -> Path:
    return _app_data_dir() / "prefs.json"


def load_prefs() -> dict[str, Any]:
    path = _prefs_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_prefs(updates: dict[str, Any]) -> None:
    data = load_prefs()
    data.update(updates)
    _prefs_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_device_id_pref(default: int = 0) -> int:
    """0 = ascolta qualsiasi PowerMeter (universale)."""
    raw = load_prefs().get("device_id", default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def set_device_id_pref(device_id: int) -> None:
    save_prefs({"device_id": int(device_id)})
