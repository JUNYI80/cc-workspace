"""Performance statistics for daily NAV series."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def drawdown(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1


def summary(nav: pd.Series, rf_daily: pd.Series | None = None) -> dict[str, float]:
    """CAGR, volatility, Sharpe/Sortino (excess over rf), max drawdown, etc."""
    nav = nav.dropna()
    rets = nav.pct_change().dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    rf = rf_daily.reindex(rets.index).fillna(0.0) if rf_daily is not None else 0.0
    excess = rets - rf
    vol = rets.std() * np.sqrt(TRADING_DAYS)
    downside = excess[excess < 0].std() * np.sqrt(TRADING_DAYS)
    sharpe = excess.mean() * TRADING_DAYS / vol if vol > 0 else np.nan
    sortino = excess.mean() * TRADING_DAYS / downside if downside > 0 else np.nan
    dd = drawdown(nav)
    monthly = nav.resample("ME").last().pct_change().dropna()
    return {
        "CAGR": cagr,
        "Volatility": vol,
        "Sharpe": sharpe,
        "Sortino": sortino,
        "Max drawdown": dd.min(),
        "Calmar": cagr / -dd.min() if dd.min() < 0 else np.nan,
        "Worst month": monthly.min() if len(monthly) else np.nan,
        "% months up": (monthly > 0).mean() if len(monthly) else np.nan,
        "Years": years,
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


def format_table(stats: dict[str, dict[str, float]]) -> str:
    pct = {"CAGR", "Volatility", "Max drawdown", "Worst month", "% months up"}
    df = pd.DataFrame(stats)
    lines = []
    width = max(len(c) for c in df.columns) + 2
    lines.append(" " * 14 + "".join(f"{c:>{width}}" for c in df.columns))
    for row in df.index:
        cells = []
        for c in df.columns:
            v = df.loc[row, c]
            cells.append(f"{v:>{width}.1%}" if row in pct else f"{v:>{width}.2f}")
        lines.append(f"{row:<14}" + "".join(cells))
    return "\n".join(lines)
