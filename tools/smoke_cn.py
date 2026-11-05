"""Real-data end-to-end check of the China A-share product (run on GitHub Actions)."""
import sys
import time
import traceback
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, "projects/cn-quant/src")

from cnquant import backtest, data, ipo, news, screener  # noqa: E402
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
    jumps = (close.pct_change(fill_method=None).abs() > data.MAX_DAILY_MOVE).sum().sum()
    print("daily moves beyond limits after cleaning:", int(jumps))
    assert jumps == 0 and len(close.columns) > 780
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
    for entry in ["宇树科技", "智谱", "月之暗面", "长鑫存储", "摩尔线程"]:
        name = ipo.aliases(entry)[0]
        heads = news.search(f"{name} 上市", limit=30)
        projects = []
        for fn in (ipo.szse_search, ipo.sse_search):
            try:
                projects += fn(name)
            except Exception as exc:
                print("   (", fn.__name__, "failed:", exc, ")")
        anns = ipo.cninfo_search(name, date.today() - timedelta(days=365), date.today())
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
