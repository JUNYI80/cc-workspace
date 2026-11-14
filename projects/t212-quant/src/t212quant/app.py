"""量化助手 — the desktop app. Start it with the launcher, or:

    streamlit run src/t212quant/app.py

Set T212Q_DEMO=1 to run on random prices and fictional companies (offline).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script without install

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from t212quant import (  # noqa: E402
    alerts, appstate, backtest, data, demo, ipo, metrics, news, rebalance, research, screener, ui,
)
from t212quant.broker import Trading212Client  # noqa: E402
from t212quant.config import load_config  # noqa: E402
from t212quant.strategy import compute_signal  # noqa: E402

DEMO = os.getenv("T212Q_DEMO") == "1"
SCREEN_START = "2010-01-01"
PLAN_MAX_AGE_S = 15 * 60  # an order plan older than this must be regenerated
ICONS = {"warn": ":material/warning:", "ok": ":material/check_circle:", "info": ":material/info:"}

st.set_page_config(page_title="量化助手", page_icon=":material/insights:", layout="wide")
if DEMO:  # never touch the real settings, history or API keys
    appstate.use_state_dir(Path(tempfile.gettempdir()) / "t212q-demo-state")
appstate.load_env_file()
settings = appstate.load_settings()
if DEMO:
    settings["watchlist"] = demo.WATCHLIST
ui.inject_css()
ui.brand("量化助手", "美股 · Trading 212")


# -- data (cached) ---------------------------------------------------------------

@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_universe() -> screener.Universe:
    return demo.universe() if DEMO else screener.sp500_universe()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_stock_prices(symbols: tuple[str, ...]) -> pd.DataFrame:
    cols = list(symbols) + [screener.MARKET, screener.CASH]
    if DEMO:
        return demo.prices(cols, cash=screener.CASH)
    raw = data.load_prices(cols, SCREEN_START, allow_missing=True)
    return data.completed_closes(raw)


def _screen_inputs(top_n: int, regime: bool):
    universe = get_universe()
    prices = get_stock_prices(tuple(universe.symbols))
    return universe, prices, screener.ScreenParams(top_n=top_n, regime_filter=regime)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_picks(top_n: int, regime: bool) -> screener.Picks:
    universe, prices, p = _screen_inputs(top_n, regime)
    return screener.current_picks(prices, universe, p)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_screen_backtest(top_n: int, regime: bool) -> dict:
    universe, prices, p = _screen_inputs(top_n, regime)
    return screener.run_backtest(prices, universe.symbols, p)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_fundamentals(symbols: tuple[str, ...]) -> pd.DataFrame:
    return demo.fundamentals(list(symbols)) if DEMO else screener.fundamentals(list(symbols))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_stock_study(symbol: str) -> research.Study:
    cfg = research.build_config([symbol])
    prices = (demo.prices(cfg.all_symbols, years=15, cash=cfg.cash_symbol) if DEMO
              else data.load_prices(cfg.all_symbols, cfg.start))
    return research.run(cfg, prices)


@st.cache_data(ttl=3600, show_spinner=False)
def get_calendar() -> tuple[pd.DataFrame | None, str | None]:
    if DEMO:
        return demo.calendar(), None
    try:
        return ipo.fetch_calendar(months_back=12, months_ahead=1), None
    except Exception as exc:
        return None, str(exc)


@st.cache_data(ttl=3600, show_spinner=False)
def get_watch(entry: str, sec_contact: str) -> ipo.WatchStatus:
    name = ipo.aliases(entry)[0]
    calendar, _ = get_calendar()
    errors = []
    if DEMO:
        heads, filings = demo.headlines(entry), []
    else:
        try:
            heads = news.search(f'"{name}" IPO', limit=30)
        except Exception:
            heads = []
            errors.append("新闻暂时无法获取")
        filings = []
        if "@" not in sec_contact:
            errors.append("未查询 SEC 官方文件：SEC 要求查询者提供联系邮箱，可在『账户与设置』填写后启用。")
        else:
            try:
                filings = ipo.sec_filings(entry, sec_contact)
            except Exception:
                errors.append("SEC 官方文件暂时无法获取，稍后刷新再试。")
    return ipo.watch_status(entry, heads, filings, calendar, errors=errors)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_ipo_prices(symbols: tuple[str, ...]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    if DEMO:
        return demo.prices(list(symbols), years=1)
    start = (pd.Timestamp.today() - pd.DateOffset(days=400)).strftime("%Y-%m-%d")
    return data.load_prices(list(symbols), start, allow_missing=True)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_etf():
    cfg = load_config()
    prices = (demo.prices(cfg.all_symbols, years=15, cash=cfg.cash_symbol) if DEMO
              else data.load_prices(cfg.all_symbols, cfg.start))
    prices = data.completed_closes(prices)
    sig = compute_signal(prices[cfg.strategy_symbols], cfg.risk_symbols, cfg.cash_symbol,
                         cfg.strategy)
    return cfg, prices, sig, backtest.run(cfg, prices)


# -- small UI helpers ----------------------------------------------------------------

STOCK_LABELS = {"Strategy": "量化选股", "S&P 500 (SPY)": "标普 500",
                "All stocks, equal weight": "全部股票平均持有"}
IPO_STEPS = ["有报道", "准备上市", "秘密递交", "公开递交", "路演定价", "已上市"]
ADVICE = {"ok": "✓ 可关注", "info": "… 观察", "warn": "✕ 回避"}


def md(text: str) -> str:
    """Escape `$` so Streamlit doesn't render dollar amounts as maths."""
    return str(text).replace("$", "\\$")


