"""Turn the latest data into a short list of plain-language reminders."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .ipo import LOCKUP_DAYS, STAGES, WatchStatus

ORDER = {"warn": 0, "ok": 1, "info": 2}


@dataclass(frozen=True)
class Alert:
    level: str  # "warn", "ok" or "info"
    area: str  # 市场 / A 类选股 / B 类新股 / ETF 轮动
    text: str


def market(market_vs_sma: float, regime_filter: bool = False) -> Alert:
    if market_vs_sma > 0:
        return Alert("ok", "市场", f"美股大盘在 200 日均线之上（{market_vs_sma:+.1%}），整体趋势向上。")
    action = ("你开启了大盘过滤，A 类策略此时持有现金，不买股票。" if regime_filter
              else "大盘偏弱，买入后波动可能更大。")
    return Alert("warn", "市场", f"美股大盘跌破 200 日均线（{market_vs_sma:+.1%}）。{action}")


def is_rebalance_window(today: date) -> bool:
    """The first three weekdays of a month."""
    first = today.replace(day=1)
    weekdays = pd.bdate_range(first, first + timedelta(days=10))[:3]
    return pd.Timestamp(today) in weekdays


def picks(current: list[str], history: dict, month: str, today: date,
          market_ok: bool) -> list[Alert]:
    out = []
    months = sorted(m for m in history.get("picks", {}) if m < month)
    if months:
        prev = set(history["picks"][months[-1]])
        added, removed = sorted(set(current) - prev), sorted(prev - set(current))
        if added or removed:
            parts = []
            if added:
                parts.append("买入 " + "、".join(added))
            if removed:
                parts.append("卖出 " + "、".join(removed))
            out.append(Alert("info", "A 类选股", f"本月持股有变化：{'；'.join(parts)}。"))
    if is_rebalance_window(today):
        action = "按『A 类选股』页面的名单调整持仓" if market_ok else "卖出 A 类股票，持有现金"
        out.append(Alert("warn", "A 类选股", f"今天是本月调仓日：{action}。"))
    return out


def watchlist(statuses: list[WatchStatus], history: dict, today: date) -> list[Alert]:
    out = []
    recent = [c for c in history.get("stage_changes", [])
              if (today - date.fromisoformat(c["date"])).days <= 7]
    for c in recent:
        out.append(Alert("warn", "B 类新股",
                         f"{c['name']} 上市进度更新：{STAGES[c['from']]} → {STAGES[c['to']]}。"))
    for s in statuses:
        if s.stage >= 4 and s.official:
            out.append(Alert("warn", "B 类新股", f"{s.name}：{s.label}。{s.evidence}"))
    return out


def ipo_calendar(upcoming: pd.DataFrame, recent: pd.DataFrame, today: date,
                 max_lockups: int = 3) -> list[Alert]:
    """Upcoming listings, plus lock-up expiries in the next 14 days (largest deals first)."""
    out = []
    for _, r in upcoming.iterrows():
        when = r["date"].isoformat() if r["date"] else "日期待定"
        price = (f"${r['price_low']:.0f}–{r['price_high']:.0f}" if pd.notna(r["price_low"])
                 else "价格待定")
        out.append(Alert("info", "B 类新股", f"{r['company']}（{r['symbol'] or '代码待定'}）"
                                            f"预计 {when} 定价上市，区间 {price}。首日不追高。"))
    soon = recent[recent["date"].map(lambda d: 0 <= (d + timedelta(days=LOCKUP_DAYS) - today).days <= 14)]
    if "value_usd" in soon:
        soon = soon.sort_values("value_usd", ascending=False, na_position="last")
    for _, r in soon.head(max_lockups).iterrows():
        lockup = r["date"] + timedelta(days=LOCKUP_DAYS)
        out.append(Alert("warn", "B 类新股", f"{r['company']}（{r['symbol']}）锁定期预计 "
                                            f"{lockup:%Y-%m-%d} 到期，前后常有抛压。"))
    if len(soon) > max_lockups:
        out.append(Alert("info", "B 类新股", f"另有 {len(soon) - max_lockups} 只新股的锁定期在两周内到期，"
                                            "见『B 类 · 新股与 IPO』页面。"))
    return out


def etf(today: date, traded_this_month: bool) -> list[Alert]:
    if is_rebalance_window(today) and not traded_this_month:
        return [Alert("warn", "ETF 轮动", "本月 ETF 轮动还没调仓：到『ETF 轮动』页面生成调仓计划。")]
    return []


def sort(alerts: list[Alert]) -> list[Alert]:
    return sorted(alerts, key=lambda a: ORDER.get(a.level, 9))
