"""Offline demo data for the app (T212Q_DEMO=1): random prices, fictional companies.

Used for tests and screenshots. Company names here are invented on purpose so
the app never shows made-up news about real companies.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import ipo, news
from .data import synthetic_prices
from .screener import FALLBACK_UNIVERSE, Universe

WATCHLIST = ["Example AI", "Sample Rockets"]
SECTORS = ["信息技术", "医疗保健", "金融", "可选消费", "通信服务", "工业", "日常消费", "能源"]


def universe(n: int = 60) -> Universe:
    syms = FALLBACK_UNIVERSE[:n]
    rng = np.random.default_rng(3)
    table = pd.DataFrame({"symbol": syms, "name": [f"{s} 演示公司" for s in syms],
                          "sector": rng.choice(SECTORS, len(syms))})
    return Universe(table, f"演示数据：{n} 只随机股票")


def prices(symbols: list[str], years: float = 14, cash: str | None = None) -> pd.DataFrame:
    """Random prices for `years` up to yesterday."""
    today = pd.Timestamp.today().normalize()
    start = today - pd.DateOffset(years=years)
    n = len(pd.bdate_range(start, today - pd.Timedelta(days=1)))
    return synthetic_prices(symbols, start=start.strftime("%Y-%m-%d"), years=n / 252,
                            cash_symbol=cash)


def fundamentals(symbols: list[str]) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    return pd.DataFrame({
        "name": [f"{s} 演示公司" for s in symbols],
        "forward_pe": rng.uniform(12, 70, len(symbols)).round(1),
        "pe": rng.uniform(15, 90, len(symbols)).round(1),
        "margin": rng.uniform(-0.05, 0.35, len(symbols)),
        "roe": rng.uniform(0.02, 0.4, len(symbols)),
        "analysts": rng.choice(["buy", "hold", "strong_buy"], len(symbols)),
    }, index=pd.Index(symbols, name="symbol"))


def calendar(today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    rows = [
        ("upcoming", "NWRB", "Northwind Robotics", 16.0, 18.0, 240e6, today + timedelta(days=5)),
        ("priced", "CTSO", "Contoso Cloud", 22.0, 22.0, 300e6, today - timedelta(days=120)),
        ("priced", "FBRK", "Fabrikam Bio", 15.0, 15.0, 150e6, today - timedelta(days=172)),
        ("priced", "TSPN", "Tailspin Energy", 30.0, 30.0, 420e6, today - timedelta(days=40)),
        ("filed", None, "Sample Rockets Corp", None, None, 2e9, today - timedelta(days=12)),
    ]
    return pd.DataFrame([{"status": s, "symbol": sym, "company": c, "exchange": "NASDAQ",
                          "price_low": lo, "price_high": hi, "shares": None, "value_usd": v,
                          "date": d} for s, sym, c, lo, hi, v, d in rows])


def headlines(entry: str) -> list[news.Headline]:
    now = datetime.now(timezone.utc)
    name = ipo.aliases(entry)[0]
    titles = {
        "Example AI": ["Example AI said to hire banks for possible IPO",
                       "Example AI valuation climbs in new funding round",
                       "Example AI CEO says no rush to go public"],
        "Sample Rockets": ["Sample Rockets publicly files for IPO",
                           "Sample Rockets IPO could be the year's largest"],
    }.get(name, [])
    return [news.Headline(t, "https://example.com/", now - timedelta(days=3 * i + 1), "演示新闻")
            for i, t in enumerate(titles)]