def show_alert(a: alerts.Alert) -> None:
    box = {"warn": st.warning, "ok": st.success, "info": st.info}[a.level]
    box(f"**{md(a.area)}**　{md(a.text)}", icon=ICONS[a.level])


def etf_labels(cfg) -> dict[str, str]:
    names = {"Buy & hold VWRL": "一直持有 VWRL（全球股票）", "60/40 VWRL/IGLT": "60/40 股债组合"}
    return {b.name: names.get(b.name, b.name) for b in cfg.benchmarks}


def verdict(results: dict, labels: dict[str, str], strategy: str = "Strategy") -> str:
    """One honest sentence per benchmark: did the strategy earn more, and lose less?"""
    s = metrics.summary(results[strategy].nav)
    parts = []
    for k, r in results.items():
        if k == strategy:
            continue
        b = metrics.summary(r.nav)
        more = "高于" if s["CAGR"] > b["CAGR"] else "低于"
        safer = "更小" if s["Max drawdown"] > b["Max drawdown"] else "更大"
        parts.append(f"比『{labels[k]}』：年化收益{more}（{s['CAGR']:+.1%} 对 {b['CAGR']:+.1%}），"
                     f"最大回撤{safer}（{s['Max drawdown']:.0%} 对 {b['Max drawdown']:.0%}）")
    return f"过去 {s['Years']:.0f} 年的回测结论：" + "；".join(parts) + "。"


def headline_tiles(results: dict, labels: dict[str, str], unit: str, strategy: str = "Strategy") -> None:
    """The strategy's key numbers, each compared with the first benchmark."""
    bench = next(k for k in results if k != strategy)
    s, b = metrics.summary(results[strategy].nav), metrics.summary(results[bench].nav)
    worst = metrics.calendar_returns(results[strategy].nav).min()
    name = labels[bench]
    c = st.columns(4)
    c[0].metric("年化收益", f"{s['CAGR']:+.1%}", f"{(s['CAGR'] - b['CAGR']) * 100:+.1f} 个百分点", border=True)
    c[1].metric("最大回撤（最多亏过）", f"{s['Max drawdown']:.0%}",
                f"{(s['Max drawdown'] - b['Max drawdown']) * 100:+.0f} 个百分点", border=True)
    c[2].metric("最差的一年", f"{worst:+.1%}", border=True)
    c[3].metric(f"1 万{unit}变成", f"{10_000 * results[strategy].nav.iloc[-1]:,.0f}", border=True)
    st.caption(f"小字是和『{name}』相比的差距（{name}：年化 {b['CAGR']:+.1%}，最大回撤 {b['Max drawdown']:.0%}，"
               f"1 万{unit}变成 {10_000 * results[bench].nav.iloc[-1]:,.0f}）。")


def show_backtest(results: dict, labels: dict[str, str], unit: str, strategy: str = "Strategy") -> None:
    st.info(md(verdict(results, labels, strategy)), icon=ICONS["info"])
    headline_tiles(results, labels, unit, strategy)
    ui.performance_charts(results, labels)
    ui.yearly_chart(results, labels, metrics.calendar_returns)
    with st.expander("详细数字"):
        st.table(summary_table(results, labels, unit))


def summary_table(results: dict, labels: dict[str, str], unit: str) -> pd.DataFrame:
    rows = {}
    for k, r in results.items():
        s = metrics.summary(r.nav)
        cal = metrics.calendar_returns(r.nav)
        rows[labels[k]] = {
            f"本金 1 万{unit}变成": f"{10_000 * r.nav.iloc[-1]:,.0f}",
            "年化收益": f"{s['CAGR']:+.1%}",
            "年化波动": f"{s['Volatility']:.1%}",
            "夏普比率": f"{s['Sharpe']:.2f}",
            "最大回撤（最多亏过）": f"{s['Max drawdown']:.1%}",
            "最差的一年": f"{cal.min():+.1%}" if len(cal) else "-",
        }
    return pd.DataFrame(rows)


