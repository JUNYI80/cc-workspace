"""本机存储：设置、提醒用的历史记录。全部保存在本机 state/ 文件夹，不上传。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
STATE_DIR = PROJECT_DIR / "state"
SETTINGS_FILE = STATE_DIR / "settings.json"
HISTORY_FILE = STATE_DIR / "history.json"

DEFAULT_WATCHLIST = [
    "宇树科技",
    "智谱",
    "月之暗面",
    "长鑫存储",
    "摩尔线程",
]
DEFAULT_SETTINGS = {
    "capital_cny": 100000.0,
    "top_n": 20,
    "regime_filter": True,
    "watchlist": DEFAULT_WATCHLIST,
}


def _read_json(path: Path, default: dict) -> dict:
    try:
        return {**default, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(default)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_settings(path: Path | None = None) -> dict:
    settings = _read_json(path or SETTINGS_FILE, DEFAULT_SETTINGS)
    settings["watchlist"] = list(settings["watchlist"])
    return settings


def save_settings(settings: dict, path: Path | None = None) -> None:
    _write_json(path or SETTINGS_FILE, settings)


def load_history(path: Path | None = None) -> dict:
    return _read_json(path or HISTORY_FILE, {"picks": {}, "stages": {}, "stage_changes": []})


def record_picks(history: dict, month: str, codes: list[str]) -> None:
    """Remember the first list of picks seen in each month."""
    history["picks"].setdefault(month, codes)


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
    """Keep settings and history in another folder (demo mode, tests)."""
    global STATE_DIR, SETTINGS_FILE, HISTORY_FILE
    STATE_DIR = directory
    SETTINGS_FILE = directory / "settings.json"
    HISTORY_FILE = directory / "history.json"
