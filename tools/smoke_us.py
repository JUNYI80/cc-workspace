"""Real-data end-to-end check of the US product (run on GitHub Actions).

Runs each pipeline the app uses against live sources and prints what came
back. Exits non-zero if a core pipeline fails, so CI shows red.
"""
import sys
import time
import traceback
from datetime import date

import pandas as pd

sys.path.insert(0, "projects/t212-quant/src")

from t212quant import backtest, data, ipo, metrics, news, research, screener  # noqa: E402
from t212quant.config import load_config  # noqa: E402

FAILED = []
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)


def step(name, core=True):
    def wrap(fn):
        print(f"\n===== {name} =====", flush=True)
        t0 = time.time()
        try:
            out = fn()
            print(f"----- OK {name} ({time.time() - t0:.1f}s)", flush=True)
            return out
        except Exception:
            traceback.print_exc(file=sys.stdout)
            print(f"----- FAIL {name} ({time.time() - t0:.1f}s)", flush=True)
            if core:
                FAILED.append(name)
            return None
    return wrap


@step("S&P 500 universe (State Street SPY holdings)")
def universe():
    u = screener.sp500_universe()
    print(u.source, len(u.symbols), u.symbols[:10])
    assert "State Street" in u.source and len(u.symbols) > 480
    return u


@step("Stock prices (Yahoo Finance, ~500 symbols since 2010)")
def prices():
    cols = universe.symbols + [screener.MARKET, screener.CASH]
    px = data.completed_closes(data.load_prices(cols, "2010-01-01", allow_missing=True))
    missing = sorted(set(cols) - set(px.columns))
    print("shape", px.shape, "last date", px.index[-1].date(), "missing", missing)
    print(px[["AAPL", "MSFT", "NVDA", "SPY", "BIL"]].tail(3))
    assert len(px.columns) > 480 and (pd.Timestamp.today() - px.index[-1]).days < 6
    return px


@step("Current picks")
def picks():
    p = screener.current_picks(prices, universe, screener.ScreenParams(top_n=10))
    print("as of", p.as_of.date(), "market ok", p.market_ok, f"SPY vs 200d {p.market_vs_sma:+.1%}",
          "next rebalance", p.next_rebalance.date())
    print(p.table.to_string())
    assert len(p.table) > 0
    return p


@step("Screener backtest")
def screen_bt():
    res = screener.run_backtest(prices, universe.symbols, screener.ScreenParams(top_n=10))
    for k, r in res.items():
        s = metrics.summary(r.nav)
        print(f"{k:28s} {r.nav.index[0].date()}..{r.nav.index[-1].date()} "
              f"x{r.nav.iloc[-1]:.2f} CAGR {s['CAGR']:+.1%} MaxDD {s['Max drawdown']:.1%} "
              f"Sharpe {s['Sharpe']:.2f}")
    cal = pd.DataFrame({k: metrics.calendar_returns(r.nav) for k, r in res.items()})
    print(cal.map(lambda v: f"{v:+.1%}").to_string())
    return res


@step("Robustness: strategy variants on real data", core=False)
def variants():
    rows = []
    for top_n in (10, 20, 30):
        for regime in (True, False):
            res = screener.run_backtest(prices, universe.symbols,
                                        screener.ScreenParams(top_n=top_n, regime_filter=regime))
            s = metrics.summary(res["Strategy"].nav)
            e = metrics.summary(res["All stocks, equal weight"].nav)
            rows.append({"top_n": top_n, "regime": regime, "CAGR": s["CAGR"], "Sharpe": s["Sharpe"],
                         "MaxDD": s["Max drawdown"], "EW CAGR": e["CAGR"], "EW Sharpe": e["Sharpe"]})
    print(pd.DataFrame(rows).to_string(float_format=lambda v: f"{v:.3f}"))


@step("Fundamentals for picks (Yahoo Finance)", core=False)
def funds():
    f = screener.fundamentals(list(picks.table["symbol"]))
    print(f.to_string())
    return f


@step("Nasdaq IPO calendar")
def calendar():
    cal = ipo.fetch_calendar(months_back=12, months_ahead=1)
    print(cal["status"].value_counts().to_string())
    print(cal.head(8).to_string())
    up = ipo.upcoming_ipos(cal, days=30)
    recent = ipo.recent_ipos(cal)
    print("upcoming 30d:", len(up), "| recent priced >= $50M:", len(recent))
    print(recent.head(10)[["symbol", "company", "date", "ipo_price", "value_usd"]].to_string())
    assert len(cal) > 20
    return cal


@step("Post-IPO advice with real prices", core=False)
def post_ipo():
    recent = ipo.recent_ipos(calendar).head(12)
    start = (pd.Timestamp.today() - pd.DateOffset(days=400)).strftime("%Y-%m-%d")
    px = data.load_prices(list(recent["symbol"]), start, allow_missing=True)
    for _, r in recent.iterrows():
        closes = px[r["symbol"]] if r["symbol"] in px.columns else None
        adv = ipo.post_ipo_advice(r["date"], r["ipo_price"], closes)
        print(f"{r['symbol']:6s} {r['date']} ${r['ipo_price']}: [{adv.level}] {adv.text}")


@step("Watchlist IPO stage (news from reputable outlets)")
def watch():
    for entry in ipo.DEFAULT_WATCHLIST:
        name = ipo.aliases(entry)[0]
        heads = news.search(f'"{name}" IPO', limit=30)
        st = ipo.watch_status(entry, heads, [], calendar)
        print(f"{name}: stage {st.stage} {st.label} | heat {st.heat} | {st.evidence}")
        for h in heads[:3]:
            print(f"    {h.published:%Y-%m-%d} [{h.source}] {h.title}")
    all_heads = news.search('"Anthropic" IPO', limit=50, trusted_only=False)
    print("untrusted sources dropped:", sorted({h.source for h in all_heads if not news.is_trusted(h.source)})[:15])


@step("ETF rotation (LSE ETFs)")
def etf():
    cfg = load_config("projects/t212-quant/config/strategy.yaml")
    px = data.load_prices(cfg.all_symbols, cfg.start)
    print("ETF prices", px.shape, px.index[-1].date())
    res = backtest.run(cfg, px)
    print(backtest.report(cfg, px, res))


@step("Single-stock study NVDA")
def study():
    cfg = research.build_config(["NVDA"])
    s = research.run(cfg, data.load_prices(cfg.all_symbols, cfg.start))
    print(research.format_study(s))


print(f"\nToday {date.today()} | failed core steps: {FAILED or 'none'}")
sys.exit(1 if FAILED else 0)