def demo_banner() -> None:
    if DEMO:
        st.warning("演示模式：以下全部是随机生成的数据和虚构公司，只用来展示界面。", icon=":material/science:")


def loading(msg: str = "正在下载最新数据……第一次打开需要 1–3 分钟，之后会快很多。"):
    return st.spinner(msg)


def stock_settings() -> tuple[int, bool]:
    return int(settings["top_n"]), bool(settings["regime_filter"])


# -- pages ----------------------------------------------------------------------------

def page_overview() -> None:
    st.title("今日概览")
    st.caption(f"{date.today():%Y 年 %m 月 %d 日}　·　数据每 6 小时自动更新")
    demo_banner()
    today = date.today()
    history = appstate.load_history()
    items: list[alerts.Alert] = []
    top_n, regime = stock_settings()

    with loading():
        try:
            picks = get_picks(top_n, regime)
        except Exception as exc:
            picks = None
            st.error(f"股票数据暂时无法下载，请检查网络后刷新页面。（{exc}）")
        calendar, cal_error = get_calendar()
        statuses = [get_watch(e, settings["sec_contact"]) for e in settings["watchlist"]]

    cols = st.columns(4)
    if picks is not None:
        held = len(picks.table) if picks.market_ok else 0
        items.append(alerts.market(picks.market_vs_sma, regime))
        items += alerts.picks(picks.added, picks.removed, today, picks.market_ok)
        spy = get_stock_prices(tuple(get_universe().symbols))[screener.MARKET].dropna().iloc[-250:]
        cols[0].metric("标普 500（对比 200 日均线）", "向上" if picks.market_vs_sma > 0 else "向下",
                       f"{picks.market_vs_sma:+.1%}", border=True, height="stretch",
                       chart_data=spy.round(2).tolist(), chart_type="line")
        cols[1].metric("A 类本月持股", f"{held} 只" if held else "持有现金",
                       f"买入 {len(picks.added)} · 卖出 {len(picks.removed)}", delta_color="off",
                       delta_arrow="off", border=True, height="stretch")
        cols[3].metric("下次调仓日", f"{picks.next_rebalance:%m 月 %d 日}", border=True, height="stretch")
    for s in statuses:
        appstate.record_stage(history, s.name, s.stage, today)
    items += alerts.watchlist(statuses, history, today)
    if calendar is not None:
        items += alerts.ipo_calendar(ipo.upcoming_ipos(calendar, today, days=14),
                                     ipo.recent_ipos(calendar, today), today)
    elif cal_error:
        items.append(alerts.Alert("info", "B 类新股", "IPO 日历暂时无法获取，稍后刷新再试。"))
    items += alerts.etf(today, rebalance.already_traded_this_month("live", rebalance.DEFAULT_JOURNAL))
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
    st.caption("用真实历史数据回测。回测不代表未来；所有策略都可能连续一两年跑输简单持有指数。")
    try:
        left, right = st.columns(2)
        with left.container(border=True):
            st.markdown("**A 类 · 量化选股**")
            _health(get_screen_backtest(top_n, regime), STOCK_LABELS)
        with right.container(border=True):
            cfg, _, _, etf_res = get_etf()
            st.markdown("**ETF 轮动**")
            _health(etf_res, {"Strategy": "ETF 轮动", **etf_labels(cfg)})
    except Exception as exc:
        st.caption(f"回测暂时无法计算：{exc}")

    with st.container(border=True):
        st.markdown(
            "**每个月只需要做一次：**\n"
            "1. 每月第一个交易日打开本软件，看『A 类选股』的名单，在 Trading 212 里照着买卖。\n"
            "2. 看一眼『B 类新股』有没有新提醒。\n"
            "3. 平时不用盯盘。"
        )


def _health(results: dict, labels: dict[str, str]) -> None:
    bench = next(k for k in results if k != "Strategy")
    s, b = metrics.summary(results["Strategy"].nav), metrics.summary(results[bench].nav)
    c1, c2 = st.columns(2)
    c1.metric("年化收益", f"{s['CAGR']:+.1%}", f"{(s['CAGR'] - b['CAGR']) * 100:+.1f} 个百分点")
    c2.metric("最大回撤", f"{s['Max drawdown']:.0%}",
              f"{(s['Max drawdown'] - b['Max drawdown']) * 100:+.0f} 个百分点")
    st.caption(f"对比『{labels[bench]}』（年化 {b['CAGR']:+.1%}，最大回撤 {b['Max drawdown']:.0%}），"
               f"回测 {s['Years']:.0f} 年。")


