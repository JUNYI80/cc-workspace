"""Smoke test: the app's main page runs in demo mode without raising."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "src" / "t212quant" / "app.py"


def test_overview_runs_in_demo_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("T212Q_DEMO", "1")
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    at = AppTest.from_file(str(APP), default_timeout=300)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value == "今日概览"
    assert any("演示模式" in w.value for w in at.warning)
    assert (tmp_path / "t212q-demo-state" / "history.json").exists()  # never the real state dir
