"""A-class: rank the whole S&P 500 by a momentum factor, monthly.

The factor is the one systematic equity funds have used for decades:
12-month return skipping the latest month ("12-1 momentum", Jegadeesh &
Titman 1993), divided by volatility (Barroso & Santa-Clara 2015), so steady
winners rank above lottery tickets. Three filters on top:

  * the stock trades above its 200-day average (trend filter);
  * its 12-1 return is positive;
  * its volatility is at least 10% a year: lower than that almost always
    means a takeover has been agreed and the price is pinned to the offer;
  * optionally, the market itself (SPY) trades above its 200-day average;
    when it does not, the whole portfolio goes to cash (BIL). Off by default:
    on 2011-2026 data it cut the worst drawdown only a little (about -45% to
    -38%) while costing 6-10 points of yearly return for every portfolio size.

Hold the top N equally weighted, rebalance monthly.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
import requests

from . import backtest
from .backtest import BacktestResult

log = logging.getLogger(__name__)

# Official daily holdings file of the SPDR S&P 500 ETF (SPY), published by State Street.
SPY_HOLDINGS_URL = ("https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/"
                    "etfs/us/holdings-daily-us-en-spy.xlsx")
USER_AGENT = "Mozilla/5.0 (compatible; t212quant personal research app)"
MARKET = "SPY"
CASH = "BIL"
TRADING_DAYS = 252

# Used when the S&P 500 list can't be fetched: about 100 of the largest members.
FALLBACK_UNIVERSE = """
AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK-B JPM LLY V UNH XOM MA COST HD PG JNJ WMT
NFLX ABBV BAC CRM ORCL CVX KO MRK AMD PEP TMO ADBE LIN WFC ACN MCD CSCO ABT IBM GE PM
DHR TXN INTU QCOM CAT VZ AMGN ISRG NOW GS MS AXP DIS BKNG SPGI UBER PFE RTX T NEE LOW
HON UNP BLK SYK PGR ETN TJX COP BSX C ADP DE MDT LMT ANET PANW SCHW CB MU BMY GILD ADI
PLD AMAT LRCX KLAC SBUX ICE CMG MO SO DUK SHW ZTS CI ELV APH CRWD PLTR
""".split()

SECTORS_ZH = {
    "Information Technology": "信息技术", "Technology": "信息技术",
    "Health Care": "医疗保健", "Healthcare": "医疗保健",
    "Financials": "金融", "Financial Services": "金融",
    "Consumer Discretionary": "可选消费", "Consumer Cyclical": "可选消费",
    "Communication Services": "通信服务",
    "Industrials": "工业",
    "Consumer Staples": "日常消费", "Consumer Defensive": "日常消费",
    "Energy": "能源",
    "Utilities": "公用事业",
    "Real Estate": "房地产",
    "Materials": "原材料", "Basic Materials": "原材料",
}


@dataclass(frozen=True)
class ScreenParams:
    top_n: int = 20
    mom_lookback: int = 252
    mom_skip: int = 21
    vol_window: int = 126
    trend_window: int = 200
    regime_filter: bool = False
    cost_bps: float = 25.0  # ~0.10% spread + Trading 212's 0.15% FX fee on US stocks
    rebalance_band: float = 0.02
    min_vol: float = 0.10  # below this the stock is usually a pinned takeover target

    @property
    def warmup(self) -> int:
        return max(self.mom_lookback, self.trend_window, self.vol_window) + 1


@dataclass
class Universe:
    table: pd.DataFrame  # columns: symbol, name, sector
    source: str

    @property
    def symbols(self) -> list[str]:
        return list(self.table["symbol"])


def parse_spy_holdings(xlsx: bytes) -> tuple[pd.DataFrame, str]:
    """Stocks in State Street's SPY holdings file, and the file's "as of" date text."""
    raw = pd.read_excel(io.BytesIO(xlsx), header=None, engine="openpyxl")
    as_of = ""
    for v in raw.iloc[:6, 1]:
        if isinstance(v, str) and v.lower().startswith("as of"):
            as_of = v[5:].strip()
    first_col = raw.iloc[:, 0].map(lambda v: str(v).strip() if isinstance(v, str) else "")
    header = raw.index[first_col.eq("Name")][0]
    table = raw.iloc[header + 1:].copy()
    table.columns = [str(c).strip() for c in raw.iloc[header]]
    tickers = table["Ticker"].map(lambda v: v.strip() if isinstance(v, str) else "")
    stocks = table[tickers.str.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?") &
                   pd.to_numeric(table["Weight"], errors="coerce").gt(0)]
    df = pd.DataFrame({
        "symbol": stocks["Ticker"].str.strip().str.replace(".", "-", regex=False),  # Yahoo uses BRK-B
        "name": stocks["Name"].astype(str).str.strip().str.title(),
        "sector": "",
    }).drop_duplicates("symbol")
    return df.reset_index(drop=True), as_of


def sp500_universe(timeout: float = 30) -> Universe:
    """Current S&P 500 members from SPY's official holdings, or a built-in list of ~100."""
    try:
        resp = requests.get(SPY_HOLDINGS_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        df, as_of = parse_spy_holdings(resp.content)
        if len(df) < 400:
            raise ValueError(f"only {len(df)} stocks parsed")
        return Universe(df, f"标普 500 成分股（State Street SPY 官方持仓，{as_of}）")
    except Exception as exc:  # network, file layout change...
        log.warning("Could not load SPY holdings (%s); using built-in list", exc)
        df = pd.DataFrame({"symbol": FALLBACK_UNIVERSE, "name": FALLBACK_UNIVERSE, "sector": ""})
        return Universe(df, f"内置的 {len(df)} 只美国大盘股（官方持仓文件暂时无法获取）")


@dataclass
class Panel:
    """Factor values for every stock on every day, using only data up to that day."""
    momentum: pd.DataFrame
    vol: pd.DataFrame
    vs_sma: pd.DataFrame
    score: pd.DataFrame
    eligible: pd.DataFrame
    market_ok: pd.Series
    market_vs_sma: pd.Series


def factor_panel(stocks: pd.DataFrame, market: pd.Series, p: ScreenParams) -> Panel:
    momentum = stocks.shift(p.mom_skip) / stocks.shift(p.mom_lookback) - 1
    vol = stocks.pct_change().rolling(p.vol_window).std() * np.sqrt(TRADING_DAYS)
    sma = stocks.rolling(p.trend_window).mean()
    vs_sma = stocks / sma - 1
    score = momentum / vol
    eligible = ((vs_sma > 0) & (momentum > 0) & (vol >= p.min_vol)
                & score.notna() & np.isfinite(score))
    market_vs_sma = market / market.rolling(p.trend_window).mean() - 1
    market_ok = (market_vs_sma > 0) if p.regime_filter else pd.Series(True, index=market.index)
    return Panel(momentum, vol, vs_sma, score, eligible, market_ok, market_vs_sma)


def weights_on(panel: Panel, date: pd.Timestamp, p: ScreenParams) -> pd.Series:
    """Target weights over the stock columns (the rest goes to cash)."""
    w = pd.Series(0.0, index=panel.score.columns)
    if not bool(panel.market_ok.get(date, False)):
        return w
    score = panel.score.loc[date][panel.eligible.loc[date]]
    w[score.nlargest(p.top_n).index] = 1.0 / p.top_n
    return w


def run_backtest(prices: pd.DataFrame, stocks: list[str], p: ScreenParams) -> dict[str, BacktestResult]:
    """Strategy vs S&P 500 vs holding every stock in the list equally.

    Uses today's index members for the whole history (survivorship bias),
    which flatters every column, the strategy most.
    """
    stocks = [s for s in stocks if s in prices.columns and s not in (MARKET, CASH)]
    panel = factor_panel(prices[stocks], prices[MARKET], p)
    base = max(prices[MARKET].first_valid_index(), prices[CASH].first_valid_index())
    first = prices.index[prices.index.searchsorted(base) + p.warmup]
    cols = stocks + [CASH]

    def strategy(hist: pd.DataFrame) -> pd.Series:
        w = weights_on(panel, hist.index[-1], p)
        return pd.concat([w, pd.Series({CASH: 1.0 - w.sum()})])

    def equal_weight(hist: pd.DataFrame) -> pd.Series:
        live = hist.iloc[-1][stocks].notna()
        return pd.Series(live / live.sum())

    return {
        "Strategy": backtest.simulate(prices[cols], strategy, first,
                                      band=p.rebalance_band, cost_bps=p.cost_bps),
        "S&P 500 (SPY)": backtest.simulate(prices[[MARKET]], lambda h: pd.Series({MARKET: 1.0}),
                                           first, cost_bps=p.cost_bps),
        "All stocks, equal weight": backtest.simulate(prices[stocks], equal_weight, first,
                                                      cost_bps=p.cost_bps),
    }


@dataclass
class Picks:
    as_of: pd.Timestamp
    market_ok: bool
    market_vs_sma: float
    table: pd.DataFrame  # top N: symbol, name, sector, momentum, vol, vs_sma, score
    next_rebalance: pd.Timestamp


def current_picks(prices: pd.DataFrame, universe: Universe, p: ScreenParams) -> Picks:
    stocks = [s for s in universe.symbols if s in prices.columns]
    panel = factor_panel(prices[stocks], prices[MARKET], p)
    date = prices.index[-1]
    row = pd.DataFrame({
        "momentum": panel.momentum.loc[date],
        "vol": panel.vol.loc[date],
        "vs_sma": panel.vs_sma.loc[date],
        "score": panel.score.loc[date],
        "eligible": panel.eligible.loc[date],
    })
    top = row[row["eligible"]].sort_values("score", ascending=False).head(p.top_n)
    info = universe.table.set_index("symbol")
    top = top.assign(name=info["name"].reindex(top.index), sector=info["sector"].reindex(top.index))
    top.index.name = "symbol"
    return Picks(
        as_of=date,
        market_ok=bool(panel.market_ok.loc[date]),
        market_vs_sma=float(panel.market_vs_sma.loc[date]),
        table=top.reset_index().drop(columns="eligible"),
        next_rebalance=next_rebalance_day(date),
    )


def next_rebalance_day(today: pd.Timestamp) -> pd.Timestamp:
    """First weekday of next month (the backtest trades the day after month end)."""
    first = (today + pd.offsets.MonthBegin(1)).normalize()
    return first + pd.offsets.BDay(0)


def fundamentals(symbols: list[str]) -> pd.DataFrame:
    """Valuation and quality numbers from Yahoo for a handful of symbols (slow: one call each)."""
    import yfinance as yf

    keys = {
        "shortName": "name", "sector": "sector", "marketCap": "market_cap",
        "trailingPE": "pe", "forwardPE": "forward_pe", "returnOnEquity": "roe",
        "profitMargins": "margin", "revenueGrowth": "revenue_growth",
        "recommendationKey": "analysts", "targetMeanPrice": "target", "currentPrice": "price",
    }
    rows = []
    for s in symbols:
        try:
            info = yf.Ticker(s).info or {}
        except Exception as exc:
            log.warning("No fundamentals for %s: %s", s, exc)
            info = {}
        rows.append({"symbol": s, **{v: info.get(k) for k, v in keys.items()}})
    df = pd.DataFrame(rows).set_index("symbol")
    df["sector"] = df["sector"].map(lambda s: SECTORS_ZH.get(s, s) if isinstance(s, str) else s)
    return df


def warnings_for(f: pd.Series) -> list[str]:
    """Plain-language red flags for one stock's fundamentals."""
    out = []
    margin, fpe, pe = f.get("margin"), f.get("forward_pe"), f.get("pe")
    if isinstance(margin, (int, float)) and margin < 0:
        out.append("亏损中")
    if (isinstance(fpe, (int, float)) and fpe > 60) or (isinstance(pe, (int, float)) and pe > 80):
        out.append("估值很高")
    return out