def page_stocks() -> None:
    st.title("A 类 · 量化选股")
    st.caption("每月从标普 500 里选出涨势最强、最稳的股票。只看数据，不看公司故事。")
    demo_banner()

    c1, c2, c3 = st.columns([1, 1, 1])
    capital = c1.number_input("准备投入多少钱（英镑）", min_value=100.0, max_value=1_000_000.0,
                              value=float(settings["capital_gbp"]), step=100.0)
    top_n = c2.slider("买几只股票", 5, 30, int(settings["top_n"]),
                      help="越多越分散、越稳；越少越集中、波动越大。")
    regime = c3.toggle("大盘过滤", value=bool(settings["regime_filter"]),
                       help="开启后，标普 500 跌破 200 日均线时全部持有现金。过去 15 年里它让最大回撤小了一些，"
                            "但每年收益少了 6–10 个百分点，所以默认关闭。")
    if (capital, top_n, regime) != (settings["capital_gbp"], settings["top_n"], settings["regime_filter"]):
        settings.update(capital_gbp=capital, top_n=top_n, regime_filter=regime)
        if not DEMO:
            appstate.save_settings(settings)

    with loading():
        try:
            picks = get_picks(top_n, regime)
            prices = get_stock_prices(tuple(get_universe().symbols))
        except Exception as exc:
            st.error(f"股票数据暂时无法下载，请检查网络后刷新页面。（{exc}）")
            return
    t = picks.table
    st.caption(f"股票池：{get_universe().source}　·　名单按 {picks.as_of:%Y-%m-%d} 收盘计算，本月内不变　·　"
               f"行情截至 {picks.data_date:%Y-%m-%d}")

    if not picks.market_ok:
        st.warning("你开启了大盘过滤，标普 500 在月底时低于 200 日均线：本月**不买股票**，"
                   "把这部分钱留作现金。下面的名单仅供参考。", icon=ICONS["warn"])
    else:
        trend = (f"标普 500 比 200 日均线高 {picks.market_vs_sma:.1%}" if picks.market_vs_sma > 0
                 else f"注意：标普 500 比 200 日均线低 {-picks.market_vs_sma:.1%}，大盘偏弱")
        st.success(f"本月买入下面 {len(t)} 只，每只约 £{capital / top_n:,.0f}。{trend}。", icon=ICONS["ok"])
        if picks.added or picks.removed:
            st.info(f"相比上月名单　买入：{'、'.join(picks.added) or '无'}　｜　卖出：{'、'.join(picks.removed) or '无'}",
                    icon=":material/swap_horiz:")
        if len(t) < top_n:
            st.info(f"本月只有 {len(t)} 只股票符合条件，剩下的 £{capital * (1 - len(t) / top_n):,.0f} 留作现金。",
                    icon=ICONS["info"])

    with loading("正在获取公司基本面……"):
        f = get_fundamentals(tuple(t["symbol"]))
    sectors = [f["sector"].get(s) or sec or "未知" for s, sec in zip(t["symbol"], t["sector"])]
    table = pd.DataFrame({
        "排名": range(1, len(t) + 1),
        "代码": t["symbol"],
        "公司": [f["name"].get(s) or n for s, n in zip(t["symbol"], t["name"])],
        "行业": sectors,
        "半年走势": ui.sparkline_rows(prices, list(t["symbol"])),
        "近 12 个月涨幅": t["momentum"] * 100,
        "年化波动": t["vol"] * 100,
        "建议买入": capital / top_n if picks.market_ok else 0.0,
        "预期市盈率": [f["forward_pe"].get(s) for s in t["symbol"]],
        "提示": ["；".join(screener.warnings_for(f.loc[s])) if s in f.index else ""
               for s in t["symbol"]],
    })
    st.dataframe(table, hide_index=True, height=35 * (len(table) + 1) + 3, column_config={
        "排名": st.column_config.NumberColumn(width="small"),
        "公司": st.column_config.TextColumn(width="medium"),
        "半年走势": st.column_config.LineChartColumn(width="small", help="最近约 6 个月的股价走势"),
        "近 12 个月涨幅": st.column_config.NumberColumn(format="%.0f%%", help="12 个月前到 1 个月前的涨幅"),
        "年化波动": st.column_config.NumberColumn(format="%.0f%%", help="越低越稳"),
        "建议买入": st.column_config.NumberColumn(format="£%.0f"),
        "预期市盈率": st.column_config.NumberColumn(format="%.1f"),
    })

    left, right = st.columns([1, 1])
    with left:
        counts = pd.Series(sectors).value_counts().rename_axis("行业").reset_index(name="只数")
        ui.hbar(counts, "行业", "只数", "行业分布（只数）")
        if len(counts) and counts["只数"].iloc[0] > len(t) / 2:
            st.caption(f"提示：超过一半集中在『{counts['行业'].iloc[0]}』，行业风险较集中。")
    with right.container(border=True):
        st.markdown(
            "**在 Trading 212 里怎么操作**\n"
            "1. 打开 Trading 212 App，搜索上表的代码（例如 NVDA）。\n"
            "2. 点『买入』，选『按金额』（Value），输入建议金额。\n"
            f"3. 下次调仓日（{picks.next_rebalance:%m 月 %d 日}）回来：不在新名单里的卖掉，新进的买入。\n\n"
            "美股每笔交易 Trading 212 收约 0.15% 换汇费。建议放在 ISA 账户里，免资本利得税。"
        )

    tab1, tab2 = st.tabs(["历史回测", "单只股票分析"])
    with tab1:
        with loading("正在回测过去十几年的表现……"):
            res = get_screen_backtest(top_n, regime)
        show_backtest(res, STOCK_LABELS, "美元")
        st.caption("回测按美元计算，含每笔 0.25% 成本。注意：回测用的是今天的成分股，过去被踢出指数的"
                   "失败公司不在里面，所以三条线都被高估，选股策略高估得最多。过去表现不代表未来。")
    with tab2:
        sym = st.text_input("输入美股代码", value="NVDA", max_chars=10).strip().upper()
        if sym:
            with loading(f"正在分析 {sym}……"):
                try:
                    study = get_stock_study(sym)
                except Exception as exc:
                    st.error(f"找不到 {sym} 的数据，请检查代码。（{exc}）")
                    return
            row = study.signal.table.loc[sym] if not study.signal.table.empty else None
            if row is not None:
                held = study.signal.weights[sym] > 0
                (st.success if held else st.warning)(
                    f"{sym} 当前信号：{'持有' if held else '空仓'}。"
                    f"比 200 日均线 {row['vs_sma200']:+.1%}，近 3/6/12 个月平均涨幅 {row['score']:+.1%}。")
            labels = {"Strategy": "趋势规则", study.cfg.benchmarks[0].name: f"一直持有 {sym}",
                      study.cfg.benchmarks[1].name: "标普 500"}
            show_backtest(study.results, labels, "美元")
            st.caption("趋势规则：股价在 200 日均线之上、且近期涨幅跑赢现金时持有，否则拿现金。"
                       "它主要帮你躲开大跌，不保证赚得更多。")


