"""把最新数据变成几条大白话提醒。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .ipo import STAGES, WatchStatus

ORDER = {"warn": 0, "ok": 1, "info": 2}


@dataclass(frozen=True)
class Alert:
    level: str  # warn / ok / info
    area: str
    text: str


def market(vs_sma: float) -> Alert:
    if vs_sma > 0:
        return Alert("ok", "市场", f"沪深 300 在 200 日均线之上（{vs_sma:+.1%}），整体趋势向上。")
    return Alert("warn", "市场", f"沪深 300 跌破 200 日均线（{vs_sma:+.1%}）。"
                                 "开启大盘过滤时，A 类策略持有现金，不买股票。")


def is_rebalance_window(today: date) -> bool:
    """每月前三个工作日。"""
    first = today.replace(day=1)
    days = pd.bdate_range(first, first + timedelta(days=10))[:3]
    return pd.Timestamp(today) in days


def picks(current: list[str], names: dict[str, str], history: dict, month: str, today: date,
          market_ok: bool) -> list[Alert]:
    out = []
    months = sorted(m for m in history.get("picks", {}) if m < month)
    if months:
        prev = set(history["picks"][months[-1]])
        added, removed = sorted(set(current) - prev), sorted(prev - set(current))
        label = lambda codes: "、".join(names.get(c, c) for c in codes)  # noqa: E731
        parts = ([f"买入 {label(added)}"] if added else []) + ([f"卖出 {label(removed)}"] if removed else [])
        if parts:
            out.append(Alert("info", "A 类选股", f"本月持股有变化：{'；'.join(parts)}。"))
    if is_rebalance_window(today):
        action = "按『A 类选股』页面的名单调整持仓" if market_ok else "卖出 A 类股票，持有现金"
        out.append(Alert("warn", "A 类选股", f"今天是本月调仓日：{action}。"))
    return out


def watchlist(statuses: list[WatchStatus], history: dict, today: date) -> list[Alert]:
    out = []
    for c in history.get("stage_changes", []):
        if (today - date.fromisoformat(c["date"])).days <= 7:
            out.append(Alert("warn", "B 类新股",
                             f"{c['name']} 上市进度更新：{STAGES[c['from']]} → {STAGES[c['to']]}。"))
    for s in statuses:
        if s.official and s.stage == 5:
            out.append(Alert("warn", "B 类新股", f"{s.name}：{s.label}。{s.evidence}"))
    return out


def subscriptions(df: pd.DataFrame) -> list[Alert]:
    out = []
    for _, r in df.iterrows():
        out.append(Alert("info", "B 类新股", f"{r['name']}（{r['board']}）{r['date']} 发布发行公告，"
                                             "一般下一个交易日可以申购（打新），以公告为准。"))
    return out


def unlocks(listings: pd.DataFrame, today: date) -> list[Alert]:
    out = []
    for _, r in listings.iterrows():
        unlock = r["date"] + timedelta(days=183)
        if 0 <= (unlock - today).days <= 14:
            out.append(Alert("warn", "B 类新股", f"{r['name']}（{r['code']}）约 {unlock:%Y-%m-%d} "
                                                "上市满 6 个月，部分股份解禁，前后常有抛压。"))
    return out


def sort(items: list[Alert]) -> list[Alert]:
    return sorted(items, key=lambda a: ORDER.get(a.level, 9))
