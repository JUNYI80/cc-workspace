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

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from t212quant import (  # noqa: E402
    alerts, appstate, backtest, data, demo, ipo, metrics, news, rebalance, research, screener,
)
from t212quant.broker import Trading212Client  # noqa: E402
from t212quant.config import load_config  # noqa: E402
from t212quant.strategy import compute_signal  # noqa: E402

DEMO = os.getenv("T212Q_DEMO") == "1"
COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]  # categorical slots 1-3 of the data-viz palette
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

def md(text: str) -> str:
    """Escape `$` so Streamlit doesn't render dollar amounts as maths."""
    return str(text).replace("$", "\\$")


def show_alert(a: alerts.Alert) -> None:
    box = {"warn": st.warning, "ok": st.success, "info": st.info}[a.level]
    box(f"**{md(a.area)}**　{md(a.text)}", icon=ICONS[a.level])


def growth_chart(results: dict, labels: dict[str, str]) -> alt.LayerChart:
    order = [labels[k] for k in results]
    df = pd.concat([
        pd.DataFrame({"日期": r.nav.resample("W").last().index, "组合": labels[k],
                      "净值": r.nav.resample("W").last().to_numpy()})
        for k, r in results.items()
    ])
    color = alt.Color("组合:N", scale=alt.Scale(domain=order, range=COLORS[: len(order)]),
                      legend=alt.Legend(orient="top", title=None))
    base = alt.Chart(df).encode(
        x=alt.X("日期:T", title=None),
        y=alt.Y("净值:Q", scale=alt.Scale(type="log"), title="1 份本金变成多少（对数坐标）"),
        color=color,
    )
    hover = alt.selection_point(fields=["日期"], nearest=True, on="pointerover", empty=False)
    rule = (alt.Chart(df).transform_pivot("组合", value="净值", groupby=["日期"])
            .mark_rule(color="#8a8a8a")
            .encode(x="日期:T", opacity=alt.condition(hover, alt.value(0.5), alt.value(0)),
                    tooltip=[alt.Tooltip("日期:T", title="日期")]
                    + [alt.Tooltip(f"{n}:Q", format=".2f") for n in order])
            .add_params(hover))
    points = base.mark_point(size=70, filled=True).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0)))
    return alt.layer(base.mark_line(strokeWidth=2), points, rule).properties(height=380)


def verdict(results: dict, labels: dict[str, str], strategy: str = "Strategy") -> str:
    """One honest sentence per benchmark: did the strategy earn more, and lose less?"""
    s = metrics.summary(results[strategy].nav)
    years = s["Years"]
    parts = []
    for k, r in results.items():
        if k == strategy:
            continue
        b = metrics.summary(r.nav)
        more = "高于" if s["CAGR"] > b["CAGR"] else "低于"
        safer = "更小" if s["Max drawdown"] > b["Max drawdown"] else "更大"
        parts.append(f"比『{labels[k]}』：年化收益{more}（{s['CAGR']:+.1%} 对 {b['CAGR']:+.1%}），"
                     f"最大回撤{safer}（{s['Max drawdown']:.0%} 对 {b['Max drawdown']:.0%}）")
    return f"过去 {years:.0f} 年的回测结论：" + "；".join(parts) + "。"


def show_backtest(results: dict, labels: dict[str, str], unit: str, strategy: str = "Strategy") -> None:
    st.info(md(verdict(results, labels, strategy)), icon=ICONS["info"])
    st.altair_chart(growth_chart(results, labels))
    st.table(summary_table(results, labels, unit))


def summary_table(results: dict, labels: dict[str, str], unit: str) -> pd.DataFrame:
    rows = {}
    for k, r in results.items():
        s = metrics.summary(r.nav)
        cal = metrics.calendar_returns(r.nav)
        rows[labels[k]] = {
            f"本金 1 万{unit}变成": f"{10_000 * r.nav.iloc[-1]:,.0f}",
            "年化收益": f"{s['CAGR']:+.1%}",
            "最大回撤（最多亏过）": f"{s['Max drawdown']:.1%}",
            "最差的一年": f"{cal.min():+.1%}" if len(cal) else "-",
        }
    return pd.DataFrame(rows)


def demo_banner() -> None:
    if DEMO:
        st.warning("演示模式：以下全部是随机生成的数据和虚构公司，只用来展示界面。", icon=":material/science:")


def loading(msg: str = "正在下载最新数据……第一次打开需要 1–3 分钟，之后会快很多。"):
    return st.spinner(msg)


# -- pages ----------------------------------------------------------------------------

