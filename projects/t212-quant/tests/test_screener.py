from dataclasses import replace

import pandas as pd
import pytest

from t212quant import screener
from t212quant.data import synthetic_prices

SYMS = [f"S{i:02d}" for i in range(40)]


@pytest.fixture(scope="module")
def prices():
    return synthetic_prices(SYMS + [screener.MARKET, screener.CASH], years=8, cash_symbol=screener.CASH)


def universe():
    return screener.Universe(pd.DataFrame({"symbol": SYMS, "name": SYMS, "sector": ""}), "test")


def test_weights_pick_top_n_equally(prices):
    p = screener.ScreenParams(top_n=5, regime_filter=False)
    panel = screener.factor_panel(prices[SYMS], prices[screener.MARKET], p)
    w = screener.weights_on(panel, prices.index[-1], p)
    held = w[w > 0]
    assert 0 < len(held) <= 5
    assert held.tolist() == pytest.approx([0.2] * len(held))
    assert panel.eligible.loc[prices.index[-1], held.index].all()


def test_regime_filter_moves_everything_to_cash(prices):
    p = screener.ScreenParams(top_n=5)
    panel = screener.factor_panel(prices[SYMS], prices[screener.MARKET], p)
    down = panel.market_ok[~panel.market_ok & panel.market_vs_sma.notna()].index
    assert len(down), "synthetic data should contain a bear market"
    assert screener.weights_on(panel, down[0], p).sum() == 0


def test_no_lookahead(prices):
    p = screener.ScreenParams(top_n=5, regime_filter=False)
    d = prices.index[-300]
    before = screener.weights_on(screener.factor_panel(prices[SYMS], prices["SPY"], p), d, p)
    shocked = prices.copy()
    shocked.iloc[-299:] *= 0.2
    after = screener.weights_on(screener.factor_panel(shocked[SYMS], shocked["SPY"], p), d, p)
    assert before.sum() > 0
    pd.testing.assert_series_equal(before, after)


def test_backtest_runs_on_common_dates(prices):
    res = screener.run_backtest(prices, SYMS, screener.ScreenParams(top_n=5))
    assert set(res) == {"Strategy", "S&P 500 (SPY)", "All stocks, equal weight"}
    starts = {r.nav.index[0] for r in res.values()}
    assert len(starts) == 1
    w = res["Strategy"].weights
    assert (w.sum(axis=1) <= 1 + 1e-9).all()


def test_current_picks_are_sorted_and_eligible(prices):
    picks = screener.current_picks(prices, universe(), screener.ScreenParams(top_n=8))
    t = picks.table
    assert len(t) <= 8
    assert list(t["score"]) == sorted(t["score"], reverse=True)
    assert (t["momentum"] > 0).all() and (t["vs_sma"] > 0).all()
    assert picks.next_rebalance > picks.as_of


def test_next_rebalance_skips_weekend():
    # 1 Aug 2026 is a Saturday, so the first trading day is Monday 3 Aug.
    assert screener.next_rebalance_day(pd.Timestamp("2026-07-15")) == pd.Timestamp("2026-08-03")


def test_universe_falls_back_when_offline(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(screener.requests, "get", boom)
    u = screener.sp500_universe()
    assert "内置" in u.source and "AAPL" in u.symbols


def spy_holdings_xlsx(n: int = 420) -> bytes:
    """A file laid out like State Street's real SPY holdings download (checked Nov 2026)."""
    import io

    rows = [["Fund Name:", "State Street SPDR S&P 500 ETF Trust"] + [None] * 6,
            ["Ticker Symbol:", "SPY"] + [None] * 6,
            ["Holdings:", "As of 03-Nov-2026"] + [None] * 6,
            [None] * 8,
            ["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held", "Local Currency"],
            ["NVIDIA CORP", "NVDA", "67066G104", "2379504", 7.66, "-", 298782688, "USD"],
            ["BERKSHIRE HATHAWAY INC CL B", "BRK.B", "084670702", "2073390", 1.6, "-", 1, "USD"]]
    rows += [[f"COMPANY {i} INC", f"T{chr(65 + i % 26)}{chr(65 + i // 26 % 26)}", "x", "y", 0.1, "-", 1, "USD"]
             for i in range(n)]
    rows += [["US DOLLAR", "CASH_USD", "-", "-", 0.02, "-", 1, "USD"],
             ["S&P500 EMINI DEC26", "ESZ6", "-", "-", 0.0, "-", 1, "USD"],
             [None] * 8,
             ["Past performance is not a guarantee of future results."] + [None] * 7]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, header=False, index=False)
    return buf.getvalue()


def test_parse_spy_holdings():
    df, as_of = screener.parse_spy_holdings(spy_holdings_xlsx())
    assert as_of == "03-Nov-2026"
    assert df["symbol"].iloc[:2].tolist() == ["NVDA", "BRK-B"]  # Yahoo uses '-' for share classes
    assert {"CASH_USD", "ESZ6"}.isdisjoint(df["symbol"])  # cash and futures lines are skipped
    assert df["name"].iloc[0] == "Nvidia Corp"


def test_universe_from_official_holdings(monkeypatch):
    class Resp:
        content = spy_holdings_xlsx()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(screener.requests, "get", lambda *a, **k: Resp())
    u = screener.sp500_universe()
    assert "State Street" in u.source and "03-Nov-2026" in u.source
    assert len(u.symbols) >= 400


def test_warnings():
    assert screener.warnings_for(pd.Series({"margin": -0.1, "forward_pe": 20})) == ["亏损中"]
    assert screener.warnings_for(pd.Series({"margin": 0.2, "forward_pe": 75})) == ["估值很高"]
    assert screener.warnings_for(pd.Series({"margin": None, "forward_pe": None})) == []


def test_params_are_frozen():
    p = screener.ScreenParams()
    assert replace(p, top_n=3).top_n == 3 and p.warmup == 253


def test_pinned_takeover_targets_are_excluded():
    idx = pd.bdate_range("2024-01-01", periods=400)
    import numpy as np

    rng = np.random.default_rng(1)
    normal = 100 * np.exp(np.cumsum(0.001 + rng.normal(0, 0.015, 400)))
    pinned = 100 * np.exp(np.cumsum(np.r_[np.full(250, 0.002), np.full(150, 0.0001)]
                                    + rng.normal(0, 0.0005, 400)))
    px = pd.DataFrame({"NORM": normal, "PIN": pinned, "SPY": normal}, index=idx)
    p = screener.ScreenParams(top_n=2, regime_filter=False)
    panel = screener.factor_panel(px[["NORM", "PIN"]], px["SPY"], p)
    assert panel.vol.iloc[-1]["PIN"] < p.min_vol
    assert not panel.eligible.iloc[-1]["PIN"]
