"""离线演示数据（CNQ_DEMO=1）：随机价格和虚构公司，只用于测试和截图。

公司名全部是虚构的，避免显示关于真实公司的假新闻。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import ipo, news
from .screener import Market

WATCHLIST = ["示例智能", "样本机器人"]


def market(n: int = 80, years: float = 9, seed: int = 11) -> Market:
    rng = np.random.default_rng(seed)
    today = pd.Timestamp.today().normalize()
    idx = pd.bdate_range(today - pd.DateOffset(years=years), today - pd.Timedelta(days=1))
    codes = [f"{600000 + i:06d}" if i % 3 else f"{300000 + i:06d}" for i in range(n)]
    yahoo = [c + (".SS" if c.startswith("6") else ".SZ") for c in codes]
    regime = np.where(np.sin(np.arange(len(idx)) / 140) > -0.3, 0.0004, -0.0012)
    rets = regime[:, None] + rng.normal(0, 0.022, (len(idx), n))
    close = pd.DataFrame(20 * np.exp(np.cumsum(rets, axis=0)), index=idx, columns=yahoo)
    volume = pd.DataFrame(rng.lognormal(15, 0.5, (len(idx), n)), index=idx, columns=yahoo)
    bench = pd.DataFrame({"沪深300ETF": 4 * np.exp(np.cumsum(regime + rng.normal(0, 0.012, len(idx)))),
                          "中证500ETF": 6 * np.exp(np.cumsum(regime + rng.normal(0, 0.015, len(idx))))},
                         index=idx)
    uni = pd.DataFrame({"code": codes, "name": [f"演示公司{i:02d}" for i in range(n)], "yahoo": yahoo,
                        "index": "沪深300", "board": ["创业板" if c.startswith("3") else "主板" for c in codes]})
    return Market(close, volume, bench, uni)


def fundamentals(symbols: list[str]) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    pe = rng.uniform(-20, 60, len(symbols))
    return pd.DataFrame({"pe": pe.round(1), "margin": rng.uniform(-0.1, 0.3, len(symbols)),
                         "sector": rng.choice(["Industrials", "Technology", "Financial Services"],
                                              len(symbols))},
                        index=pd.Index(symbols, name="yahoo"))


def headlines(name: str) -> list[news.Headline]:
    now = datetime.now(timezone.utc)
    titles = {"示例智能": ["示例智能完成上市辅导备案", "示例智能新一轮融资估值上涨"],
              "样本机器人": ["样本机器人科创板IPO获受理"]}.get(name, [])
    return [news.Headline(t, "https://example.com/", now - timedelta(days=4 * i + 1), "证券时报")
            for i, t in enumerate(titles)]


def projects(name: str) -> list[ipo.Project]:
    if name == "样本机器人":
        return [ipo.Project("上交所", "样本机器人股份有限公司", "样本机器人", "已问询", 4,
                            (date.today() - timedelta(days=6)).isoformat(), "示例证券", 12.0, "专用设备制造业")]
    return []


def listings(today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    rows = [("301999", "演示新材", today - timedelta(days=4), "创业板"),
            ("688999", "演示芯片", today - timedelta(days=176), "科创板"),
            ("001999", "演示食品", today - timedelta(days=120), "主板")]
    return pd.DataFrame([{"code": c, "name": n, "date": d, "board": b, "url": "https://example.com/"}
                         for c, n, d, b in rows])


def subscriptions(today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    return pd.DataFrame([{"code": "301998", "name": "演示科技", "date": today - timedelta(days=1),
                          "board": "创业板", "title": "首次公开发行股票并在创业板上市发行公告",
                          "url": "https://example.com/"}])


def recent_projects() -> list[ipo.Project]:
    d = date.today()
    return [ipo.Project("深交所", "演示电子股份有限公司", "演示电子", "已受理", 3, d.isoformat(), "示例证券", 8.5,
                        "计算机、通信和其他电子设备制造业"),
            ipo.Project("深交所", "演示医药股份有限公司", "演示医药", "注册生效", 5,
                        (d - timedelta(days=2)).isoformat(), "示例证券", 6.2, "医药制造业")]
