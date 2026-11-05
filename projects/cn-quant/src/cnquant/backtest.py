"""Daily backtest with month-end decisions executed at the next day's close."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

DecideFn = Callable[[pd.Timestamp], pd.Series]


@dataclass
class BacktestResult:
    nav: pd.Series
    weights: pd.DataFrame  # post-trade weights on each execution day
    turnover: pd.Series  # sum of |weight change| on each execution day


def month_end_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Last trading day of each calendar month present in `index`."""
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby(index.to_period("M")).max().to_numpy())


def apply_band(current: pd.Series, target: pd.Series, band: float) -> pd.Series:
    """Move each position to its target only if the change is at least `band`.

    Full exits always trade. The rest (1 - sum) is cash. If skipped sells leave
    too little cash for the buys, the buys are scaled down.
    """
    cur = current.reindex(target.index).fillna(0.0)
    new = cur.copy()
    for a in target.index:
        t = target[a]
        if (t == 0 and cur[a] > 0) or abs(t - cur[a]) >= band:
            new[a] = t
    total = new.sum()
    if total > 1.0 + 1e-12:
        buys = (new - cur).clip(lower=0)
        new -= buys * min(1.0, (total - 1.0) / buys.sum())
    return new


def simulate(
    returns: pd.DataFrame,
    decide: DecideFn,
    first_decision: pd.Timestamp,
    band: float = 0.0,
    buy_cost: float = 0.0,
    sell_cost: float = 0.0,
    cash_rate: float = 0.0,
) -> BacktestResult:
    """Simulate a portfolio rebalanced after each month end.

    `returns` holds daily simple returns (NaN = not trading that day, treated
    as 0). `decide(date)` returns target weights using only data up to that
    month-end close; trades happen at the next day's close. Costs are
    fractions of traded value (buy_cost on purchases, sell_cost on sales,
    e.g. stamp duty only on sales in China). Cash earns `cash_rate` a year.
    """
    cols = list(returns.columns)
    rets = returns.fillna(0.0).to_numpy()
    dates = returns.index
    month_ends = month_end_dates(dates)
    decisions = month_ends[month_ends >= first_decision]
    if decisions.empty:
        raise ValueError("No month end on or after the first decision date")
    start = dates.searchsorted(decisions[0])
    decision_set = set(decisions)
    daily_cash = (1 + cash_rate) ** (1 / 252) - 1

    w = np.zeros(len(cols))
    nav = 1.0
    navs, w_hist, w_dates, turnover = [], [], [], []
    pending = None
    for i in range(start, len(dates)):
        if i > start:
            grown = w * (1 + rets[i])
            cash = (1 - w.sum()) * (1 + daily_cash)
            nav_mult = grown.sum() + cash
            nav *= nav_mult
            w = grown / nav_mult
        if pending is not None:
            target = pending.reindex(cols).fillna(0.0)
            new = apply_band(pd.Series(w, index=cols), target, band).to_numpy()
            delta = new - w
            cost = delta.clip(min=0).sum() * buy_cost + (-delta).clip(min=0).sum() * sell_cost
            nav *= 1 - cost
            w = new
            w_hist.append(w.copy())
            w_dates.append(dates[i])
            turnover.append(np.abs(delta).sum())
            pending = None
        if dates[i] in decision_set:
            pending = decide(dates[i])
        navs.append(nav)

    return BacktestResult(
        nav=pd.Series(navs, index=dates[start:], name="nav"),
        weights=pd.DataFrame(w_hist, index=pd.DatetimeIndex(w_dates), columns=cols),
        turnover=pd.Series(turnover, index=pd.DatetimeIndex(w_dates), name="turnover"),
    )


def drawdown(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1


def summary(nav: pd.Series) -> dict[str, float]:
    nav = nav.dropna()
    rets = nav.pct_change().dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    vol = rets.std() * np.sqrt(252)
    yearly = calendar_returns(nav)
    return {
        "cagr": cagr,
        "vol": vol,
        "sharpe": rets.mean() * 252 / vol if vol > 0 else np.nan,
        "max_drawdown": drawdown(nav).min(),
        "worst_year": yearly.min() if len(yearly) else np.nan,
        "years": years,
    }


def calendar_returns(nav: pd.Series, min_days: int = 20) -> pd.Series:
    """Return per calendar year; a first year with under `min_days` days is dropped."""
    yearly = nav.resample("YE").last()
    rets = yearly.pct_change()
    rets.iloc[0] = yearly.iloc[0] / nav.iloc[0] - 1
    rets.index = rets.index.year
    if (nav.index.year == rets.index[0]).sum() < min_days:
        rets = rets.iloc[1:]
    return rets
