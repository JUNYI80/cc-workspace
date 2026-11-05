"""Real-data end-to-end check of the China A-share product (run on GitHub Actions)."""
import sys
import time
import traceback
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, "projects/cn-quant/src")

from cnquant import appstate, backtest, data, ipo, screener  # noqa: E402
from cnquant.factors import Params  # noqa: E402

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


@step("CSI 800 constituents (official CSI files)")
def uni():
    u = data.universe()
    print(len(u), u["index"].value_counts().to_dict(), u["board"].value_counts().to_dict())
    print(u.head(3).to_string())
    assert len(u) >= 790
    return u


@step("Prices + volume for CSI 800 (Yahoo Finance)")
def market():
    close, volume = data.load_prices(list(uni["yahoo"]))
    bench = data.load_benchmarks()
    print("close", close.shape, close.index[0].date(), "->", close.index[-1].date())
    print("bench", bench.shape, bench.tail(2).to_string())
    rets = close.pct_change(fill_method=None)
    # The first 5 trading days after listing have no price limit, so big moves there are real.
    after_debut = rets.apply(lambda s: s.where(s.notna().cumsum() > 6))
    big = after_debut.stack()[after_debut.stack().abs() > data.MAX_DAILY_MOVE]
    debut = rets.stack()[rets.stack().abs() > data.MAX_DAILY_MOVE]
    print("moves beyond limits in first 5 days after listing (real):", len(debut) - len(big))
    print("moves beyond limits afterwards (should be 0):", len(big), big.head(10).to_dict())
    assert len(big) == 0 and len(close.columns) > 780
    return screener.Market(close, volume, bench, uni)


@step("Current picks (with Yahoo PE check and lot sizes)")
def picks():
    p = Params(top_n=20)
    cands = list(screener.candidates(market, p).index)
    f = data.fundamentals(cands)
    print("fundamentals:", f["pe"].notna().sum(), "of", len(f), "have PE; loss-makers:", int((f["pe"] <= 0).sum()))
    prices = data.latest_prices(cands)
    pk = screener.current_picks(market, p, 200_000, f, prices)
    print("as of", pk.as_of.date(), "market ok", pk.market_ok, f"CSI300 ETF vs 200d {pk.market_vs_sma:+.1%}",
          "next", pk.next_rebalance.date())
    print(pk.table[["code", "name", "board", "reversal", "abnormal_turnover", "volatility", "pe", "price",
                    "shares", "cost"]].to_string())
    assert len(pk.table) > 0
    return pk


@step("Factor diagnostics (each factor alone, turnover)")
def diag():
    variants = {
        "reversal only": (1, 0, 0), "low turnover only": (0, 1, 0), "low vol only": (0, 0, 1),
        "turnover + low vol": (0, 1, 1), "all three": (1, 1, 1),
    }
    for regime in (False, True):
        for label, fw in variants.items():
            res = screener.run_backtest(market, Params(top_n=20, regime_filter=regime, factor_weights=fw))
            r = res["策略"]
            s = backtest.summary(r.nav)
            years = s["years"]
            turnover = r.turnover.sum() / 2 / years
            print(f"regime={regime} {label:18s} CAGR {s['cagr']:+.1%} MaxDD {s['max_drawdown']:.0%} "
                  f"Sharpe {s['sharpe']:.2f} turnover/yr {turnover:.0%}")
    b = {k: backtest.summary(v.nav) for k, v in res.items() if k != "策略"}
    print({k: f"{v['cagr']:+.1%}/{v['max_drawdown']:.0%}" for k, v in b.items()})
    res = screener.run_backtest(market, Params(top_n=20, regime_filter=True))
    dd = backtest.drawdown(res["策略"].nav)
    print("worst drawdown trough", dd.idxmin().date(), f"{dd.min():.0%}",
          "peak", res["策略"].nav.loc[:dd.idxmin()].idxmax().date())
    print("invested share by year:", res["策略"].weights.sum(axis=1).groupby(lambda d: d.year).mean().round(2).to_dict())


@step("Backtest + variants")
def bt():
    for top_n in (10, 20, 30):
        for regime in (True, False):
            res = screener.run_backtest(market, Params(top_n=top_n, regime_filter=regime))
            line = []
            for k, r in res.items():
                s = backtest.summary(r.nav)
                line.append(f"{k} {s['cagr']:+.1%}/{s['max_drawdown']:.0%}/{s['sharpe']:.2f}")
            print(f"top{top_n} regime={regime}: " + " | ".join(line))
    res = screener.run_backtest(market, Params())
    cal = pd.DataFrame({k: backtest.calendar_returns(r.nav) for k, r in res.items()})
    print(cal.map(lambda v: f"{v:+.1%}").to_string())


@step("SZSE IPO projects (official)")
def szse():
    recent = ipo.szse_recent()
    print(len(recent))
    for p in recent[:8]:
        print(" ", p.updated, p.short_name, p.status, p.stage, p.amount_yi, p.sponsor)


@step("SSE IPO search (official, may be unreachable from abroad)", core=False)
def sse():
    for name in ("宇树", "摩尔线程", "长鑫"):
        print(name, [(p.company, p.status, p.updated) for p in ipo.sse_search(name)])


@step("cninfo: recent listings and IPO subscriptions")
def cninfo():
    lst = ipo.recent_listings()
    print("recent listings", len(lst))
    print(lst.head(10).to_string())
    subs = ipo.upcoming_subscriptions()
    print("subscriptions (last 10 days)", len(subs))
    print(subs.head(10).to_string())
    assert len(lst) > 0
    return lst


@step("Watchlist stages")
def watch():
    for entry in ["宇树科技", "长鑫存储 | 长鑫科技"] + appstate.DEFAULT_WATCHLIST:
        name = ipo.aliases(entry)[0]
        heads, projects, anns, errors = ipo.gather(entry)
        print("   errors:", errors)
        s = ipo.watch_status(entry, heads, projects, anns)
        print(f"{name}: stage {s.stage} {s.label} | code {s.code} | {s.evidence} | terminated {s.terminated}")
        for h in heads[:3]:
            print(f"    {h.published:%Y-%m-%d} [{h.source}] {h.title}")


@step("Post-listing advice with real prices", core=False)
def advice():
    syms = [data.to_yahoo(c) for c in cninfo["code"]][:10]
    close, _ = data.download(syms, (date.today() - timedelta(days=400)).isoformat())
    for _, r in cninfo.head(10).iterrows():
        sym = data.to_yahoo(r["code"])
        closes = close[sym] if sym in close.columns else None
        a = ipo.post_listing_advice(r["date"], closes)
        print(f"{r['code']} {r['name']} {r['date']}: [{a.level}] {a.text}")


print(f"\nToday {date.today()} | failed core steps: {FAILED or 'none'}")
sys.exit(1 if FAILED else 0)
