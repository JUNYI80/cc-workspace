"""A 股多因子：短期反转 + 低异常换手 + 低波动，按月调仓。

为什么不照搬美股的动量策略：在 A 股，过去一年的涨幅（动量）几乎没有预测力，
反而是上个月涨得多的、换手突然放大的、波动大的股票，之后更容易跑输。
参考：Liu, Stambaugh & Yuan (2019), "Size and Value in China", Journal of Financial Economics；
以及大量 A 股实证研究中稳健的短期反转效应和低波动效应。

每个月最后一个交易日：
  1. 只看中证 800（沪深 300 + 中证 500）成分股，排除 ST、停牌、上市不满一年的股票；
  2. 三个因子分别排百分位：近一个月涨幅越低越好、异常换手（近 1 个月平均成交量 ÷
     近 1 年平均成交量）越低越好、近半年波动越低越好；
  3. 三个百分位取平均作为总分，等权买入总分最高的 N 只；
  4. 沪深 300 跌破 200 日均线时，全部持有现金（可在页面上关掉）。

另外，实际选股时会用最新市盈率排除亏损公司（历史市盈率拿不到，所以回测里没有这一步）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    top_n: int = 20
    reversal_window: int = 21  # 约 1 个月
    volume_short: int = 21
    volume_long: int = 250  # 约 1 年
    vol_window: int = 126  # 约半年
    min_listed_days: int = 365
    regime_window: int = 200
    regime_filter: bool = True
    buy_cost: float = 0.0015  # 佣金约 0.025% + 滑点约 0.1%，按 0.15% 估算
    sell_cost: float = 0.0020  # 再加 0.05% 印花税（只在卖出时收）
    rebalance_band: float = 0.01

    @property
    def warmup(self) -> int:
        return max(self.volume_long, self.regime_window) + 1


@dataclass
class Panel:
    """每只股票每天的因子值，只用到当天及以前的数据。"""
    reversal: pd.DataFrame
    abnormal_turnover: pd.DataFrame
    volatility: pd.DataFrame
    eligible: pd.DataFrame
    market_ok: pd.Series
    market_vs_sma: pd.Series


def build_panel(close: pd.DataFrame, volume: pd.DataFrame, st_codes: set[str],
                benchmark: pd.Series, p: Params) -> Panel:
    """close 是复权收盘价，volume 是成交量（股），列名相同；benchmark 是沪深 300 ETF。"""
    trading = volume.gt(0) & close.notna()
    rets = close.pct_change(fill_method=None)
    reversal = close / close.shift(p.reversal_window) - 1
    vol_traded = volume.where(trading)
    abnormal = (vol_traded.rolling(p.volume_short, min_periods=15).mean()
                / vol_traded.rolling(p.volume_long, min_periods=200).mean())
    volatility = rets.rolling(p.vol_window, min_periods=100).std() * np.sqrt(252)
    first_seen = close.apply(lambda s: s.first_valid_index())
    days_listed = pd.DataFrame({c: (close.index - first_seen[c]).days for c in close.columns},
                               index=close.index)
    not_st = pd.Series([c not in st_codes for c in close.columns], index=close.columns)
    eligible = (reversal.notna() & abnormal.notna() & np.isfinite(abnormal) & volatility.notna()
                & trading & days_listed.ge(p.min_listed_days)) & not_st
    bench = benchmark.reindex(close.index).ffill()
    market_vs_sma = bench / bench.rolling(p.regime_window).mean() - 1
    market_ok = market_vs_sma > 0 if p.regime_filter else pd.Series(True, index=close.index)
    return Panel(reversal, abnormal, volatility, eligible, market_ok, market_vs_sma)


def scores_on(panel: Panel, date: pd.Timestamp) -> pd.DataFrame:
    """当天合格股票的因子值、百分位和总分，按总分从高到低。"""
    ok = panel.eligible.loc[date]
    df = pd.DataFrame({
        "reversal": panel.reversal.loc[date][ok],
        "abnormal_turnover": panel.abnormal_turnover.loc[date][ok],
        "volatility": panel.volatility.loc[date][ok],
    })
    if df.empty:
        return df.assign(score=pd.Series(dtype=float))
    df["rank_reversal"] = (-df["reversal"]).rank(pct=True)
    df["rank_turnover"] = (-df["abnormal_turnover"]).rank(pct=True)
    df["rank_lowvol"] = (-df["volatility"]).rank(pct=True)
    df["score"] = df[["rank_reversal", "rank_turnover", "rank_lowvol"]].mean(axis=1)
    return df.sort_values("score", ascending=False)


def weights_on(panel: Panel, date: pd.Timestamp, p: Params) -> pd.Series:
    w = pd.Series(0.0, index=panel.reversal.columns)
    if not bool(panel.market_ok.get(date, False)):
        return w
    top = scores_on(panel, date).head(p.top_n).index
    w[top] = 1.0 / p.top_n
    return w
