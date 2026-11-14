"""Every chart must compile in Vega-Lite, in light and dark mode."""
import numpy as np
import pandas as pd
import pytest

from cnquant import backtest, ui

vlc = pytest.importorskip("vl_convert")


@pytest.fixture
def specs(monkeypatch):
    out = []
    monkeypatch.setattr(ui, "_show", lambda chart: out.append(ui._style(chart, ui.tokens()).to_dict()))
    return out


def results():
    idx = pd.bdate_range("2023-01-02", periods=500)
    rng = np.random.default_rng(0)
    rets = pd.DataFrame(rng.normal(0.0004, 0.01, (500, 4)), index=idx, columns=list("ABCD"))
    return {name: backtest.simulate(rets[[c]], lambda d, c=c: pd.Series({c: 1.0}), idx[0])
            for name, c in [("策略", "A"), ("沪深300ETF", "B"), ("中证500ETF", "C"), ("成分股等权", "D")]}


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_all_charts_compile(specs, monkeypatch, mode):
    monkeypatch.setattr(ui, "mode", lambda: mode)
    res = results()
    labels = {k: k for k in res}
    ui.performance_charts(res, labels)
    ui.yearly_chart(res, labels, backtest.calendar_returns)
    ui.hbar(pd.DataFrame({"板块": ["主板", "科创板"], "只数": [18, 2]}), "板块", "只数", "板块分布")
    ui.hbar(pd.DataFrame({"标签": ["A", "B"], "涨跌": [0.4, -0.2]}), "标签", "涨跌", "相对首日收盘",
            fmt="+.0%", sign_labels=("高于首日收盘", "低于首日收盘"))
    assert len(specs) == 5
    for spec in specs:
        assert "<svg" in vlc.vegalite_to_svg(spec)
