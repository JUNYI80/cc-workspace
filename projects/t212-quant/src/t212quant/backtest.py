"""Daily backtest with month-end decisions executed at the next day's close."""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from . import metrics
from .config import Config
from .strategy import apply_band, compute_signal, month_end_dates

DecideFn = Callable[[pd.DataFrame], pd.Series]


@dataclass
class BacktestResult:
    nav: pd.Series
    weights: pd.DataFrame  # post-trade weights on each execution day
    turnover: pd.Series  # sum of |weight change| on each execution day


def simulate(
    prices: pd.DataFrame,
    decide: DecideFn,
    first_decision: pd.Timestamp,
    band: float = 0.0,
    cost_bps: float = 0.0,
    cash_buffer: float = 0.0,
) -> BacktestResult:
    """Simulate a monthly-rebalanced portfolio.

    `decide(history)` sees prices up to and including a month-end close and
    returns target weights. Trades happen at the next day's close, so the
    decision never earns the return of the day it was made on. Uninvested
    weight (1 - sum of weights) earns nothing. Costs are cost_bps per unit of
    traded weight.
    """
    cols = list(prices.columns)
    rets = prices.pct_change().fillna(0.0).to_numpy()
    dates = prices.index
    month_ends = month_end_dates(dates)
    decisions = month_ends[month_ends >= first_decision]
    if decisions.empty:
        raise ValueError("No month end on or after the first decision date")
    start = dates.searchsorted(decisions[0])
    decisions = set(decisions)

    w = np.zeros(len(cols))
    nav = 1.0
    navs, w_hist, w_dates, turnover = [], [], [], []
    pending = None
    for i in range(start, len(dates)):
        if i > start:
            grown = w * (1 + rets[i])
            nav_mult = grown.sum() + (1 - w.sum())
            nav *= nav_mult
            w = grown / nav_mult
        if pending is not None:
            target = pending.reindex(cols).fillna(0.0) * (1 - cash_buffer)
            new = apply_band(pd.Series(w, index=cols), target, band).to_numpy()
            traded = np.abs(new - w).sum()
            nav *= 1 - traded * cost_bps / 1e4
            w = new
            w_hist.append(w.copy())
            w_dates.append(dates[i])
            turnover.append(traded)
            pending = None
        if dates[i] in decisions:
            pending = decide(prices.iloc[: i + 1])
        navs.append(nav)

    return BacktestResult(
        nav=pd.Series(navs, index=dates[start:], name="nav"),
        weights=pd.DataFrame(w_hist, index=pd.DatetimeIndex(w_dates), columns=cols),
        turnover=pd.Series(turnover, index=pd.DatetimeIndex(w_dates), name="turnover"),
    )


def first_decision_date(prices: pd.DataFrame, cfg: Config) -> pd.Timestamp:
    """First month end when every asset has data plus a full warm-up period."""
    common = max(prices[c].first_valid_index() for c in prices.columns)
    i = prices.index.searchsorted(common) + cfg.strategy.warmup
    if i >= len(prices.index):
        raise ValueError("Not enough price history for the warm-up period")
    return prices.index[i]


def strategy_decider(cfg: Config) -> DecideFn:
    risk, cash, params = cfg.risk_symbols, cfg.cash_symbol, cfg.strategy
    return lambda hist: compute_signal(hist, risk, cash, params).weights


def run(cfg: Config, prices: pd.DataFrame) -> dict[str, BacktestResult]:
    """Backtest the strategy and every configured benchmark over the same period."""
    start = first_decision_date(prices[cfg.all_symbols], cfg)
    e = cfg.execution
    results = {
        "Strategy": simulate(
            prices[cfg.strategy_symbols],
            strategy_decider(cfg),
            start,
            band=e.rebalance_band,
            cost_bps=e.cost_bps,
            cash_buffer=e.cash_buffer,
        )
    }
    for b in cfg.benchmarks:
        fixed = pd.Series(b.weights)
        results[b.name] = simulate(
            prices[list(b.weights)], lambda _h, f=fixed: f, start, cost_bps=e.cost_bps
        )
    return results


def report(cfg: Config, prices: pd.DataFrame, results: dict[str, BacktestResult]) -> str:
    rf = prices[cfg.cash_symbol].pct_change()
    stats = {name: metrics.summary(r.nav, rf) for name, r in results.items()}
    strat = results["Strategy"]
    years = stats["Strategy"]["Years"]
    lines = [
        f"Period: {strat.nav.index[0].date()} -> {strat.nav.index[-1].date()} ({years:.1f} years)",
        "Sharpe/Sortino are measured over the cash ETF's return.",
        "",
        metrics.format_table(stats),
        "",
        f"Strategy avg one-way turnover per year: {strat.turnover.sum() / 2 / years:.0%}",
        f"Strategy avg invested in risk assets: "
        f"{strat.weights.drop(columns=cfg.cash_symbol).sum(axis=1).mean():.0%}",
        "",
        "Calendar-year returns:",
    ]
    cal = pd.DataFrame({n: metrics.calendar_returns(r.nav) for n, r in results.items()})
    lines.append(cal.map(lambda v: f"{v:+.1%}").to_string())
    return "\n".join(lines)


SWEEP_GRID = {
    "top_n": [2, 3, 4],
    "lookbacks": [(21, 63, 126, 252), (63, 126, 252), (126, 252)],
    "trend_window": [150, 200, 250],
    "target_vol": [0.10, 0.12, None],
}


def sweep(cfg: Config, prices: pd.DataFrame, grid: dict | None = None) -> pd.DataFrame:
    """Run the strategy over a parameter grid to check the result is not a fluke.

    Every combination uses the same start date (set by the longest warm-up)
    so the rows are comparable.
    """
    grid = grid or SWEEP_GRID
    keys = list(grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*grid.values())]
    longest = max((cfg.with_strategy(**c) for c in combos), key=lambda c: c.strategy.warmup)
    start = first_decision_date(prices[cfg.all_symbols], longest)
    rf = prices[cfg.cash_symbol].pct_change()
    e = cfg.execution
    rows = []
    for combo in combos:
        c = cfg.with_strategy(**combo)
        r = simulate(
            prices[c.strategy_symbols],
            strategy_decider(c),
            start,
            band=e.rebalance_band,
            cost_bps=e.cost_bps,
            cash_buffer=e.cash_buffer,
        )
        s = metrics.summary(r.nav, rf)
        rows.append({**combo, **{k: s[k] for k in ("CAGR", "Volatility", "Sharpe", "Max drawdown")}})
    return pd.DataFrame(rows)
