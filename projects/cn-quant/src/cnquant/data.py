"""A 股数据。

来源：
  * 中证指数有限公司官网：沪深 300、中证 500 的官方成分股文件（每天更新）；
  * Yahoo Finance：A 股日线（已按分红、送转复权的收盘价）和成交量，
    沪深 300 ETF（510300）和中证 500 ETF（510500）作为基准。

价格每天最多下载一次，保存在 data/ 文件夹。
"""
from __future__ import annotations

import io
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_DIR / "data"
CACHE_MAX_AGE_S = 12 * 3600
CSI_CONS_URL = ("https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/"
                "autofile/cons/{code}cons.xls")
INDEXES = {"000300": "沪深300", "000905": "中证500"}
BENCHMARKS = {"沪深300ETF": "510300.SS", "中证500ETF": "510500.SS"}
USER_AGENT = "Mozilla/5.0 (compatible; cnquant personal research app)"
START = "2014-01-01"
# 主板 ±10%、创业板/科创板 ±20%：单日超过 21% 的变动在中证 800 里只可能是复权数据出错。
MAX_DAILY_MOVE = 0.21


def to_yahoo(code: str) -> str:
    """'600519' -> '600519.SS'，'000001' -> '000001.SZ'。"""
    code = str(code).strip().zfill(6)
    return f"{code}.SS" if code.startswith(("6", "9")) else f"{code}.SZ"


def board(code: str) -> str:
    c = str(code)[:6]
    if c.startswith("688"):
        return "科创板"
    if c.startswith(("300", "301")):
        return "创业板"
    return "主板"


# -- official index constituents -------------------------------------------------

def parse_csi_constituents(xls: bytes) -> pd.DataFrame:
    """中证指数公司成分股文件 -> code（6 位）, name, yahoo。"""
    raw = pd.read_excel(io.BytesIO(xls), dtype=str, engine="calamine")
    cols = {c: str(c) for c in raw.columns}
    code_col = next(c for c, s in cols.items() if "成份券代码" in s or "成分券代码" in s)
    name_col = next(c for c, s in cols.items()
                    if ("成份券名称" in s or "成分券名称" in s) and "Eng" not in s)
    df = pd.DataFrame({
        "code": raw[code_col].astype(str).str.strip().str.zfill(6),
        "name": raw[name_col].astype(str).str.strip(),
    })
    df["yahoo"] = df["code"].map(to_yahoo)
    return df.drop_duplicates("code").reset_index(drop=True)


def csi_constituents(index_code: str, timeout: float = 30) -> pd.DataFrame:
    last_exc = None
    for attempt in range(3):
        try:
            resp = requests.get(CSI_CONS_URL.format(code=index_code),
                                headers={"User-Agent": USER_AGENT}, timeout=timeout)
            resp.raise_for_status()
            df = parse_csi_constituents(resp.content)
            df["index"] = INDEXES.get(index_code, index_code)
            return df
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"中证指数官网成分股文件下载失败：{last_exc}")


def universe(cache_dir: Path = CACHE_DIR) -> pd.DataFrame:
    """中证 800（沪深 300 + 中证 500）当前成分股。官网不可用时用最近一次下载的名单。"""
    path = cache_dir / "universe.csv"
    try:
        df = pd.concat([csi_constituents(c) for c in INDEXES], ignore_index=True)
        df = df.drop_duplicates("code").reset_index(drop=True)
        df["board"] = df["code"].map(board)
        cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        return df
    except RuntimeError:
        if path.exists():
            log.warning("Using cached constituents list")
            return pd.read_csv(path, dtype=str)
        raise


def is_st(name: str) -> bool:
    """名称带 ST / *ST 的是被风险警示的股票。"""
    return "ST" in str(name).upper()


# -- prices ----------------------------------------------------------------------------

