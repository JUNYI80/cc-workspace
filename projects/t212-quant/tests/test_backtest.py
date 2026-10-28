import pandas as pd
import pytest

from conftest import trend_prices
from t212quant import backtest, metrics
from t212quant.config import Benchmark, load_config
from t212quant.data import clean_prices, synthetic_prices


def test_buy_and_hold_matches_asset_return():
    prices = trend_prices()
    first = prices.index[25]
    r = backtest.simulate(prices[["UP"]], lambda h: pd.Series({"UP": 1.0}), first)
    exec_day = r.weights.index[0]
    # Invested from the close of the execution day onward.
    expected = prices["UP"].iloc[-1] / prices.loc[exec_day, "UP"]
    assert r.nav.iloc[-1] == pytest.approx(expected)


def test_trades_execute_the_day_after_the_decision():
    prices = trend_prices()
    r = backtest.simulate(prices[["UP"]], lambda h: pd.Series({"UP": 1.0}), prices.index[0])
    month_end = prices.index[prices.index.to_period("M") == prices.index[0].to_period("M")][-1]
    nxt = prices.index[prices.index.get_loc(month_end) + 1]
    assert r.weights.index[0] == nxt
    assert r.nav.loc[:nxt].eq(1.0).all()  # no return earned before execution


def test_costs_reduce_returns():
    prices = trend_prices()
    flip = lambda h: pd.Series({"UP": 1.0} if len(h) % 2 else {"UP2": 1.0})  # noqa: E731
    cheap = backtest.simulate(prices[["UP", "UP2"]], flip, prices.index[0], cost_bps=0)
    dear = backtest.simulate(prices[["UP", "UP2"]], flip, prices.index[0], cost_bps=50)
    assert dear.nav.iloc[-1] < cheap.nav.iloc[-1]


def test_cash_buffer_stays_uninvested():
    prices = trend_prices()
    r = backtest.simulate(prices[["UP"]], lambda h: pd.Series({"UP": 1.0}), prices.index[0],
                          cash_buffer=0.1)
    assert r.weights["UP"].iloc[0] == pytest.approx(0.9)


def test_full_run_on_synthetic_data():
    cfg = load_config()
    prices = synthetic_prices(cfg.all_symbols, years=6, cash_symbol=cfg.cash_symbol)
    results = backtest.run(cfg, prices)
    assert set(results) == {"Strategy", *(b.name for b in cfg.benchmarks)}
    navs = [r.nav for r in results.values()]
    assert all(n.index[0] == navs[0].index[0] for n in navs)  # same start for fair comparison
    w = results["Strategy"].weights
    assert (w.sum(axis=1) <= 1 + 1e-9).all() and (w >= -1e-12).all().all()
    assert "CAGR" in backtest.report(cfg, prices, results)


def test_sweep_runs():
    cfg = load_config()
    prices = synthetic_prices(cfg.all_symbols, years=5, cash_symbol=cfg.cash_symbol)
    df = backtest.sweep(cfg, prices, grid={"top_n": [2, 3], "target_vol": [None, 0.1]})
    assert len(df) == 4 and df["CAGR"].notna().all()


def test_metrics_on_known_series():
    idx = pd.bdate_range("2020-01-01", periods=253)
    nav = pd.Series([1.0] * 100 + [0.8] * 53 + [1.2] * 100, index=idx)
    s = metrics.summary(nav)
    assert s["Max drawdown"] == pytest.approx(-0.2)


def test_unit_glitch_is_fixed():
    idx = pd.bdate_range("2024-01-01", periods=6)
    raw = pd.DataFrame({"X.L": [7000, 7010, 70.2, 70.3, 7040, 7050.0]}, index=idx)
    fixed = clean_prices(raw)["X.L"]
    assert fixed.max() / fixed.min() < 1.01


def test_benchmark_weights_validated():
    from t212quant.config import validate

    cfg = load_config()
    bad = cfg.__class__(assets=cfg.assets, benchmarks=(Benchmark("x", {"A": 0.5}),))
    with pytest.raises(ValueError):
        validate(bad)


def test_download_parses_yfinance_frame(monkeypatch, tmp_path):
    import yfinance

    from t212quant import data

    idx = pd.date_range("2024-01-02", periods=3, tz="Europe/London")
    cols = pd.MultiIndex.from_product([["Close", "Volume"], ["A.L", "B.L"]], names=["Price", "Ticker"])
    frame = pd.DataFrame([[1.0, 2.0, 10, 20], [1.1, 2.1, 10, 20], [1.2, 2.2, 10, 20]],
                         index=idx, columns=cols)
    calls = []
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: calls.append(1) or frame)
    out = data.load_prices(["B.L", "A.L"], "2024-01-01", cache_dir=tmp_path)
    assert list(out.columns) == ["B.L", "A.L"] and out.index.tz is None
    assert out["A.L"].iloc[-1] == 1.2
    data.load_prices(["A.L", "B.L"], "2024-01-01", cache_dir=tmp_path)  # same set: from cache
    assert len(calls) == 1
