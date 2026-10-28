"""Study the trend rule on individual stocks, e.g. TSLA and NVDA.

Each stock gets an equal slot. A stock's slot is invested while the stock
trades above its 200-day average and its 3/6/12-month momentum beats cash;
otherwise the slot sits in US T-bills (BIL). Compared against simply holding
the same stocks and against the S&P 500.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import pandas as pd

from . import backtest, metrics
from .config import Asset, Benchmark, Config, ExecutionParams, StrategyParams, validate
from .data import completed_closes
from .strategy import Signal, compute_signal

CASH_PROXY = "BIL"  # SPDR 1-3 month T-bill ETF: stands in for cash in the backtest
MARKET = "SPY"
COST_BPS = 25  # ~0.10% spread + Trading 212's 0.15% FX fee on USD stocks
START_CAPITAL = 10_000


def build_config(symbols: list[str]) -> Config:
    symbols = [s.upper() for s in symbols]
    if CASH_PROXY in symbols:
        raise ValueError(f"{CASH_PROXY} is used as the cash stand-in; pick other symbols")
    assets = tuple(Asset(s, s, isin="", quote_currency="USD") for s in symbols)
    assets += (Asset(CASH_PROXY, "US T-bills", isin="", quote_currency="USD", role="cash"),)
    hold = f"Buy & hold {symbols[0]}" if len(symbols) == 1 else f"Hold {'+'.join(symbols)} equally"
    return validate(Config(
        assets=assets,
        strategy=StrategyParams(top_n=len(symbols), target_vol=None, max_weight=1.0,
                                weighting="equal"),
        execution=ExecutionParams(cost_bps=COST_BPS, cash_buffer=0.0),
        benchmarks=(
            Benchmark(hold, {s: 1 / len(symbols) for s in symbols}),
            Benchmark(f"S&P 500 ({MARKET})", {MARKET: 1.0}),
        ),
        start="2000-01-01",
        account_currency="USD",
    ))


@dataclass
class Study:
    cfg: Config
    prices: pd.DataFrame
    results: dict[str, backtest.BacktestResult]
    signal: Signal


def run(cfg: Config, prices: pd.DataFrame) -> Study:
    results = backtest.run(cfg, prices)
    sig = compute_signal(completed_closes(prices), cfg.risk_symbols, cfg.cash_symbol, cfg.strategy)
    return Study(cfg, prices, results, sig)


def _pad(text: str, width: int, right: bool = True) -> str:
    """Pad to a terminal display width (CJK characters take two columns)."""
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    fill = " " * max(width - w, 0)
    return fill + text if right else text + fill


def _reason(row: pd.Series, cash_score: float) -> str:
    reasons = []
    if not row["vs_sma200"] > 0:
        reasons.append("跌破 200 日均线")
    if not row["score"] > cash_score:
        reasons.append("涨幅不如现金")
    return "，".join(reasons) or "条件不满足"


def format_study(study: Study) -> str:
    cfg, res = study.cfg, study.results
    strat = res["Strategy"]
    nav0 = strat.nav.index[0]
    years = (strat.nav.index[-1] - nav0).days / 365.25
    stocks = cfg.risk_symbols
    names = {"Strategy": "趋势策略"}
    names[cfg.benchmarks[0].name] = "一直持有"
    names[cfg.benchmarks[1].name] = "标普500"

    exposure = strat.weights[stocks].sum(axis=1).mean()
    rows = {
        f"{START_CAPITAL:,} 美元变成": lambda n, r: f"{START_CAPITAL * r.nav.iloc[-1]:,.0f}",
        "年化收益": lambda n, r: f"{metrics.summary(r.nav)['CAGR']:+.1%}",
        "最大回撤": lambda n, r: f"{metrics.drawdown(r.nav).min():.1%}",
        "平均股票仓位": lambda n, r: f"{exposure:.0%}" if n == "Strategy" else "100%",
    }
    col = 14
    lines = [
        f"研究对象：{' + '.join(stocks)}",
        f"回测区间：{nav0.date()} → {strat.nav.index[-1].date()}（{years:.1f} 年），"
        f"按美元计算，每笔交易成本 {COST_BPS / 100:.2f}%",
        "",
        _pad("", 16, right=False) + "".join(_pad(names[n], col) for n in res),
    ]
    for label, fn in rows.items():
        lines.append(_pad(label, 16, right=False) + "".join(_pad(fn(n, r), col) for n, r in res.items()))

    sig = study.signal
    lines += ["", f"当前信号（{sig.as_of.date()} 收盘）："]
    if sig.table.empty:
        lines.append("  历史数据不足一年，无法判断。")
    for s in stocks:
        if sig.table.empty:
            break
        row = sig.table.loc[s]
        cash_score = sig.table.loc[cfg.cash_symbol, "score"]
        cash_score = cash_score if pd.notna(cash_score) else 0.0
        w = sig.weights[s]
        verdict = f"→ 持有，占 {w:.0%}" if w > 0 else f"→ 空仓：{_reason(row, cash_score)}"
        lines.append(
            f"  {s:<6} 比 200 日均线 {row['vs_sma200']:+7.1%}   "
            f"近 3/6/12 月平均涨幅 {row['score']:+7.1%}   {verdict}"
        )
    return "\n".join(lines)
