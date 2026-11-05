import os
from datetime import date

import pandas as pd
import pytest

from t212quant import alerts, appstate, ipo


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(appstate, "STATE_DIR", tmp_path)
    monkeypatch.setattr(appstate, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(appstate, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(appstate, "ENV_FILE", tmp_path / ".env")
    for k in appstate.ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_settings_roundtrip_and_defaults():
    s = appstate.load_settings()
    assert s["top_n"] == 20 and s["regime_filter"] is False and "Anthropic" in s["watchlist"]
    s["watchlist"].append("X")  # must not leak into the defaults
    assert "X" not in appstate.load_settings()["watchlist"]
    s["top_n"] = 7
    appstate.save_settings(s)
    assert appstate.load_settings()["top_n"] == 7


def test_env_file_roundtrip(isolated_state):
    appstate.save_env_values({"T212_API_KEY_DEMO": "abc", "T212_ALLOW_LIVE": "no", "OTHER": "1"})
    assert os.environ["T212_API_KEY_DEMO"] == "abc"
    values = appstate.read_env_file()
    assert values["T212_API_KEY_DEMO"] == "abc" and values["OTHER"] == "1"
    appstate.save_env_values({"T212_API_KEY_DEMO": ""})
    assert "T212_API_KEY_DEMO" not in os.environ
    assert appstate.read_env_file()["OTHER"] == "1"  # untouched keys survive


def test_stage_changes_are_logged():
    h = appstate.load_history()
    appstate.record_stage(h, "Anthropic", 1, date(2026, 10, 1))
    appstate.record_stage(h, "Anthropic", 1, date(2026, 10, 2))
    appstate.record_stage(h, "Anthropic", 3, date(2026, 11, 3))
    assert h["stage_changes"] == [{"name": "Anthropic", "from": 1, "to": 3, "date": "2026-11-03"}]
    appstate.save_history(h)
    assert appstate.load_history()["stages"]["Anthropic"] == 3


def test_first_picks_of_the_month_are_kept():
    h = appstate.load_history()
    appstate.record_picks(h, "2026-11", ["A", "B"])
    appstate.record_picks(h, "2026-11", ["C"])
    assert h["picks"]["2026-11"] == ["A", "B"]


def test_rebalance_window_is_first_three_weekdays():
    # November 2026 starts on a Sunday.
    assert not alerts.is_rebalance_window(date(2026, 11, 1))
    assert alerts.is_rebalance_window(date(2026, 11, 2))
    assert alerts.is_rebalance_window(date(2026, 11, 4))
    assert not alerts.is_rebalance_window(date(2026, 11, 5))


def test_picks_alert_lists_buys_and_sells():
    h = {"picks": {"2026-10": ["AAPL", "MSFT"]}}
    out = alerts.picks(["MSFT", "NVDA"], h, "2026-11", date(2026, 11, 20), market_ok=True)
    assert len(out) == 1 and "买入 NVDA" in out[0].text and "卖出 AAPL" in out[0].text
    on_day = alerts.picks(["MSFT"], h, "2026-11", date(2026, 11, 2), market_ok=False)
    assert any("持有现金" in a.text for a in on_day)


def test_market_alert_levels():
    assert alerts.market(0.05).level == "ok"
    assert alerts.market(-0.02).level == "warn"


def test_watchlist_alerts_show_recent_changes_only():
    h = {"stage_changes": [{"name": "A", "from": 1, "to": 2, "date": "2026-11-01"},
                           {"name": "B", "from": 0, "to": 1, "date": "2026-09-01"}]}
    out = alerts.watchlist([], h, date(2026, 11, 4))
    assert len(out) == 1 and "A 上市进度更新" in out[0].text


def test_lockup_alert():
    recent = pd.DataFrame([{"company": "Acme", "symbol": "ACME", "date": date(2026, 5, 20)}])
    out = alerts.ipo_calendar(pd.DataFrame(columns=["date"]), recent, date(2026, 11, 4))
    assert len(out) == 1 and "锁定期" in out[0].text


def test_sort_puts_warnings_first():
    a = [alerts.Alert("info", "x", "1"), alerts.Alert("warn", "x", "2"), alerts.Alert("ok", "x", "3")]
    assert [x.level for x in alerts.sort(a)] == ["warn", "ok", "info"]


def test_lockup_days_constant():
    assert ipo.LOCKUP_DAYS == 180
