"""Price data: download from Yahoo Finance, cache to disk, clean, or synthesise."""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data"
CACHE_MAX_AGE_S = 12 * 3600


def download_prices(symbols: list[str], start: str) -> pd.DataFrame:
    """Daily dividend-adjusted closes, one column per symbol."""
    import yfinance as yf  # imported lazily so tests run without network deps

    raw = yf.download(symbols, start=start, auto_adjust=True, progress=False, group_by="column")
    if raw is None or raw.empty:
        raise RuntimeError(f"No price data returned for {symbols}")
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    if not isinstance(raw.columns, pd.MultiIndex):
        close.columns = symbols
    missing = [s for s in symbols if s not in close.columns or close[s].dropna().empty]
    if missing:
        raise RuntimeError(f"No price data for {missing}; check the symbols")
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close[symbols].sort_index()


def load_prices(
    symbols: list[str],
    start: str,
    cache_dir: Path = DEFAULT_CACHE,
    refresh: bool = False,
) -> pd.DataFrame:
    """Load prices from the CSV cache, downloading when missing, stale or refresh=True."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(",".join(sorted(symbols)).encode()).hexdigest()[:8]
    path = cache_dir / f"prices_{start}_{key}.csv"
    fresh = path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE_S
    if fresh and not refresh:
        cached = pd.read_csv(path, index_col=0, parse_dates=True)
        if set(symbols) <= set(cached.columns):
            return clean_prices(cached[symbols])
    prices = download_prices(symbols, start)
    prices.to_csv(path)
    return clean_prices(prices)


def completed_closes(prices: pd.DataFrame) -> pd.DataFrame:
    """Drop today's still-moving intraday bar so signals use finished closes only."""
    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    return prices[prices.index < today] if prices.index[-1] >= today else prices


def clean_prices(prices: pd.DataFrame, max_gap: int = 5) -> pd.DataFrame:
    """Fix pence/pound unit glitches, forward-fill short gaps, drop empty rows."""
    out = prices.copy().astype(float)
    for col in out.columns:
        out[col] = _fix_unit_glitches(out[col])
    out = out.ffill(limit=max_gap)
    return out.dropna(how="all")


def _fix_unit_glitches(s: pd.Series) -> pd.Series:
    """Yahoo sometimes quotes LSE lines in GBP instead of GBX for a few days.

    Any day whose price is ~100x (or ~1/100x) the previous valid price gets
    rescaled. Genuine daily moves of that size do not happen for ETFs.
    """
    vals = s.to_numpy(dtype=float).copy()
    prev = np.nan
    fixed = 0
    for i, v in enumerate(vals):
        if np.isnan(v):
            continue
        if not np.isnan(prev):
            ratio = v / prev
            if 30 < ratio < 300:
                vals[i] = v / 100
                fixed += 1
            elif 1 / 300 < ratio < 1 / 30:
                vals[i] = v * 100
                fixed += 1
        prev = vals[i]
    if fixed:
        log.warning("%s: rescaled %d price(s) with a 100x unit glitch", s.name, fixed)
    return pd.Series(vals, index=s.index, name=s.name)


def synthetic_prices(
    symbols: list[str],
    start: str = "2010-01-01",
    years: float = 15,
    seed: int = 7,
    cash_symbol: str | None = None,
) -> pd.DataFrame:
    """Random regime-switching price paths for tests and offline demos.

    The numbers mean nothing about real markets; they only exercise the code.
    """
    rng = np.random.default_rng(seed)
    n = int(years * 252)
    idx = pd.bdate_range(start, periods=n)
    n_assets = len(symbols)
    # Two regimes (calm bull / volatile bear) with persistent switching.
    regime = np.zeros(n, dtype=int)
    for t in range(1, n):
        stay = 0.995 if regime[t - 1] == 0 else 0.98
        regime[t] = regime[t - 1] if rng.random() < stay else 1 - regime[t - 1]
    drift = rng.normal(0.07, 0.04, n_assets) / 252
    bear_drift = rng.normal(-0.25, 0.15, n_assets) / 252
    vol = rng.uniform(0.08, 0.25, n_assets) / np.sqrt(252)
    common = rng.normal(0, 1, n)
    beta = rng.uniform(0.2, 0.9, n_assets)
    eps = rng.normal(0, 1, (n, n_assets))
    shocks = beta * common[:, None] + np.sqrt(1 - beta**2) * eps
    mu = np.where(regime[:, None] == 0, drift, bear_drift)
    sig = np.where(regime[:, None] == 0, vol, vol * 2)
    rets = mu + sig * shocks
    if cash_symbol is not None:
        j = symbols.index(cash_symbol)
        rets[:, j] = 0.02 / 252 + rng.normal(0, 0.0003, n)
    prices = 100 * np.exp(np.cumsum(rets, axis=0))
    return pd.DataFrame(prices, index=idx, columns=symbols)