def page_ipo() -> None:
    st.title("B 类 · 新股与 IPO")
    st.caption("跟踪还没上市的公司离 IPO 还有多远，以及刚上市的新股该不该碰。")
    demo_banner()
    tab1, tab2, tab3 = st.tabs(["关注的公司", "IPO 日历", "新股提醒"])

    with tab1:
        with loading("正在搜索新闻和 SEC 文件……"):
            statuses = [get_watch(e, settings["sec_contact"]) for e in settings["watchlist"]]
        shared = sorted({e for s in statuses for e in s.errors})
        for e in shared:
            st.caption(f":orange[{e}]")
        for s in statuses:
            with st.container(border=True):
                head, badge = st.columns([4, 1])
                head.subheader(s.name)
                if s.stage:
                    badge.badge("官方数据" if s.official else "据新闻", color="blue" if s.official else "gray",
                                icon=":material/verified:" if s.official else ":material/newspaper:")
                ui.stepper(s.stage, IPO_STEPS, s.official)
                st.markdown(f"**{s.label}**" + (f"　·　近 30 天相关新闻 {s.heat} 条" if s.heat else ""))
                if s.evidence:
                    st.caption(f"依据：{md(s.evidence)}")
                if s.filings:
                    st.markdown("**SEC 上市申请文件**")
                    for fl in s.filings[:5]:
                        st.markdown(f"- {fl.filed}　{fl.form}　[{md(fl.company)}]({fl.url})")
                if s.headlines:
                    with st.expander("最新新闻"):
                        for h in s.headlines:
                            when = f"{h.published:%m-%d}" if h.published else ""
                            st.markdown(f"- {when}　[{md(h.title)}]({h.link})　*{md(h.source)}*")
        with st.expander("编辑关注名单"):
            text = st.text_area("每行一家公司。可用 | 写上正式名称，方便搜索 SEC 文件。",
                                value="\n".join(settings["watchlist"]), height=150)
            if st.button("保存名单"):
                settings["watchlist"] = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if not DEMO:
                    appstate.save_settings(settings)
                st.rerun()
        st.caption("进度是根据公开新闻和官方数据自动判断的，带『据新闻』的还没有官方证实。"
                   "Trading 212 一般不提供新股认购，只能在上市后买入。")

    calendar, error = get_calendar()
    with tab2:
        if calendar is None:
            st.warning(f"IPO 日历暂时无法获取，稍后再试。（{error}）")
        else:
            up = ipo.upcoming_ipos(calendar, days=30)
            st.subheader("即将上市")
            if up.empty:
                st.info("未来 30 天没有已公布定价日的 IPO。")
            else:
                st.dataframe(_ipo_table(up, "预计定价日"), hide_index=True)
            recent = calendar[calendar["status"] == "priced"].head(25)
            st.subheader("最近上市")
            st.dataframe(_ipo_table(recent, "上市日"), hide_index=True)
            st.caption("来源：Nasdaq 官网 IPO 日历。")

    with tab3:
        if calendar is None:
            st.warning("IPO 日历暂时无法获取。")
            return
        recent = ipo.recent_ipos(calendar)
        if recent.empty:
            st.info("过去一年没有募资 5000 万美元以上的新股。")
            return
        with loading("正在获取新股价格……"):
            px = get_ipo_prices(tuple(recent["symbol"]))
        rows = []
        for _, r in recent.iterrows():
            closes = px[r["symbol"]].dropna() if r["symbol"] in px.columns else pd.Series(dtype=float)
            adv = ipo.post_ipo_advice(r["date"], r["ipo_price"], closes if len(closes) else None)
            last = float(closes.iloc[-1]) if len(closes) else None
            rows.append({"上市日": r["date"], "代码": r["symbol"], "公司": r["company"],
                         "发行价": r["ipo_price"], "现价": last,
                         "相对发行价": (last / r["ipo_price"] - 1) * 100 if last and r["ipo_price"] else None,
                         "建议": ADVICE[adv.level], "说明": adv.text})
        df = pd.DataFrame(rows)
        c = st.columns(3)
        for col, level in zip(c, ("ok", "info", "warn")):
            col.metric(ADVICE[level], f"{(df['建议'] == ADVICE[level]).sum()} 只", border=True)
        priced = df.dropna(subset=["相对发行价"]).head(25)
        if not priced.empty:
            chart = priced.assign(名称=priced["代码"] + "　" + priced["公司"].str.slice(0, 22),
                                  涨跌=priced["相对发行价"] / 100)
            ui.hbar(chart, "名称", "涨跌", "最近上市的新股：现价相对发行价", fmt="+.0%",
                    sign_labels=("高于发行价", "低于发行价"))
        choice = st.radio("筛选", ["全部"] + list(ADVICE.values()), horizontal=True, label_visibility="collapsed")
        shown = df if choice == "全部" else df[df["建议"] == choice]
        st.dataframe(shown, hide_index=True, column_config={
            "发行价": st.column_config.NumberColumn(format="$%.2f"),
            "现价": st.column_config.NumberColumn(format="$%.2f"),
            "相对发行价": st.column_config.NumberColumn(format="%+.0f%%"),
            "公司": st.column_config.TextColumn(width="medium"),
            "说明": st.column_config.TextColumn(width="large"),
        })
        st.caption("只列出过去一年募资 5000 万美元以上的公司，不含 SPAC。规则依据：新股长期平均跑输大盘"
                   "（Ritter 1991）；锁定期（通常约 180 天）到期前后股价平均偏弱（Field & Hanka 2001）。"
                   "锁定期是估算，准确日期以招股书为准。")


