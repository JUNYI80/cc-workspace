"""A 股量化助手。用启动文件打开，或：streamlit run src/cnquant/app.py

CNQ_DEMO=1 时使用随机价格和虚构公司（离线演示和测试用）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script without install

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from cnquant import alerts, appstate, backtest, data, demo, ipo, screener, ui  # noqa: E402
from cnquant.factors import Params  # noqa: E402

DEMO = os.getenv("CNQ_DEMO") == "1"
ICONS = {"warn": ":material/warning:", "ok": ":material/check_circle:", "info": ":material/info:"}
SOURCES = ("数据来源：中证指数有限公司官网（成分股）、Yahoo Finance（行情和市盈率）、"
           "深圳证券交易所和上海证券交易所发行上市审核信息、巨潮资讯网（证监会指定信息披露网站）、"
           "Google 新闻（只保留官方媒体和主流财经媒体）。")

st.set_page_config(page_title="A股量化助手", page_icon=":material/monitoring:", layout="wide")
if DEMO:
    appstate.use_state_dir(Path(tempfile.gettempdir()) / "cnq-demo-state")
settings = appstate.load_settings()
if DEMO:
    settings["watchlist"] = demo.WATCHLIST
ui.inject_css()
ui.brand("A股量化助手", "沪深 A 股 · 中证 800")


# -- data (cached) -----------------------------------------------------------------

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_market() -> screener.Market:
    if DEMO:
        return demo.market()
    uni = data.universe()
    close, volume = data.load_prices(list(uni["yahoo"]))
    return screener.Market(close, volume, data.load_benchmarks(), uni)


def params(top_n: int, regime: bool) -> Params:
    return Params(top_n=top_n, regime_filter=regime)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_picks(top_n: int, regime: bool, capital: float) -> screener.Picks:
    m, p = get_market(), params(top_n, regime)
    cands = screener.candidates(m, p)
    if DEMO:
        f = demo.fundamentals(cands)
        prices = {s: float(m.close[s].iloc[-1]) for s in cands}
    else:
        f = data.fundamentals(cands)
        prices = data.latest_prices(cands)
    return screener.current_picks(m, p, capital, f, prices)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_backtest(top_n: int, regime: bool) -> dict:
    return screener.run_backtest(get_market(), params(top_n, regime))


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_factor_comparison(top_n: int, regime: bool) -> pd.DataFrame:
    variants = {"只用低波动": (0, 0, 1), "只用短期反转": (1, 0, 0), "只用低异常换手": (0, 1, 0),
                "三个因子合在一起": (1, 1, 1)}
    rows = {}
    for label, fw in variants.items():
        r = screener.run_backtest(get_market(), Params(top_n=top_n, regime_filter=regime,
                                                       factor_weights=fw))["策略"]
        s = backtest.summary(r.nav)
        rows[label] = {"年化收益": f"{s['cagr']:+.1%}", "最大回撤": f"{s['max_drawdown']:.0%}",
                       "夏普": f"{s['sharpe']:.2f}", "年换手": f"{r.turnover.sum() / 2 / s['years']:.0%}"}
    return pd.DataFrame(rows).T


@st.cache_data(ttl=3600, show_spinner=False)
def get_watch(entry: str) -> ipo.WatchStatus:
    name = ipo.aliases(entry)[0]
    if DEMO:
        return ipo.watch_status(entry, demo.headlines(name), demo.projects(name), [])
    heads, projects, anns, errors = ipo.gather(entry)
    return ipo.watch_status(entry, heads, projects, anns, errors=errors)


@st.cache_data(ttl=3600, show_spinner=False)
def get_listings() -> tuple[pd.DataFrame | None, str | None]:
    if DEMO:
        return demo.listings(), None
    try:
        return ipo.recent_listings(), None
    except Exception as exc:
        return None, str(exc)


@st.cache_data(ttl=3600, show_spinner=False)
def get_subscriptions() -> tuple[pd.DataFrame | None, str | None]:
    if DEMO:
        return demo.subscriptions(), None
    try:
        return ipo.upcoming_subscriptions(), None
    except Exception as exc:
        return None, str(exc)


@st.cache_data(ttl=3600, show_spinner=False)
def get_recent_projects() -> tuple[list[ipo.Project] | None, str | None]:
    if DEMO:
        return demo.recent_projects(), None
    try:
        return ipo.szse_recent(), None
    except Exception as exc:
        return None, str(exc)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_listing_prices(symbols: tuple[str, ...]) -> pd.DataFrame:
    symbols = tuple(s for s in symbols if s)  # Beijing exchange stocks have no Yahoo symbol
    if DEMO or not symbols:
        return pd.DataFrame()
    try:
        close, _ = data.download(list(symbols), (date.today() - timedelta(days=400)).isoformat())
        return close
    except Exception:
        return pd.DataFrame()


# -- UI helpers ----------------------------------------------------------------------

IPO_STEPS = ["有报道", "上市辅导", "已受理", "审核中", "注册发行", "已上市"]
ADVICE = {"ok": "✓ 可关注", "info": "… 观察", "warn": "✕ 回避"}
IDENTITY = {k: k for k in ("策略", "沪深300ETF", "中证500ETF", "成分股等权")}


def md(text: str) -> str:
    return str(text).replace("$", "\\$")


def show_alert(a: alerts.Alert) -> None:
    box = {"warn": st.warning, "ok": st.success, "info": st.info}[a.level]
    box(f"**{md(a.area)}**　{md(a.text)}", icon=ICONS[a.level])


def demo_banner() -> None:
    if DEMO:
        st.warning("演示模式：以下全部是随机生成的数据和虚构公司，只用来展示界面。", icon=":material/science:")


def loading(msg: str = "正在下载最新数据……第一次打开需要 1–3 分钟，之后会快很多。"):
    return st.spinner(msg)


def verdict(results: dict, strategy: str = "策略") -> str:
    s = backtest.summary(results[strategy].nav)
    parts = []
    for k, r in results.items():
        if k == strategy:
            continue
        b = backtest.summary(r.nav)
        more = "高于" if s["cagr"] > b["cagr"] else "低于"
        safer = "更小" if s["max_drawdown"] > b["max_drawdown"] else "更大"
        parts.append(f"比『{k}』：年化收益{more}（{s['cagr']:+.1%} 对 {b['cagr']:+.1%}），"
                     f"最大回撤{safer}（{s['max_drawdown']:.0%} 对 {b['max_drawdown']:.0%}）")
    return f"过去 {s['years']:.0f} 年的回测结论：" + "；".join(parts) + "。"


def headline_tiles(results: dict, bench: str = "沪深300ETF") -> None:
    s, b = backtest.summary(results["策略"].nav), backtest.summary(results[bench].nav)
    c = st.columns(4)
    c[0].metric("年化收益", f"{s['cagr']:+.1%}", f"{(s['cagr'] - b['cagr']) * 100:+.1f} 个百分点", border=True)
    c[1].metric("最大回撤（最多亏过）", f"{s['max_drawdown']:.0%}",
                f"{(s['max_drawdown'] - b['max_drawdown']) * 100:+.0f} 个百分点", border=True)
    c[2].metric("最差的一年", f"{s['worst_year']:+.1%}", border=True)
    c[3].metric("10 万元变成", f"{100_000 * results['策略'].nav.iloc[-1]:,.0f}", border=True)
    st.caption(f"小字是和『{bench}』相比的差距（{bench}：年化 {b['cagr']:+.1%}，最大回撤 {b['max_drawdown']:.0%}，"
               f"10 万元变成 {100_000 * results[bench].nav.iloc[-1]:,.0f}）。")


def summary_table(results: dict) -> pd.DataFrame:
    rows = {}
    for k, r in results.items():
        s = backtest.summary(r.nav)
        rows[k] = {
            "本金 10 万元变成": f"{100_000 * r.nav.iloc[-1]:,.0f}",
            "年化收益": f"{s['cagr']:+.1%}",
            "年化波动": f"{s['vol']:.1%}",
            "夏普比率": f"{s['sharpe']:.2f}",
            "最大回撤（最多亏过）": f"{s['max_drawdown']:.1%}",
            "最差的一年": f"{s['worst_year']:+.1%}",
        }
    return pd.DataFrame(rows)


def watch_statuses() -> list[ipo.WatchStatus]:
    return [get_watch(e) for e in settings["watchlist"]]


# -- pages ------------------------------------------------------------------------------

def page_overview() -> None:
    st.title("今日概览")
    st.caption(f"{date.today():%Y 年 %m 月 %d 日}　·　数据每 6 小时自动更新")
    demo_banner()
    today = date.today()
    history = appstate.load_history()
    items: list[alerts.Alert] = []
    top_n, regime = int(settings["top_n"]), bool(settings["regime_filter"])

    with loading():
        try:
            picks = get_picks(top_n, regime, float(settings["capital_cny"]))
        except Exception as exc:
            picks = None
            st.error(f"行情数据暂时无法下载，请检查网络后刷新页面。（{exc}）")
        statuses = watch_statuses()
        listings, _ = get_listings()
        subs, _ = get_subscriptions()

    cols = st.columns(4)
    if picks is not None:
        held = len(picks.table) if picks.market_ok else 0
        items.append(alerts.market(picks.market_vs_sma, regime))
        items += alerts.picks(picks.added, picks.removed, get_market().names(), today, picks.market_ok)
        bench = get_market().bench["沪深300ETF"].dropna().iloc[-250:]
        cols[0].metric("沪深 300（对比 200 日均线）", "向上" if picks.market_vs_sma > 0 else "向下",
                       f"{picks.market_vs_sma:+.1%}", border=True, height="stretch",
                       chart_data=bench.round(3).tolist(), chart_type="line")
        cols[1].metric("A 类本月持股", f"{held} 只" if held else "持有现金",
                       f"买入 {len(picks.added)} · 卖出 {len(picks.removed)}", delta_color="off",
                       delta_arrow="off", border=True, height="stretch")
        cols[3].metric("下次调仓日", f"{picks.next_rebalance:%m 月 %d 日}", border=True, height="stretch")
    for s in statuses:
        appstate.record_stage(history, s.name, s.stage, today)
    items += alerts.watchlist(statuses, history, today)
    if subs is not None:
        items += alerts.subscriptions(subs)
    if listings is not None:
        items += alerts.unlocks(listings, today)
    appstate.save_history(history)
    cols[2].metric("B 类关注公司", f"{len(statuses)} 家",
                   f"最高进度 {max((s.stage for s in statuses), default=0)}/6", border=True,
                   delta_color="off", delta_arrow="off", height="stretch")

    st.subheader("提醒")
    if not items:
        st.info("今天没有需要处理的事情。", icon=ICONS["info"])
    for a in alerts.sort(items):
        show_alert(a)

    st.subheader("策略体检")
    st.caption("用真实历史数据回测。回测不代表未来；所有策略都可能连续一两年跑输指数。")
    try:
        res = get_backtest(top_n, regime)
        s, b = backtest.summary(res["策略"].nav), backtest.summary(res["沪深300ETF"].nav)
        with st.container(border=True):
            st.markdown("**A 类 · 低波动选股**")
            c1, c2, c3 = st.columns(3)
            c1.metric("年化收益", f"{s['cagr']:+.1%}", f"{(s['cagr'] - b['cagr']) * 100:+.1f} 个百分点")
            c2.metric("最大回撤", f"{s['max_drawdown']:.0%}",
                      f"{(s['max_drawdown'] - b['max_drawdown']) * 100:+.0f} 个百分点")
            c3.metric("最差的一年", f"{s['worst_year']:+.1%}")
            st.caption(f"对比『沪深300ETF』（年化 {b['cagr']:+.1%}，最大回撤 {b['max_drawdown']:.0%}），"
                       f"回测 {s['years']:.0f} 年。")
    except Exception as exc:
        st.caption(f"回测暂时无法计算：{exc}")
    with st.container(border=True):
        st.markdown(
            "**每个月只需要做一次：**\n"
            "1. 每月第一个交易日打开本软件，看『A 类选股』的名单，在你的证券 App 里照着买卖。\n"
            "2. 看一眼『B 类新股』有没有打新或解禁提醒。\n"
            "3. 平时不用盯盘。"
        )
    st.caption(SOURCES)


def page_stocks() -> None:
    st.title("A 类 · 低波动选股")
    st.caption("每月从中证 800（沪深 300 + 中证 500）里选出波动最低的股票。只看数据，不听消息。")
    demo_banner()
    c1, c2, c3 = st.columns([1, 1, 1])
    capital = c1.number_input("准备投入多少钱（元）", min_value=10_000.0, max_value=100_000_000.0,
                              value=float(settings["capital_cny"]), step=10_000.0)
    top_n = c2.slider("买几只股票", 5, 30, int(settings["top_n"]),
                      help="越多越分散、越稳；资金少时减少只数，否则很多股票买不起一手。")
    regime = c3.toggle("大盘过滤", value=bool(settings["regime_filter"]),
                       help="开启后，沪深 300 跌破 200 日均线时全部持有现金。2015–2026 年的回测里它在所有组合中"
                            "都降低了收益，所以默认关闭。")
    if (capital, top_n, regime) != (settings["capital_cny"], settings["top_n"], settings["regime_filter"]):
        settings.update(capital_cny=capital, top_n=top_n, regime_filter=regime)
        if not DEMO:
            appstate.save_settings(settings)

    with loading():
        try:
            picks = get_picks(top_n, regime, capital)
            market = get_market()
        except Exception as exc:
            st.error(f"行情数据暂时无法下载，请检查网络后刷新页面。（{exc}）")
            return
    t = picks.table
    st.caption(f"名单按 {picks.as_of:%Y-%m-%d} 收盘计算，本月内不变　·　行情截至 {picks.data_date:%Y-%m-%d}")
    if t.empty:
        st.info("这个月没有符合条件的股票。")
        return
    names = market.names()
    if not picks.market_ok:
        st.warning("你开启了大盘过滤，沪深 300 在月底时低于 200 日均线：本月**不买股票**，持有现金"
                   "（或货币基金）。下面的名单仅供参考。", icon=ICONS["warn"])
    else:
        trend = (f"沪深 300 比 200 日均线高 {picks.market_vs_sma:.1%}" if picks.market_vs_sma > 0
                 else f"注意：沪深 300 比 200 日均线低 {-picks.market_vs_sma:.1%}，大盘偏弱")
        st.success(f"本月买入下面 {len(t)} 只，每只约 ¥{capital / top_n:,.0f}。{trend}。", icon=ICONS["ok"])
        if picks.added or picks.removed:
            label = lambda codes: "、".join(names.get(c, c) for c in codes) or "无"  # noqa: E731
            st.info(f"相比上月名单　买入：{label(picks.added)}　｜　卖出：{label(picks.removed)}",
                    icon=":material/swap_horiz:")

    tips = []
    for _, r in t.iterrows():
        tip = []
        if picks.market_ok and r["shares"] == 0:
            tip.append("钱不够买一手" if r["board"] != "科创板" else "钱不够买 200 股")
        if isinstance(r["pe"], float) and r["pe"] > 80:
            tip.append("估值很高")
        tips.append("；".join(tip))
    table = pd.DataFrame({
        "排名": range(1, len(t) + 1), "代码": t["code"], "名称": t["name"], "板块": t["board"],
        "半年走势": ui.sparkline_rows(market.close, list(t["yahoo"])),
        "年化波动": t["volatility"] * 100, "近 1 个月涨跌": t["reversal"] * 100,
        "市盈率": t["pe"], "最新价": t["price"],
        "建议买入（股）": t["shares"], "约需金额": t["cost"], "提示": tips,
    })
    st.dataframe(table, hide_index=True, height=35 * (len(table) + 1) + 3, column_config={
        "排名": st.column_config.NumberColumn(width="small"),
        "半年走势": st.column_config.LineChartColumn(width="small", help="最近约 6 个月的复权股价走势"),
        "年化波动": st.column_config.NumberColumn(format="%.0f%%", help="选股依据：越低越稳"),
        "近 1 个月涨跌": st.column_config.NumberColumn(format="%+.1f%%"),
        "市盈率": st.column_config.NumberColumn(format="%.1f"),
        "最新价": st.column_config.NumberColumn(format="¥%.2f"),
        "约需金额": st.column_config.NumberColumn(format="¥%.0f"),
        "提示": st.column_config.TextColumn(width="medium"),
    })
    st.caption(f"买入合计约 ¥{t['cost'].sum():,.0f}，剩余资金留作现金。")

    left, right = st.columns([1, 1])
    with left:
        counts = t["board"].value_counts().rename_axis("板块").reset_index(name="只数")
        ui.hbar(counts, "板块", "只数", "板块分布（只数）")
    with right.container(border=True):
        st.markdown(
            "**怎么操作**\n"
            "1. 打开你的证券 App（A 股需要在国内券商开户；Trading 212 不能买 A 股）。\n"
            "2. 按上表的代码和股数买入：主板、创业板 100 股为一手；科创板至少 200 股。\n"
            f"3. 下次调仓日（{picks.next_rebalance:%m 月 %d 日}）回来：不在新名单里的卖掉，新进的买入。\n\n"
            "A 股是 T+1：今天买的明天才能卖。卖出时收 0.05% 印花税，佣金一般约万分之 2.5（不足 5 元按 5 元收）。"
        )

    with st.expander("历史回测", expanded=True):
        with loading("正在回测过去十年的表现……"):
            res = get_backtest(top_n, regime)
        st.info(md(verdict(res)), icon=ICONS["info"])
        headline_tiles(res)
        ui.performance_charts(res, IDENTITY)
        ui.yearly_chart(res, IDENTITY, backtest.calendar_returns)
        with st.expander("详细数字"):
            st.table(summary_table(res))
        st.caption("『成分股等权』是把同样这 800 只股票平均买入，和策略一样受幸存者偏差影响，所以是最公平的对比；"
                   "两只 ETF（510300、510500）是真实可买的产品。回测含买卖成本（买 0.15%、卖 0.20%，卖出含印花税），"
                   "现金按年化 1.5% 计息。注意：回测用的是今天的成分股，过去被剔除的公司不在里面（幸存者偏差），"
                   "会高估所有曲线；排除亏损公司这一步也只在实际选股时做，回测里没有。")
    with st.expander("为什么选低波动？（三个因子的真实数据对比）"):
        st.markdown("用同样的中证 800 股票、同样的成本，把每个因子单独测一遍。点下面的按钮会重新计算（约 1 分钟）。")
        if st.button("重新计算因子对比"):
            with loading("正在计算……"):
                st.table(get_factor_comparison(top_n, regime))
        else:
            st.markdown("""
