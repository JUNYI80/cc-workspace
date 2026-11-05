import io

import numpy as np
import pandas as pd
import pytest

from cnquant import backtest, data, factors, screener


def synthetic_market(n_stocks=40, days=900, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=days)
    syms = [f"{600000 + i:06d}.SS" if i % 2 else f"{i:06d}.SZ" for i in range(n_stocks)]
    rets = rng.normal(0.0003, 0.02, (days, n_stocks))
    close = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=idx, columns=syms)
    volume = pd.DataFrame(rng.uniform(1e6, 3e6, (days, n_stocks)), index=idx, columns=syms)
    bench = pd.DataFrame({"沪深300ETF": close.mean(axis=1), "中证500ETF": close.median(axis=1)})
    uni = pd.DataFrame({"code": [s[:6] for s in syms], "name": [f"公司{i}" for i in range(n_stocks)],
                        "yahoo": syms, "index": "沪深300", "board": "主板"})
    return screener.Market(close, volume, bench, uni)


def test_codes_and_boards():
    assert data.to_yahoo("600519") == "600519.SS" and data.to_yahoo("1") == "000001.SZ"
    assert data.board("688981.SS") == "科创板" and data.board("300750.SZ") == "创业板"
    assert data.board("600519.SS") == "主板"
    assert data.is_st("*ST国华") and data.is_st("ST天山") and not data.is_st("贵州茅台")


def test_parse_csi_file_layout():
    """Columns exactly as in the official file downloaded on 2026-11-04."""
    cols = ["日期Date", "指数代码 Index Code", "指数名称 Index Name", "指数英文名称Index Name(Eng)",
            "成份券代码Constituent Code", "成份券名称Constituent Name", "成份券英文名称Constituent Name(Eng)",
            "交易所Exchange", "交易所英文名称Exchange(Eng)"]
    rows = [["20261104", "000300", "沪深300", "CSI 300", "000001", "平安银行", "Ping An Bank", "深圳证券交易所", "SZSE"],
            ["20261104", "000300", "沪深300", "CSI 300", "600519", "贵州茅台", "Kweichow Moutai", "上海证券交易所", "SSE"]]
    buf = io.BytesIO()
    pd.DataFrame(rows, columns=cols).to_excel(buf, index=False)
    df = data.parse_csi_constituents(buf.getvalue())
    assert df.to_dict("records") == [
        {"code": "000001", "name": "平安银行", "yahoo": "000001.SZ"},
        {"code": "600519", "name": "贵州茅台", "yahoo": "600519.SS"},
    ]


def test_adjustment_jump_is_removed():
    s = pd.Series([10.0] * 10 + [5.0] * 5)  # 10送10 not adjusted: -50% overnight
    fixed = data.fix_adjustment_jumps(s)
    assert fixed.pct_change().abs().max() < 1e-12
    assert fixed.iloc[-1] == 5.0  # latest price kept, history rescaled
    ok = pd.Series([10.0, 10.5, 11.0, 10.0, 10.9, 11.5, 12.0, 13.0])
    pd.testing.assert_series_equal(data.fix_adjustment_jumps(ok), ok)  # normal limit moves untouched


def test_incomplete_last_day_dropped():
    idx = pd.bdate_range("2026-10-01", periods=10)
    df = pd.DataFrame({f"S{i}": np.arange(10.0) + i for i in range(8)}, index=idx)
    df.iloc[-1, 2:] = np.nan
    assert data.drop_incomplete_tail(df).index[-1] == idx[-2]


def test_lot_sizes():
    assert screener.lot_size("600519.SS", 1446.0, 100_000) == (0, 0.0)  # 一手要 14.46 万
    assert screener.lot_size("600519.SS", 1446.0, 200_000) == (100, 144_600.0)
    assert screener.lot_size("000001.SZ", 11.74, 5_000) == (400, 4_696.0)
    assert screener.lot_size("688981.SS", 111.3, 30_000) == (269, 269 * 111.3)
    assert screener.lot_size("688981.SS", 111.3, 20_000) == (0, 0.0)  # 科创板至少 200 股


def test_scores_and_weights():
    m = synthetic_market()
    p = factors.Params(top_n=5, regime_filter=False)
    panel = m.panel(p)
    d = m.close.index[-1]
    s = factors.scores_on(panel, d)
    assert list(s["score"]) == sorted(s["score"], reverse=True)
    w = factors.weights_on(panel, d, p)
    assert (w > 0).sum() == 5 and w.sum() == pytest.approx(1.0)


def test_st_stocks_never_picked():
    m = synthetic_market()
    m.universe.loc[0, "name"] = "*ST公司"
    p = factors.Params(top_n=40, regime_filter=False)
    w = factors.weights_on(m.panel(p), m.close.index[-1], p)
    assert w[m.universe.loc[0, "yahoo"]] == 0


def test_no_lookahead():
    m = synthetic_market()
    p = factors.Params(top_n=5, regime_filter=False)
    d = m.close.index[-200]
    before = factors.weights_on(m.panel(p), d, p)
    m.close.iloc[-199:] *= 0.3
    m.volume.iloc[-199:] *= 50
    after = factors.weights_on(m.panel(p), d, p)
    pd.testing.assert_series_equal(before, after)


def test_backtest_runs_and_charges_stamp_duty():
    m = synthetic_market()
    res = screener.run_backtest(m, factors.Params(top_n=5, regime_filter=False))
    assert set(res) == {"策略", "沪深300ETF", "中证500ETF", "成分股等权"}
    assert len({r.nav.index[0] for r in res.values()}) == 1
    rets = pd.DataFrame({"A": [0.0] * 60, "B": [0.0] * 60}, index=pd.bdate_range("2024-01-01", periods=60))
    flip = backtest.simulate(rets, lambda d: pd.Series({"A": 1.0} if d.month % 2 else {"B": 1.0}),
                             rets.index[0], buy_cost=0.001, sell_cost=0.002)
    assert flip.nav.iloc[-1] < 1.0  # only costs move the NAV


def test_picks_skip_loss_makers_and_size_lots():
    m = synthetic_market()
    p = factors.Params(top_n=3, regime_filter=False)
    cands = screener.candidates(m, p)
    f = pd.DataFrame({"pe": [-5.0] + [12.0] * (len(cands) - 1), "margin": 0.1, "sector": "Industrials"},
                     index=cands.index)
    prices = {s: 10.0 for s in cands.index}
    picks = screener.current_picks(m, p, 30_000, f, prices)
    t = picks.table
    assert len(t) == 3 and cands.index[0] not in set(t["yahoo"])
    assert (t["shares"] % 100 == 0).all() and (t["cost"] <= 10_000).all()


def test_regime_filter_goes_to_cash():
    m = synthetic_market()
    m.bench.iloc[-60:] *= 0.5  # market far below its 200-day average
    p = factors.Params(top_n=5, regime_filter=True)
    picks = screener.current_picks(m, p, 100_000)
    assert not picks.market_ok and (picks.table["shares"] == 0).all()


def test_equal_weight_benchmark_invests_even_with_many_stocks():
    m = synthetic_market(n_stocks=200)
    res = screener.run_backtest(m, factors.Params(top_n=5, regime_filter=False))
    assert res["成分股等权"].weights.sum(axis=1).min() > 0.9


def test_factor_weights_change_the_ranking():
    m = synthetic_market()
    panel = m.panel(factors.Params(regime_filter=False))
    d = m.close.index[-1]
    lowvol = factors.scores_on(panel, d, (0, 0, 1))
    assert list(lowvol.index) == list(lowvol.sort_values("volatility").index)