def _ipo_table(df: pd.DataFrame, date_label: str) -> pd.DataFrame:
    price = ["待定" if pd.isna(lo) else f"${lo:.2f}" if lo == hi else f"${lo:.2f}–{hi:.2f}"
             for lo, hi in zip(df["price_low"], df["price_high"])]
    return pd.DataFrame({
        date_label: df["date"], "代码": df["symbol"].fillna("待定"), "公司": df["company"],
        "交易所": df["exchange"], "价格": price,
        "募资（百万美元）": (df["value_usd"] / 1e6).round(0),
    })


def page_etf() -> None:
    st.title("ETF 轮动（可自动下单）")
    st.caption("在 10 只英镑计价的 ETF 里每月轮动，可以连接 Trading 212 自动调仓。")
    demo_banner()
    with loading():
        try:
            cfg, prices, sig, res = get_etf()
        except Exception as exc:
            st.error(f"ETF 数据暂时无法下载。（{exc}）")
            return

    st.subheader(f"本月目标持仓（截至 {sig.as_of:%Y-%m-%d}）")
    held = sig.weights[sig.weights > 0].sort_values(ascending=False)
    ui.hbar(pd.DataFrame({"ETF": [f"{s.replace('.L', '')}　{cfg.asset(s).name}" for s in held.index],
                          "仓位": held.to_numpy()}), "ETF", "仓位", "目标仓位", fmt=".0%", height_per_row=34)

    with st.expander("历史回测", expanded=True):
        show_backtest(res, {"Strategy": "ETF 轮动", **etf_labels(cfg)}, "英镑")

    st.subheader("连接 Trading 212 调仓")
    env_label = st.radio("账户", ["模拟账户（Practice）", "真实账户（Live）"], horizontal=True)
    env = "demo" if env_label.startswith("模拟") else "live"
    if not (os.getenv(f"T212_API_KEY_{env.upper()}") or os.getenv("T212_API_KEY")):
        st.info("还没有填写这个账户的 API key：先到『账户与设置』页面填写。", icon=ICONS["info"])
        return
    if st.button("生成调仓计划", type="primary"):
        try:
            client = Trading212Client.from_env(env)
            plan = rebalance.plan_from_account(cfg, client, prices, sig.weights)
            st.session_state["etf_plan"] = (env, plan, time.time())
        except Exception as exc:
            st.error(f"无法生成计划：{exc}")
    saved = st.session_state.get("etf_plan")
    if not saved or saved[0] != env:
        return
    plan, made_at = saved[1], saved[2]
    st.code(plan.describe(cfg.account_currency), language=None)
    if time.time() - made_at > PLAN_MAX_AGE_S:
        st.warning("这个计划已超过 15 分钟，价格可能变了：请重新点『生成调仓计划』。", icon=ICONS["warn"])
        return
    if plan.problems or not plan.orders:
        st.info("没有可以执行的订单。" if not plan.problems else "计划有问题，不能执行。")
        return
    blockers = rebalance.execution_blockers(env, rebalance.DEFAULT_JOURNAL)
    messages = {
        rebalance.LIVE_NOT_ALLOWED: "真实账户下单未开启：到『账户与设置』勾选『允许真实账户下单』。",
        rebalance.MARKET_CLOSED: "伦敦交易所现在不在交易时间（工作日 08:15–16:15），市价单会排队到开盘。",
        rebalance.ALREADY_TRADED: "这个月已经调过仓了。",
    }
    for b in blockers:
        st.warning(messages[b], icon=ICONS["warn"])
    confirm = True
    if env == "live":
        confirm = st.text_input("真实账户会用真钱下单。确认请输入：我确认") == "我确认"
    if st.button(f"在{'模拟' if env == 'demo' else '真实'}账户下单", disabled=bool(blockers) or not confirm):
        client = Trading212Client.from_env(env)
        rows = rebalance.execute(plan, client, cfg.execution)
        failed = [r for r in rows if "error" in r or "abort" in r]
        (st.error if failed else st.success)(
            f"完成：{len(rows) - len(failed)} 笔成功，{len(failed)} 笔失败或中止。")
        for r in failed:
            st.caption(r.get("error") or r.get("abort"))
        del st.session_state["etf_plan"]


