"""Settings management — ~/.openheart/settings.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SETTINGS_DIR = Path.home() / ".openheart"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

DEFAULTS = {
    "heartbeat": "HEARTBEAT.md",
    "model": "sonnet",
    "budget": 0.50,
    "dir": ".",
    "allowed_tools": None,
    "quiet_start": 23,
    "quiet_end": 8,
    "interval": 30,
}


def load() -> dict[str, Any]:
    """Load settings, falling back to defaults for missing keys."""
    settings = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            saved = json.load(f)
        settings.update(saved)
    return settings


def save(settings: dict[str, Any]) -> None:
    """Write settings to disk (strips values that match defaults)."""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    # Only persist non-default values
    to_save = {k: v for k, v in settings.items() if v != DEFAULTS.get(k)}
    with open(SETTINGS_FILE, "w") as f:
        json.dump(to_save, f, indent=2)
        f.write("\n")


def resolve(cli_value: Any, key: str, *, is_default: bool) -> Any:
    """Return CLI value if explicitly set, otherwise settings value."""
    if not is_default:
        return cli_value
    return load().get(key, DEFAULTS.get(key))