def download(symbols: list[str], start: str = START) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Yahoo Finance 复权收盘价和成交量。"""
    import yfinance as yf

    raw = yf.download(symbols, start=start, auto_adjust=True, progress=False, threads=True,
                      group_by="column")
    if raw is None or raw.empty:
        raise RuntimeError("Yahoo Finance 没有返回数据")
    close, volume = raw["Close"], raw["Volume"]
    close.index = volume.index = pd.to_datetime(close.index).tz_localize(None)
    keep = [s for s in symbols if s in close.columns and close[s].notna().any()]
    return close[keep].sort_index(), volume[keep].sort_index()


def load_prices(symbols: list[str], start: str = START, cache_dir: Path = CACHE_DIR,
                refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cpath, vpath = cache_dir / "close.csv", cache_dir / "volume.csv"
    fresh = cpath.exists() and time.time() - cpath.stat().st_mtime < CACHE_MAX_AGE_S
    if fresh and not refresh:
        close = pd.read_csv(cpath, index_col=0, parse_dates=True)
        volume = pd.read_csv(vpath, index_col=0, parse_dates=True)
        if set(symbols) <= set(close.columns) | set(_known_missing(cache_dir)):
            have = [s for s in symbols if s in close.columns]
            return clean(close[have], volume[have])
    close, volume = download(symbols, start)
    close.to_csv(cpath)
    volume.to_csv(vpath)
    (cache_dir / "missing.txt").write_text("\n".join(sorted(set(symbols) - set(close.columns))))
    return clean(close, volume)


def _known_missing(cache_dir: Path) -> list[str]:
    path = cache_dir / "missing.txt"
    return path.read_text().split() if path.exists() else []


def clean(close: pd.DataFrame, volume: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """去掉还没发布完整的最后一天，修正复权错误造成的价格跳变。"""
    close = close.dropna(how="all")
    close = drop_incomplete_tail(close)
    volume = volume.reindex(close.index)
    close = close.apply(fix_adjustment_jumps)
    return close, volume


def drop_incomplete_tail(prices: pd.DataFrame, min_share: float = 0.8) -> pd.DataFrame:
    """最新一天只有部分股票有数据、或几乎全部和前一天相同，说明还没发布完整，先不用。"""
    if prices.shape[1] < 5:
        return prices
    counts = prices.notna().sum(axis=1)
    typical = counts.iloc[-30:].median()
    end = len(prices)
    while end > 1 and counts.iloc[end - 1] < min_share * typical:
        end -= 1
    while end > 2:
        last, prev = prices.iloc[end - 1], prices.iloc[end - 2]
        both = last.notna() & prev.notna()
        if both.sum() == 0 or (last[both] == prev[both]).mean() < min_share:
            break
        end -= 1
    return prices.iloc[:end]


def fix_adjustment_jumps(s: pd.Series, limit: float = MAX_DAILY_MOVE) -> pd.Series:
    """单日涨跌超过涨跌停限制的，是送转/分红没复权好：把之前的价格按比例调整，消除跳变。

    上市头几天没有涨跌幅限制，所以每只股票的前 5 个交易日不处理。
    """
    vals = s.to_numpy(dtype=float).copy()
    valid = np.flatnonzero(~np.isnan(vals))
    for k in range(6, len(valid)):
        i, j = valid[k], valid[k - 1]
        ratio = vals[i] / vals[j]
        if not (1 - limit <= ratio <= 1 + limit):
            vals[:i] *= ratio
    return pd.Series(vals, index=s.index, name=s.name)


def load_benchmarks(start: str = START, cache_dir: Path = CACHE_DIR) -> pd.DataFrame:
    path = cache_dir / "benchmarks.csv"
    if path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE_S:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    close, _ = download(list(BENCHMARKS.values()), start)
    df = close.rename(columns={v: k for k, v in BENCHMARKS.items()}).ffill()
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def latest_prices(symbols: list[str]) -> dict[str, float]:
    """最新收盘价（不复权，用来算买多少股）。"""
    import yfinance as yf

    raw = yf.download(symbols, period="10d", auto_adjust=False, progress=False, threads=True)
    close = raw["Close"]
    return {s: float(close[s].dropna().iloc[-1]) for s in symbols
            if s in close.columns and close[s].notna().any()}


def fundamentals(symbols: list[str]) -> pd.DataFrame:
    """当前市盈率、利润率、行业（Yahoo Finance，一只一只查，较慢）。"""
    import yfinance as yf

    rows = []
    for s in symbols:
        try:
            info = yf.Ticker(s).info or {}
        except Exception as exc:
            log.warning("No fundamentals for %s: %s", s, exc)
            info = {}
        rows.append({"yahoo": s, "pe": info.get("trailingPE"), "margin": info.get("profitMargins"),
                     "sector": info.get("sector")})
    return pd.DataFrame(rows).set_index("yahoo")