def page_overview() -> None:
    st.title("今日概览")
    st.caption(f"{date.today():%Y 年 %m 月 %d 日}　·　数据每 6 小时自动更新")
    demo_banner()
    today = date.today()
    history = appstate.load_history()
    items: list[alerts.Alert] = []

    with loading():
        try:
            picks = get_picks(int(settings["top_n"]), bool(settings["regime_filter"]))
        except Exception as exc:
            picks = None
            st.error(f"股票数据暂时无法下载，请检查网络后刷新页面。（{exc}）")
        calendar, cal_error = get_calendar()
        statuses = [get_watch(e, settings["sec_contact"]) for e in settings["watchlist"]]

    cols = st.columns(4)
    if picks is not None:
        month = f"{today:%Y-%m}"
        symbols = list(picks.table["symbol"]) if picks.market_ok else []
        appstate.record_picks(history, month, symbols)
        items.append(alerts.market(picks.market_vs_sma, bool(settings["regime_filter"])))
        items += alerts.picks(symbols, history, month, today, picks.market_ok)
        cols[0].metric("美股大盘趋势", "向上" if picks.market_vs_sma > 0 else "向下",
                       f"{picks.market_vs_sma:+.1%} 相对 200 日均线", border=True)
        cols[1].metric("A 类本月持股", f"{len(symbols)} 只" if symbols else "持有现金", border=True)
        cols[3].metric("下次调仓日", f"{picks.next_rebalance:%m 月 %d 日}", border=True)
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
                   f"最高进度：{max((s.stage for s in statuses), default=0)}/6", border=True,
                   delta_color="off")

    st.subheader("提醒")
    if not items:
        st.info("今天没有需要处理的事情。", icon=ICONS["info"])
    for a in alerts.sort(items):
        show_alert(a)

    st.subheader("策略体检（用真实历史数据回测）")
    try:
        res = get_screen_backtest(int(settings["top_n"]), bool(settings["regime_filter"]))
        st.caption("A 类 · 量化选股：" + md(verdict(res, {"Strategy": "量化选股", "S&P 500 (SPY)": "标普 500",
                                                          "All stocks, equal weight": "全部股票平均持有"})))
        cfg, _, _, etf_res = get_etf()
        st.caption("ETF 轮动：" + md(verdict(etf_res, {"Strategy": "ETF 轮动",
                                                       **{b.name: b.name for b in cfg.benchmarks}})))
    except Exception as exc:
        st.caption(f"回测暂时无法计算：{exc}")
    st.caption("回测不代表未来；所有策略都可能连续一两年跑输简单持有指数。")

    with st.container(border=True):
        st.markdown(
            "**每个月只需要做一次：**\n"
            "1. 每月第一个交易日打开本软件，看『A 类选股』的名单，在 Trading 212 里照着买卖。\n"
            "2. 看一眼『B 类新股』有没有新提醒。\n"
            "3. 平时不用盯盘。"
        )


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
        except Exception as exc:
            st.error(f"股票数据暂时无法下载，请检查网络后刷新页面。（{exc}）")
            return
    universe = get_universe()
    st.caption(f"股票池：{universe.source}　·　数据截至 {picks.as_of:%Y-%m-%d} 收盘")

    if picks.market_ok:
        st.success(f"大盘趋势向上（标普 500 比 200 日均线 {picks.market_vs_sma:+.1%}）："
                   f"本月买入下面 {top_n} 只，每只约 £{capital / top_n:,.0f}。", icon=ICONS["ok"])
    else:
        st.warning(f"大盘跌破 200 日均线（{picks.market_vs_sma:+.1%}）：本月**不买股票**，"
                   "把这部分钱留作现金。下面的名单仅供参考。", icon=ICONS["warn"])

    t = picks.table
    if picks.market_ok and len(t) < top_n:
        st.info(f"本月只有 {len(t)} 只股票符合条件，剩下的 £{capital * (1 - len(t) / top_n):,.0f} "
                "留作现金。", icon=ICONS["info"])
    with loading("正在获取公司基本面……"):
        f = get_fundamentals(tuple(t["symbol"]))
    amount = capital / top_n if picks.market_ok else 0.0
    table = pd.DataFrame({
        "排名": range(1, len(t) + 1),
        "代码": t["symbol"],
        "公司": [f["name"].get(s) or n for s, n in zip(t["symbol"], t["name"])],
        "行业": [f["sector"].get(s) or sec for s, sec in zip(t["symbol"], t["sector"])],
        "近 12 个月涨幅": t["momentum"] * 100,
        "年化波动": t["vol"] * 100,
        "高于 200 日均线": t["vs_sma"] * 100,
        "建议买入 £": amount,
        "预期市盈率": [f["forward_pe"].get(s) for s in t["symbol"]],
        "提示": ["；".join(screener.warnings_for(f.loc[s])) if s in f.index else ""
               for s in t["symbol"]],
    })
    st.dataframe(table, hide_index=True, column_config={
        "公司": st.column_config.TextColumn(width="medium"),
        "近 12 个月涨幅": st.column_config.NumberColumn(format="%.1f%%",
                                                   help="12 个月前到 1 个月前的涨幅"),
        "年化波动": st.column_config.NumberColumn(format="%.1f%%", help="越低越稳"),
        "高于 200 日均线": st.column_config.NumberColumn(format="%.1f%%"),
        "建议买入 £": st.column_config.NumberColumn(format="£%.0f"),
        "预期市盈率": st.column_config.NumberColumn(format="%.1f"),
        "提示": st.column_config.TextColumn(width="large"),
    })

    with st.container(border=True):
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
        labels = {"Strategy": "量化选股", "S&P 500 (SPY)": "标普 500",
                  "All stocks, equal weight": "全部股票平均持有"}
        show_backtest(res, labels, "美元")
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
        for s in statuses:
            with st.container(border=True):
                st.subheader(s.name)
                st.progress(s.stage / 6, text=f"上市进度：**{s.label}**（{s.stage}/6）")
                if s.evidence:
                    st.caption(f"依据：{md(s.evidence)}")
                st.caption(f"近 30 天 IPO 相关新闻：{s.heat} 条")
                for e in s.errors:
                    st.caption(f":orange[{e}]")
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
                st.info("未来 30 天没有已公布的 IPO。")
            else:
                st.dataframe(_ipo_table(up, "预计定价日"), hide_index=True)
            recent = calendar[calendar["status"] == "priced"].head(25)
            st.subheader("最近上市")
            st.dataframe(_ipo_table(recent, "上市日"), hide_index=True)

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
        for _, r in recent.iterrows():
            closes = px[r["symbol"]] if r["symbol"] in px.columns else None
            adv = ipo.post_ipo_advice(r["date"], r["ipo_price"], closes)
            last = f"，现价 ${closes.dropna().iloc[-1]:.2f}" if closes is not None and closes.notna().any() else ""
            show_alert(alerts.Alert(adv.level, f"{r['company']}（{r['symbol']}）",
                                    f"{r['date']:%Y-%m-%d} 以 ${r['ipo_price']:.2f} 上市{last}。{adv.text}"))
        st.caption("规则依据：新股长期平均跑输大盘（Ritter 1991）；锁定期（通常约 180 天）到期前后"
                   "股价平均偏弱（Field & Hanka 2001）。锁定期是估算，准确日期以招股书为准。")


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
    st.dataframe(pd.DataFrame({
        "ETF": held.index, "名称": [cfg.asset(s).name for s in held.index],
        "目标仓位": held.values * 100,
    }), hide_index=True, column_config={"目标仓位": st.column_config.NumberColumn(format="%.1f%%")})

    with st.expander("历史回测", expanded=True):
        labels = {"Strategy": "ETF 轮动", **{b.name: b.name for b in cfg.benchmarks}}
        show_backtest(res, labels, "英镑")

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


