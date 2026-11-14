"""Every chart must compile in Vega-Lite, in light and dark mode."""
import pandas as pd
import pytest

from conftest import trend_prices
from t212quant import backtest, metrics, ui

vlc = pytest.importorskip("vl_convert")


@pytest.fixture
def specs(monkeypatch):
    out = []
    monkeypatch.setattr(ui, "_show", lambda chart: out.append(ui._style(chart, ui.tokens()).to_dict()))
    return out


def results():
    prices = trend_prices()
    first = prices.index[25]
    return {name: backtest.simulate(prices[[col]], lambda h, c=col: pd.Series({c: 1.0}), first)
            for name, col in [("Strategy", "UP"), ("S&P 500 (SPY)", "UP2"), ("Hold BRK.B", "DOWN")]}


LABELS = {"Strategy": "量化选股", "S&P 500 (SPY)": "标普 500", "Hold BRK.B": "一直持有 BRK.B"}


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_all_charts_compile(specs, monkeypatch, mode):
    monkeypatch.setattr(ui, "mode", lambda: mode)
    res = results()
    ui.performance_charts(res, LABELS)
    ui.yearly_chart(res, LABELS, metrics.calendar_returns)
    ui.hbar(pd.DataFrame({"行业": ["信息技术", "能源"], "只数": [3, 1]}), "行业", "只数", "行业分布")
    ui.hbar(pd.DataFrame({"名称": ["A", "B", "C"], "涨跌": [0.4, -0.2, 0.0]}), "名称", "涨跌", "相对发行价",
            fmt="+.0%", sign_labels=("高于发行价", "低于发行价"))
    assert len(specs) == 5
    for spec in specs:
        assert "<svg" in vlc.vegalite_to_svg(spec)


def test_series_names_are_safe_field_names():
    assert "." not in ui.clean("一直持有 BRK.B")


def test_sparkline_rows_rebase_to_100():
    px = pd.DataFrame({"A": [10.0, 11.0, 12.0], "B": [None, None, None]})
    rows = ui.sparkline_rows(px, ["A", "B", "ZZ"])
    assert rows[0] == [100.0, 110.0, 120.0] and rows[1] == [] and rows[2] == []
