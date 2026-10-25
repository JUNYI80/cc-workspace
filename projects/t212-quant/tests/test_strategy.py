import numpy as np
import pandas as pd
import pytest
from dataclasses import replace

from conftest import trend_prices
from t212quant.strategy import apply_band, compute_signal, month_end_dates

RISK = ["UP", "UP2", "DOWN"]


def test_picks_uptrends_and_skips_downtrend(params):
    sig = compute_signal(trend_prices(), RISK, "CASH", params)
    w = sig.weights
    assert w["UP"] > 0 and w["UP2"] > 0
    assert w["DOWN"] == 0
    assert w.sum() == pytest.approx(1.0)
    assert (w >= 0).all()


def test_all_cash_when_everything_falls(params):
    prices = trend_prices()
    prices[["UP", "UP2"]] = prices["DOWN"].to_numpy()[:, None] * [1.0, 1.01]
    sig = compute_signal(prices, RISK, "CASH", params)
    assert sig.weights["CASH"] == pytest.approx(1.0)


def test_insufficient_history_is_all_cash(params):
    sig = compute_signal(trend_prices(n=params.warmup - 1), RISK, "CASH", params)
    assert sig.weights["CASH"] == 1.0
    assert sig.table.empty


def test_missing_history_asset_is_ineligible(params):
    prices = trend_prices()
    prices.iloc[:-30, prices.columns.get_loc("UP")] = np.nan  # UP listed only 30 days ago
    sig = compute_signal(prices, RISK, "CASH", params)
    assert sig.weights["UP"] == 0


def test_empty_slots_go_to_cash(params):
    p = replace(params, top_n=3)  # only 2 eligible assets for 3 slots
    w = compute_signal(trend_prices(), RISK, "CASH", p).weights
    assert w["CASH"] == pytest.approx(1 / 3, abs=1e-9)


def test_max_weight_cap(params):
    p = replace(params, top_n=1, max_weight=0.4)
    w = compute_signal(trend_prices(), RISK, "CASH", p).weights
    assert w.drop("CASH").max() == pytest.approx(0.4)
    assert w["CASH"] == pytest.approx(0.6)


def test_vol_target_scales_down(params):
    prices = trend_prices()
    loose = compute_signal(prices, RISK, "CASH", params)
    tight = compute_signal(prices, RISK, "CASH", replace(params, target_vol=0.01))
    assert tight.expected_vol == pytest.approx(0.01)
    assert tight.weights.drop("CASH").sum() < loose.weights.drop("CASH").sum()


def test_no_lookahead(params):
    """Weights decided on day t must not change when later prices change."""
    prices = trend_prices()
    t = 250
    before = compute_signal(prices.iloc[: t + 1], RISK, "CASH", params).weights
    shocked = prices.copy()
    shocked.iloc[t + 1:] *= 0.1
    after = compute_signal(shocked.iloc[: t + 1], RISK, "CASH", params).weights
    pd.testing.assert_series_equal(before, after)


def test_band_skips_small_changes_but_exits_fully():
    cur = pd.Series({"A": 0.30, "B": 0.02, "C": 0.50, "CASH": 0.18})
    tgt = pd.Series({"A": 0.31, "B": 0.0, "C": 0.40, "CASH": 0.29})
    new = apply_band(cur, tgt, band=0.03)
    assert new["A"] == 0.30  # 1pt change skipped
    assert new["B"] == 0.0  # full exit always trades
    assert new["C"] == 0.40
    assert new["CASH"] == 0.29


def test_band_never_overspends():
    cur = pd.Series({"A": 0.52, "B": 0.0, "CASH": 0.48})
    tgt = pd.Series({"A": 0.50, "B": 0.50, "CASH": 0.0})  # A's sale is skipped
    new = apply_band(cur, tgt, band=0.03)
    assert new.sum() == pytest.approx(1.0)
    assert new["A"] == 0.52 and new["B"] == pytest.approx(0.48)


def test_month_end_dates():
    idx = pd.bdate_range("2024-01-01", "2024-03-15")
    me = month_end_dates(idx)
    assert list(me.strftime("%Y-%m-%d")) == ["2024-01-31", "2024-02-29", "2024-03-15"]