SECRET_FIELDS = [
    ("T212_API_KEY_DEMO", "模拟账户 API key"), ("T212_API_SECRET_DEMO", "模拟账户 API secret"),
    ("T212_API_KEY_LIVE", "真实账户 API key"), ("T212_API_SECRET_LIVE", "真实账户 API secret"),
]


def page_account() -> None:
    st.title("账户与设置")
    env_values = appstate.read_env_file()
    with st.form("keys", clear_on_submit=True):
        st.markdown("**Trading 212 API key**　在 Trading 212 App → 设置 → API (Beta) 生成。"
                    "先用模拟账户（Practice）的 key 练习。")
        cols = st.columns(2)
        entered = {}
        for i, (key, label) in enumerate(SECRET_FIELDS):
            saved = bool(env_values.get(key))
            # A saved key is never sent back to the browser: leave the box empty to keep it.
            entered[key] = cols[i % 2].text_input(
                label, type="password", placeholder="已保存；留空表示不修改" if saved else "还没有填写")
        allow_live = st.checkbox("允许真实账户下单（不勾选时只能看，不能下单）",
                                 value=env_values.get("T212_ALLOW_LIVE") == "yes")
        contact = st.text_input("联系邮箱（可选，SEC 要求查询者提供，用于 B 类新股数据）",
                                settings.get("sec_contact", ""))
        clear = st.checkbox("清除所有已保存的 API key")
        if st.form_submit_button("保存", type="primary"):
            updates = {k: "" for k, _ in SECRET_FIELDS} if clear else \
                {k: v.strip() for k, v in entered.items() if v.strip()}
            updates["T212_ALLOW_LIVE"] = "yes" if allow_live and not clear else "no"
            appstate.save_env_values(updates)
            settings["sec_contact"] = contact.strip()
            appstate.save_settings(settings)
            st.success("已保存在本机的 .env 文件里（不会上传）。")
    st.caption("API key 只保存在这台电脑上。不要发给任何人；如果泄露，在 Trading 212 里删除它再生成新的。")

    st.subheader("查看账户")
    env_label = st.radio("账户", ["模拟账户（Practice）", "真实账户（Live）"], horizontal=True,
                         key="acct_env")
    env = "demo" if env_label.startswith("模拟") else "live"
    if st.button("连接并查看"):
        try:
            client = Trading212Client.from_env(env)
            cash = client.cash()
            positions = client.portfolio()
        except Exception as exc:
            st.error(f"连接失败：{exc}")
            return
        c1, c2, c3 = st.columns(3)
        c1.metric("总资产", f"£{cash.get('total', 0):,.2f}", border=True)
        c2.metric("可用现金", f"£{cash.get('free', 0):,.2f}", border=True)
        c3.metric("浮动盈亏", f"£{cash.get('ppl', 0):,.2f}", border=True)
        if positions:
            st.dataframe(pd.DataFrame([{
                "代码": p["ticker"], "数量": p["quantity"], "均价": p["averagePrice"],
                "现价": p["currentPrice"], "盈亏 £": p.get("ppl", 0)} for p in positions]),
                hide_index=True)
        else:
            st.info("这个账户目前没有持仓。")