| 因子（持 20 只，2015–2026） | 年化收益 | 最大回撤 | 夏普 | 年换手 |
|---|---|---|---|---|
| **只用低波动（默认）** | **+7.6%** | **-39%** | **0.56** | **约 2 倍** |
| 只用短期反转 | +10.0% | -51% | 0.47 | 约 11 倍 |
| 只用低异常换手 | +7.6% | -66% | 0.43 | 约 7 倍 |
| 三个因子合在一起 | +2.8% | -59% | 0.24 | 约 9 倍 |
| 沪深 300 ETF | +3.6% | -45% | 0.28 | — |
""")
    st.caption(SOURCES)


def page_ipo() -> None:
    st.title("B 类 · 新股与 IPO")
    st.caption("跟踪还没上市的公司离上市还有多远，提醒打新和新股的风险点。")
    demo_banner()
    tab1, tab2, tab3, tab4 = st.tabs(["关注的公司", "打新日历", "新股提醒", "审核动态"])

    with tab1:
        with loading("正在查询交易所审核信息、巨潮资讯和新闻……"):
            statuses = watch_statuses()
        for s in statuses:
            with st.container(border=True):
                head, badge = st.columns([4, 1])
                head.subheader(s.name + (f"　{s.code}" if s.code else ""))
                if s.stage:
                    badge.badge("官方数据" if s.official else "据新闻", color="blue" if s.official else "gray",
                                icon=":material/verified:" if s.official else ":material/newspaper:")
                ui.stepper(s.stage, IPO_STEPS, s.official)
                st.markdown(f"**{s.label}**" + (f"　·　近 30 天相关新闻 {s.heat} 条" if s.heat else ""))
                if s.evidence:
                    st.caption(f"依据：{md(s.evidence)}")
                if s.terminated:
                    st.caption(f":orange[最近一次申请已终止：{s.terminated}]")
                for e in s.errors:
                    st.caption(f":orange[{e}]")
                if s.headlines:
                    with st.expander("最新新闻（只显示官方媒体和主流财经媒体）"):
                        for h in s.headlines:
                            st.markdown(f"- {h.published:%m-%d}　[{md(h.title)}]({h.link})　*{md(h.source)}*")
        with st.expander("编辑关注名单"):
            text = st.text_area("每行一家公司，用简称即可（例如：宇树科技）。公司正式名称不同时，用 | 隔开写上，"
                                "例如：长鑫存储 | 长鑫科技", value="\n".join(settings["watchlist"]),
                                height=150)
            if st.button("保存名单"):
                settings["watchlist"] = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if not DEMO:
                    appstate.save_settings(settings)
                st.rerun()
        st.caption("进度阶段：传闻 → 上市辅导 → 交易所受理 → 问询 / 上市委审议 → 注册 → 发行上市。"
                   "交易所和巨潮资讯是官方数据；带『据新闻』的还没有官方证实。")

    with tab2:
        subs, err = get_subscriptions()
        st.markdown("**打新**：新股发行时可以申购（需要持有对应市场一定市值的股票），中签后按发行价买入。"
                    "近年 A 股新股上市首日多数上涨，但中签率很低，也有破发。")
        if subs is None:
            st.warning(f"巨潮资讯暂时无法获取。（{err}）")
        elif subs.empty:
            st.info("最近 10 天没有新股发行公告。")
        else:
            st.dataframe(pd.DataFrame({"公告日": subs["date"], "代码": subs["code"], "名称": subs["name"],
                                       "板块": subs["board"], "公告": subs["title"], "原文": subs["url"]}),
                         hide_index=True, column_config={"原文": st.column_config.LinkColumn(display_text="PDF")})
            st.caption("发行公告发布后的下一个交易日一般是申购日，具体以公告为准。来源：巨潮资讯网。")

    with tab3:
        listings, err = get_listings()
        if listings is None:
            st.warning(f"巨潮资讯暂时无法获取。（{err}）")
        elif listings.empty:
            st.info("最近半年没有新股上市公告。")
        else:
            px = get_listing_prices(tuple(data.to_yahoo(c) for c in listings["code"]))
            rows = []
            for _, r in listings.iterrows():
                sym = data.to_yahoo(r["code"])
                closes = px[sym].dropna() if sym in px.columns else pd.Series(dtype=float)
                adv = ipo.post_listing_advice(r["date"], closes if len(closes) else None)
                first, last = (float(closes.iloc[0]), float(closes.iloc[-1])) if len(closes) else (None, None)
                rows.append({"上市日（约）": r["date"], "代码": r["code"], "名称": r["name"], "板块": r["board"],
                             "最新价": last,
                             "相对首日收盘": (last / first - 1) * 100 if first else None,
                             "建议": ADVICE[adv.level], "说明": adv.text, "公告": r["url"]})
            df = pd.DataFrame(rows)
            c = st.columns(3)
            for col, level in zip(c, ("ok", "info", "warn")):
                col.metric(ADVICE[level], f"{(df['建议'] == ADVICE[level]).sum()} 只", border=True)
            priced = df.dropna(subset=["相对首日收盘"]).head(25)
            if not priced.empty:
                chart = priced.assign(标签=priced["代码"] + "　" + priced["名称"], 涨跌=priced["相对首日收盘"] / 100)
                ui.hbar(chart, "标签", "涨跌", "最近上市的新股：最新价相对上市首日收盘价", fmt="+.0%",
                        sign_labels=("高于首日收盘", "低于首日收盘"))
            choice = st.radio("筛选", ["全部"] + list(ADVICE.values()), horizontal=True,
                              label_visibility="collapsed")
            st.dataframe(df if choice == "全部" else df[df["建议"] == choice], hide_index=True, column_config={
                "最新价": st.column_config.NumberColumn(format="¥%.2f"),
                "相对首日收盘": st.column_config.NumberColumn(format="%+.0f%%"),
                "说明": st.column_config.TextColumn(width="large"),
                "公告": st.column_config.LinkColumn(display_text="PDF"),
            })
            st.caption("规则：新股前 5 个交易日没有涨跌幅限制；上市满 6 个月和 12 个月有股份解禁。"
                       "上市日和解禁日期是按公告日估算的，准确日期以公告为准。北交所股票暂无行情。")

    with tab4:
        projects, err = get_recent_projects()
        if projects is None:
            st.warning(f"深交所审核信息暂时无法获取。（{err}）")
        else:
            order = ["已受理", "已问询", "上市委会议通过", "提交注册", "注册生效"]
            counts = pd.Series([p.status for p in projects]).value_counts()
            counts = counts.reindex([s for s in order if s in counts.index] +
                                    [s for s in counts.index if s not in order])
            ui.hbar(counts.rename_axis("状态").reset_index(name="家数"), "状态", "家数",
                    "深交所最近更新的 IPO 项目（按审核状态）")
            st.dataframe(pd.DataFrame([{"更新日期": p.updated, "公司": p.company, "状态": p.status,
                                        "行业": p.industry, "保荐机构": p.sponsor,
                                        "计划募资（亿元）": p.amount_yi} for p in projects]), hide_index=True)
            st.caption("来源：深圳证券交易所发行上市审核信息公开网站（创业板、深市主板）。")
    st.caption(SOURCES)


def page_help() -> None:
    st.title("使用说明")
    st.markdown("""
