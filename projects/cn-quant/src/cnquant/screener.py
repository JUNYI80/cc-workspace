"""A 类：中证 800 低波动选股、按手数算买入股数、历史回测。"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from . import backtest
from .data import board, is_st
from .factors import Panel, Params, build_panel, scores_on, weights_on

CASH_RATE = 0.015  # 现金按年化 1.5% 计息（接近货币基金）
CANDIDATES = 3  # 取前 3×N 名做市盈率检查，排除亏损公司后保留 N 只


@dataclass
class Market:
    """回测和选股需要的数据。close / volume 的列是 Yahoo 代码（600519.SS）。"""
    close: pd.DataFrame
    volume: pd.DataFrame
    bench: pd.DataFrame  # 列：沪深300ETF、中证500ETF
    universe: pd.DataFrame  # code, name, yahoo, index, board

    @property
    def st_codes(self) -> set[str]:
        return set(self.universe.loc[self.universe["name"].map(is_st), "yahoo"])

    def names(self) -> dict[str, str]:
        return dict(zip(self.universe["yahoo"], self.universe["name"]))

    def panel(self, p: Params) -> Panel:
        return build_panel(self.close, self.volume, self.st_codes, self.bench["沪深300ETF"], p)


def lot_size(code: str, price: float, amount: float) -> tuple[int, float]:
    """交易规则：主板/创业板 100 股为一手；科创板至少买 200 股，之后可以 1 股递增。"""
    if not price or price <= 0 or amount <= 0 or math.isnan(price):
        return 0, 0.0
    if board(code) == "科创板":
        shares = int(amount // price)
        shares = shares if shares >= 200 else 0
    else:
        shares = int(amount // (price * 100)) * 100
    return shares, shares * price


@dataclass
class Picks:
    as_of: pd.Timestamp
    market_ok: bool
    market_vs_sma: float
    table: pd.DataFrame
    next_rebalance: pd.Timestamp


def next_rebalance_day(today: pd.Timestamp) -> pd.Timestamp:
    """下个月第一个工作日（遇到节假日顺延到开市日）。"""
    first = (today + pd.offsets.MonthBegin(1)).normalize()
    return first + pd.offsets.BDay(0)


def candidates(m: Market, p: Params) -> pd.DataFrame:
    """今天总分最高的 3×N 只（还没做市盈率检查）。"""
    panel = m.panel(p)
    return scores_on(panel, m.close.index[-1], p.factor_weights).head(CANDIDATES * p.top_n)


def current_picks(m: Market, p: Params, capital: float, fundamentals: pd.DataFrame | None = None,
                  prices: dict[str, float] | None = None) -> Picks:
    """本月名单：总分最高、且最新市盈率为正（不亏损）的 N 只，并按手数算出买多少股。"""
    panel = m.panel(p)
    date = m.close.index[-1]
    ranked = scores_on(panel, date, p.factor_weights).head(CANDIDATES * p.top_n)
    f = fundamentals if fundamentals is not None else pd.DataFrame(columns=["pe", "margin", "sector"])
    names = m.names()
    market_ok = bool(panel.market_ok.loc[date])
    per_stock = capital / p.top_n if market_ok else 0.0
    rows = []
    for sym, r in ranked.iterrows():
        pe = f["pe"].get(sym) if sym in f.index else None
        if pe is not None and not (isinstance(pe, (int, float)) and pe > 0):
            continue  # 亏损（市盈率为负或没有）
        price = (prices or {}).get(sym, float("nan"))
        shares, cost = lot_size(sym, price, per_stock)
        rows.append({
            "code": sym[:6], "yahoo": sym, "name": names.get(sym, sym), "board": board(sym),
            "reversal": r["reversal"], "abnormal_turnover": r["abnormal_turnover"],
            "volatility": r["volatility"], "score": r["score"], "pe": pe,
            "sector": f["sector"].get(sym) if sym in f.index else None,
            "price": price, "shares": shares, "cost": cost,
        })
        if len(rows) == p.top_n:
            break
    return Picks(date, market_ok, float(panel.market_vs_sma.loc[date]), pd.DataFrame(rows),
                 next_rebalance_day(date))


def run_backtest(m: Market, p: Params) -> dict[str, backtest.BacktestResult]:
    """策略 vs 沪深 300 ETF vs 中证 500 ETF vs 全部成分股等权。

    注意：用今天的成分股回看过去（幸存者偏差），会高估所有曲线，策略高估得最多。
    """
    panel = m.panel(p)
    trading = m.volume.gt(0)
    rets = m.close.pct_change(fill_method=None).where(trading)
    first = m.close.index[min(p.warmup, len(m.close.index) - 1)]
    bench_rets = m.bench.reindex(m.close.index).ffill().pct_change(fill_method=None)
    st = m.st_codes

    def equal_weight(d: pd.Timestamp) -> pd.Series:
        live = trading.loc[d] & m.close.loc[d].notna()
        live[[c for c in live.index if c in st]] = False
        return live.astype(float) / max(live.sum(), 1)

    kw = dict(band=p.rebalance_band, buy_cost=p.buy_cost, sell_cost=p.sell_cost)
    return {
        "策略": backtest.simulate(rets, lambda d: weights_on(panel, d, p), first,
                                cash_rate=CASH_RATE, **kw),
        "沪深300ETF": backtest.simulate(bench_rets[["沪深300ETF"]],
                                     lambda d: pd.Series({"沪深300ETF": 1.0}), first),
        "中证500ETF": backtest.simulate(bench_rets[["中证500ETF"]],
                                     lambda d: pd.Series({"中证500ETF": 1.0}), first),
        # 800 stocks at 0.125% each: the no-trade band would stop this benchmark from ever buying.
        "成分股等权": backtest.simulate(rets, equal_weight, first, buy_cost=p.buy_cost,
                                    sell_cost=p.sell_cost),
    }
