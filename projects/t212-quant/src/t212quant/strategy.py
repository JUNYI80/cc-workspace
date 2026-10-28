"""Trend-filtered dual momentum with inverse-volatility sizing and a volatility cap.

At each month end:
  1. Score every risk asset by the mean of its 3, 6 and 12-month returns.
  2. An asset is eligible only if it trades above its 200-day moving average
     AND its score beats the cash asset's score (absolute momentum).
  3. Hold the `top_n` highest-scoring eligible assets. Each holds one of
     `top_n` slots, split by inverse volatility (or equally); empty slots
     go to cash.
  4. Cap each asset at `max_weight` and scale the risk book down if its
     expected volatility exceeds `target_vol`. Whatever is left goes to cash.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import StrategyParams

TRADING_DAYS = 252


@dataclass
class Signal:
    weights: pd.Series  # target weights, sum to 1 (cash asset absorbs the rest)
    table: pd.DataFrame  # per-asset diagnostics
    expected_vol: float  # annualised expected vol of the target portfolio
    as_of: pd.Timestamp | None


def compute_signal(hist: pd.DataFrame, risk: list[str], cash: str, p: StrategyParams) -> Signal:
    """Target weights from price history up to and including the decision day."""
    hist = hist[risk + [cash]]
    weights = pd.Series(0.0, index=risk + [cash])
    as_of = hist.index[-1] if len(hist) else None
    if len(hist) < p.warmup:
        weights[cash] = 1.0
        return Signal(weights, pd.DataFrame(), 0.0, as_of)

    window = hist.iloc[-p.warmup:]
    last = window.iloc[-1]
    score = pd.concat(
        [last / window.iloc[-1 - lb] - 1 for lb in p.lookbacks], axis=1
    ).mean(axis=1, skipna=False)
    sma = window.iloc[-p.trend_window:].mean(skipna=False)
    daily = window.iloc[-(p.vol_window + 1):].pct_change().iloc[1:]
    vol = daily.std() * np.sqrt(TRADING_DAYS)

    cash_score = score[cash] if pd.notna(score[cash]) else 0.0
    above_trend = last > sma
    eligible = score.notna() & above_trend & (score > cash_score) & (vol > 0)
    eligible[cash] = False

    chosen = score[eligible].nlargest(p.top_n).index
    expected_vol = 0.0
    if len(chosen):
        if p.weighting == "equal":
            w = pd.Series(1.0 / p.top_n, index=chosen)
        else:
            inv_vol = 1.0 / vol[chosen]
            w = inv_vol / inv_vol.sum() * len(chosen) / p.top_n
        w = w.clip(upper=p.max_weight)
        cov = daily[chosen].cov() * TRADING_DAYS
        expected_vol = float(np.sqrt(w @ cov @ w))
        if p.target_vol is not None and expected_vol > p.target_vol:
            w *= p.target_vol / expected_vol
            expected_vol = p.target_vol
        weights[chosen] = w
    weights[cash] = 1.0 - weights[risk].sum()

    table = pd.DataFrame(
        {
            "score": score,
            "vs_sma200": last / sma - 1,
            "vol": vol,
            "eligible": eligible.astype(object),
            "weight": weights,
        }
    )
    table.loc[cash, "eligible"] = "cash"
    return Signal(weights, table.sort_values("score", ascending=False), expected_vol, as_of)


def apply_band(current: pd.Series, target: pd.Series, band: float) -> pd.Series:
    """Move each position to its target only if the change is at least `band`.

    Full exits (target 0) always trade. Weights need not sum to 1: the rest is
    uninvested cash. If skipped sells leave too little cash for the buys, the
    buys are scaled down so the total never exceeds 1.
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


def month_end_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Last trading day of each calendar month present in `index`."""
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby(index.to_period("M")).max().to_numpy())