def page_help() -> None:
    st.title("使用说明")
    st.markdown("""
**这个软件做三件事**

| 板块 | 做什么 | 你要做什么 |
|---|---|---|
| A 类 · 量化选股 | 每月从标普 500 里选出涨势最强、最稳的 20 只股票 | 每月一次，照名单在 Trading 212 买卖 |
| B 类 · 新股与 IPO | 跟踪 Anthropic 这类公司的上市进度，提醒新股的风险点 | 看提醒，自己决定 |
| ETF 轮动 | 在 10 只 ETF 里轮动，可以自动下单 | 可选，适合想省事的人 |

**A 类的原理**（量化基金几十年来最常用的『动量』因子）
- 过去一年涨得好、而且涨得稳的股票，接下来几个月更可能继续涨。
- 只买股价在 200 日均线之上的股票。
- 名单按上个月最后一个交易日的收盘数据计算，一个月内固定不变，和回测的做法一致。
- 可选的『大盘过滤』：标普 500 跌破 200 日均线时全部换成现金。用 2011–2026 年真实数据回测，它让最大回撤从约 -45% 降到约 -38%，但每年收益少了 6–10 个百分点，所以默认关闭，想更稳可以打开。

**B 类的原理**
- 公司上市要经过：传闻 → 聘请投行 → 秘密递交 → 公开递交 S-1 → 路演定价 → 上市。软件根据新闻和 SEC 文件判断走到了哪一步。
- 研究显示：新股首日追高、长期持有，平均跑输大盘；锁定期（约 180 天）到期前后常有抛压。所以策略是：不追首日，等趋势和财报。

**一定要知道的风险**
- 没有任何策略能保证赚钱。动量策略也会连续一两年跑输大盘，偶尔会在市场急转时亏得很快。
- 回测是用历史数据模拟的，会高估真实表现。
- 只投亏得起的钱；建议先用 Trading 212 的模拟账户跑几个月。

本软件仅供研究学习，不构成投资建议。
""")


pages = st.navigation([
    st.Page(page_overview, title="今日概览", icon=":material/home:", default=True),
    st.Page(page_stocks, title="A 类 · 量化选股", icon=":material/trending_up:", url_path="stocks"),
    st.Page(page_ipo, title="B 类 · 新股与 IPO", icon=":material/rocket_launch:", url_path="ipo"),
    st.Page(page_etf, title="ETF 轮动", icon=":material/autorenew:", url_path="etf"),
    st.Page(page_account, title="账户与设置", icon=":material/settings:", url_path="account"),
    st.Page(page_help, title="使用说明", icon=":material/help:", url_path="help"),
])
st.sidebar.caption("仅供研究学习，不构成投资建议。投资有风险，可能亏损本金。")
pages.run()
