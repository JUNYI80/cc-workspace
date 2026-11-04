"""Local storage for the app: settings, API keys (.env) and history used for alerts."""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from .ipo import DEFAULT_WATCHLIST

PROJECT_DIR = Path(__file__).resolve().parents[2]
STATE_DIR = PROJECT_DIR / "state"
SETTINGS_FILE = STATE_DIR / "settings.json"
HISTORY_FILE = STATE_DIR / "history.json"
ENV_FILE = PROJECT_DIR / ".env"

DEFAULT_SETTINGS = {
    "capital_gbp": 1000.0,
    "top_n": 10,
    "watchlist": DEFAULT_WATCHLIST,
    "sec_contact": "",
}
ENV_KEYS = ["T212_API_KEY_DEMO", "T212_API_SECRET_DEMO", "T212_API_KEY_LIVE",
            "T212_API_SECRET_LIVE", "T212_ALLOW_LIVE"]


def _read_json(path: Path, default: dict) -> dict:
    try:
        return {**default, **json.loads(path.read_text())}
    except (OSError, ValueError):
        return dict(default)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def load_settings(path: Path | None = None) -> dict:
    settings = _read_json(path or SETTINGS_FILE, DEFAULT_SETTINGS)
    settings["watchlist"] = list(settings["watchlist"])
    return settings


def save_settings(settings: dict, path: Path | None = None) -> None:
    _write_json(path or SETTINGS_FILE, settings)


# -- API keys in .env ------------------------------------------------------------

def read_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_FILE
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def load_env_file(path: Path | None = None) -> None:
    """Put keys from .env into the environment (values already set there win)."""
    for k, v in read_env_file(path).items():
        if v and not os.environ.get(k):
            os.environ[k] = v


def save_env_values(updates: dict[str, str], path: Path | None = None) -> None:
    """Update keys in .env, keeping other lines, and apply them to this process."""
    path = path or ENV_FILE
    values = {**read_env_file(path), **updates}
    lines = ["# Trading 212 API keys. Keep this file private; never commit or share it."]
    lines += [f"{k}={values.get(k, '')}" for k in ENV_KEYS]
    lines += [f"{k}={v}" for k, v in values.items() if k not in ENV_KEYS]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    for k, v in updates.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


# -- history (what changed since last time) -----------------------------------------

def load_history(path: Path | None = None) -> dict:
    return _read_json(path or HISTORY_FILE, {"picks": {}, "stages": {}, "stage_changes": []})


def record_picks(history: dict, month: str, symbols: list[str]) -> None:
    """Remember the first list of picks seen in each month."""
    history["picks"].setdefault(month, symbols)


def record_stage(history: dict, name: str, stage: int, today: date | None = None) -> None:
    today = today or date.today()
    old = history["stages"].get(name)
    if old is not None and stage > old:
        history["stage_changes"].append(
            {"name": name, "from": old, "to": stage, "date": today.isoformat()})
    history["stages"][name] = stage


def save_history(history: dict, path: Path | None = None) -> None:
    history["stage_changes"] = history["stage_changes"][-50:]
    _write_json(path or HISTORY_FILE, history)


def use_state_dir(directory: Path) -> None:
    """Keep settings, history and keys in another folder (demo mode, tests)."""
    global STATE_DIR, SETTINGS_FILE, HISTORY_FILE, ENV_FILE
    STATE_DIR = directory
    SETTINGS_FILE = directory / "settings.json"
    HISTORY_FILE = directory / "history.json"
    ENV_FILE = directory / ".env"