**这个软件做两件事**

| 板块 | 做什么 | 你要做什么 |
|---|---|---|
| A 类 · 低波动选股 | 每月从中证 800 里选出波动最低的 20 只股票，算好每只买多少股 | 每月一次，照名单在证券 App 里买卖 |
| B 类 · 新股与 IPO | 跟踪公司的上市进度，提醒打新和解禁 | 看提醒，自己决定 |

**A 类的原理**（针对 A 股的特点，不是照搬美股）
- 在 A 股，过去一年涨得多的股票（动量）后面并不更强；波动大的"妖股"长期反而跑输。
- 每月从中证 800 里选出近半年波动最低的 20 只，排除 ST 股、停牌股、上市不满一年的股票和亏损公司。
- 用 2015–2026 年真实数据测试，低波动年化 +7.6%、最大回撤 -39%，好于沪深 300 ETF（+3.6%、-45%）；详见『A 类』页面底部的因子对比。
- 可选的大盘过滤（沪深 300 跌破 200 日均线时空仓）在回测里降低了收益，默认关闭。

**B 类的原理**
- A 股上市要经过：上市辅导 → 交易所受理 → 问询 → 上市委审议 → 证监会注册 → 发行上市。软件直接读取交易所的官方审核状态和巨潮资讯的公告。
- 新股前 5 个交易日没有涨跌幅限制，波动极大；上市 6 个月、12 个月有解禁。所以策略是：可以参与打新，但不追高刚上市的新股。

**一定要知道的风险**
- 没有任何策略能保证赚钱。回测是用历史数据模拟的，会高估真实表现。
- Trading 212 不能买 A 股；需要国内券商账户（或通过港股通等渠道）。
- 只投亏得起的钱。

本软件仅供研究学习，不构成投资建议。
""")
    st.caption(SOURCES)


pages = st.navigation([
    st.Page(page_overview, title="今日概览", icon=":material/home:", default=True),
    st.Page(page_stocks, title="A 类 · 低波动选股", icon=":material/trending_up:", url_path="stocks"),
    st.Page(page_ipo, title="B 类 · 新股与 IPO", icon=":material/rocket_launch:", url_path="ipo"),
    st.Page(page_help, title="使用说明", icon=":material/help:", url_path="help"),
])
st.sidebar.caption("仅供研究学习，不构成投资建议。投资有风险，可能亏损本金。")
pages.run()