def page_account() -> None:
    st.title("账户与设置")
    env_values = appstate.read_env_file()
    with st.form("keys"):
        st.markdown("**Trading 212 API key**　在 Trading 212 App → 设置 → API (Beta) 生成。"
                    "先用模拟账户（Practice）的 key 练习。")
        c1, c2 = st.columns(2)
        demo_key = c1.text_input("模拟账户 API key", env_values.get("T212_API_KEY_DEMO", ""), type="password")
        demo_secret = c2.text_input("模拟账户 API secret", env_values.get("T212_API_SECRET_DEMO", ""),
                                    type="password")
        live_key = c1.text_input("真实账户 API key", env_values.get("T212_API_KEY_LIVE", ""), type="password")
        live_secret = c2.text_input("真实账户 API secret", env_values.get("T212_API_SECRET_LIVE", ""),
                                    type="password")
        allow_live = st.checkbox("允许真实账户下单（不勾选时只能看，不能下单）",
                                 value=env_values.get("T212_ALLOW_LIVE") == "yes")
        contact = st.text_input("联系邮箱（可选，SEC 要求查询者提供，用于 B 类新股数据）",
                                settings.get("sec_contact", ""))
        if st.form_submit_button("保存", type="primary"):
            appstate.save_env_values({
                "T212_API_KEY_DEMO": demo_key.strip(), "T212_API_SECRET_DEMO": demo_secret.strip(),
                "T212_API_KEY_LIVE": live_key.strip(), "T212_API_SECRET_LIVE": live_secret.strip(),
                "T212_ALLOW_LIVE": "yes" if allow_live else "no",
            })
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
