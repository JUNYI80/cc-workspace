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

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from cnquant import alerts, appstate, backtest, data, demo, ipo, screener  # noqa: E402
from cnquant.factors import Params  # noqa: E402

DEMO = os.getenv("CNQ_DEMO") == "1"
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # categorical slots 1-4 of the data-viz palette
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
    cands = list(screener.candidates(m, p).index)
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
    if DEMO or not symbols:
        return pd.DataFrame()
    try:
        close, _ = data.download(list(symbols), (date.today() - timedelta(days=400)).isoformat())
        return close
    except Exception:
        return pd.DataFrame()


# -- UI helpers ----------------------------------------------------------------------

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


def growth_chart(results: dict) -> alt.LayerChart:
    order = list(results)
    df = pd.concat([
        pd.DataFrame({"日期": r.nav.resample("W").last().index, "组合": k,
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


def summary_table(results: dict) -> pd.DataFrame:
    rows = {}
    for k, r in results.items():
        s = backtest.summary(r.nav)
        rows[k] = {
            "本金 10 万元变成": f"{100_000 * r.nav.iloc[-1]:,.0f}",
            "年化收益": f"{s['cagr']:+.1%}",
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
        month = f"{today:%Y-%m}"
        codes = list(picks.table["yahoo"]) if picks.market_ok and not picks.table.empty else []
        appstate.record_picks(history, month, codes)
        items.append(alerts.market(picks.market_vs_sma))
        items += alerts.picks(codes, get_market().names(), history, month, today, picks.market_ok)
        cols[0].metric("沪深 300 趋势", "向上" if picks.market_vs_sma > 0 else "向下",
                       f"{picks.market_vs_sma:+.1%} 相对 200 日均线", border=True)
        cols[1].metric("A 类本月持股", f"{len(codes)} 只" if codes else "持有现金", border=True)
        cols[3].metric("下次调仓日", f"{picks.next_rebalance:%m 月 %d 日}", border=True)
    for s in statuses:
        appstate.record_stage(history, s.name, s.stage, today)
    items += alerts.watchlist(statuses, history, today)
    if subs is not None:
        items += alerts.subscriptions(subs)
    if listings is not None:
        items += alerts.unlocks(listings, today)
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
        st.caption("A 类 · 多因子选股：" + md(verdict(get_backtest(top_n, regime))))
    except Exception as exc:
        st.caption(f"回测暂时无法计算：{exc}")
    st.caption("回测不代表未来；所有策略都可能连续一两年跑输指数。")
    with st.container(border=True):
        st.markdown(
            "**每个月只需要做一次：**\n"
            "1. 每月第一个交易日打开本软件，看『A 类选股』的名单，在你的证券 App 里照着买卖。\n"
            "2. 看一眼『B 类新股』有没有打新或解禁提醒。\n"
            "3. 平时不用盯盘。"
        )
    st.caption(SOURCES)


def page_stocks() -> None:
    st.title("A 类 · 多因子选股")
    st.caption("每月从中证 800（沪深 300 + 中证 500）里，按 A 股最稳健的三个因子选股。只看数据，不听消息。")
    demo_banner()
    c1, c2, c3 = st.columns([1, 1, 1])
    capital = c1.number_input("准备投入多少钱（元）", min_value=10_000.0, max_value=100_000_000.0,
                              value=float(settings["capital_cny"]), step=10_000.0)
    top_n = c2.slider("买几只股票", 5, 30, int(settings["top_n"]),
                      help="越多越分散、越稳；资金少时减少只数，否则很多股票买不起一手。")
    regime = c3.toggle("大盘过滤", value=bool(settings["regime_filter"]),
                       help="开启后，沪深 300 跌破 200 日均线时全部持有现金。")
    if (capital, top_n, regime) != (settings["capital_cny"], settings["top_n"], settings["regime_filter"]):
        settings.update(capital_cny=capital, top_n=top_n, regime_filter=regime)
        if not DEMO:
            appstate.save_settings(settings)

    with loading():
        try:
            picks = get_picks(top_n, regime, capital)
        except Exception as exc:
            st.error(f"行情数据暂时无法下载，请检查网络后刷新页面。（{exc}）")
            return
    st.caption(f"数据截至 {picks.as_of:%Y-%m-%d} 收盘")
    if picks.market_ok:
        st.success(f"本月买入下面 {len(picks.table)} 只，每只约 ¥{capital / top_n:,.0f}。", icon=ICONS["ok"])
    else:
        st.warning(f"沪深 300 跌破 200 日均线（{picks.market_vs_sma:+.1%}）：本月**不买股票**，持有现金"
                   "（或货币基金）。下面的名单仅供参考。", icon=ICONS["warn"])
    t = picks.table
    if t.empty:
        st.info("今天没有符合条件的股票。")
        return
    tips = []
    for _, r in t.iterrows():
        tip = []
        if picks.market_ok and r["shares"] == 0:
            tip.append("钱不够买一手" if r["board"] != "科创板" else "钱不够买 200 股")
        if r["pe"] is not None and isinstance(r["pe"], float) and r["pe"] > 80:
            tip.append("估值很高")
        tips.append("；".join(tip))
    table = pd.DataFrame({
        "排名": range(1, len(t) + 1), "代码": t["code"], "名称": t["name"], "板块": t["board"],
        "近 1 个月涨跌": t["reversal"] * 100, "异常换手": t["abnormal_turnover"],
        "年化波动": t["volatility"] * 100, "市盈率": t["pe"], "最新价": t["price"],
        "建议买入（股）": t["shares"], "约需金额": t["cost"], "提示": tips,
    })
    st.dataframe(table, hide_index=True, column_config={
        "近 1 个月涨跌": st.column_config.NumberColumn(format="%.1f%%"),
        "异常换手": st.column_config.NumberColumn(format="%.2f", help="近 1 个月平均成交量 ÷ 近 1 年平均成交量，越低越好"),
        "年化波动": st.column_config.NumberColumn(format="%.1f%%", help="越低越稳"),
        "市盈率": st.column_config.NumberColumn(format="%.1f"),
        "最新价": st.column_config.NumberColumn(format="¥%.2f"),
        "约需金额": st.column_config.NumberColumn(format="¥%.0f"),
        "提示": st.column_config.TextColumn(width="medium"),
    })
    st.caption(f"买入合计约 ¥{t['cost'].sum():,.0f}，剩余资金留作现金。")
    with st.container(border=True):
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
        st.altair_chart(growth_chart(res))
        st.table(summary_table(res))
        st.caption("回测含买卖成本（买 0.15%、卖 0.20%，卖出含印花税），现金按年化 1.5% 计息。基准是沪深 300 ETF "
                   "（510300）和中证 500 ETF（510500）。注意：回测用的是今天的成分股，过去被剔除的公司不在里面"
                   "（幸存者偏差），会高估所有曲线；排除亏损公司这一步也只在实际选股时做，回测里没有。")
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
                st.subheader(s.name)
                st.progress(s.stage / 6, text=f"上市进度：**{s.label}**（{s.stage}/6）")
                if s.evidence:
                    st.caption(f"依据：{md(s.evidence)}")
                if s.terminated:
                    st.caption(f":orange[最近一次申请已终止：{s.terminated}]")
                st.caption(f"近 30 天相关新闻：{s.heat} 条")
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
                st.cache_data.clear()
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
            st.caption("发行公告发布后的下一个交易日一般是申购日，具体以公告为准。")

    with tab3:
        listings, err = get_listings()
        if listings is None:
            st.warning(f"巨潮资讯暂时无法获取。（{err}）")
        elif listings.empty:
            st.info("最近半年没有新股上市公告。")
        else:
            px = get_listing_prices(tuple(data.to_yahoo(c) for c in listings["code"]))
            for _, r in listings.iterrows():
                sym = data.to_yahoo(r["code"])
                closes = px[sym] if sym in px.columns else None
                adv = ipo.post_listing_advice(r["date"], closes)
                last = (f"，最新价 ¥{closes.dropna().iloc[-1]:.2f}"
                        if closes is not None and closes.notna().any() else "")
                show_alert(alerts.Alert(adv.level, f"{r['name']}（{r['code']}，{r['board']}）",
                                        f"约 {r['date']:%Y-%m-%d} 上市{last}。{adv.text}"))
            st.caption("规则：新股前 5 个交易日没有涨跌幅限制；上市满 6 个月和 12 个月有股份解禁。"
                       "解禁日期是估算，准确日期以公告为准。")

    with tab4:
        projects, err = get_recent_projects()
        if projects is None:
            st.warning(f"深交所审核信息暂时无法获取。（{err}）")
        else:
            st.markdown("深交所（创业板、深市主板）最近更新的 IPO 项目：")
            st.dataframe(pd.DataFrame([{"更新日期": p.updated, "公司": p.company, "状态": p.status,
                                        "行业": p.industry, "保荐机构": p.sponsor,
                                        "计划募资（亿元）": p.amount_yi} for p in projects]), hide_index=True)
    st.caption(SOURCES)


def page_help() -> None:
    st.title("使用说明")
    st.markdown("""
**这个软件做两件事**

| 板块 | 做什么 | 你要做什么 |
|---|---|---|
| A 类 · 多因子选股 | 每月从中证 800 里选出 20 只股票，算好每只买多少股 | 每月一次，照名单在证券 App 里买卖 |
| B 类 · 新股与 IPO | 跟踪公司的上市进度，提醒打新和解禁 | 看提醒，自己决定 |

**A 类的原理**（针对 A 股的特点，不是照搬美股）
- 在 A 股，过去一年涨得多的股票（动量）后面并不更强；反而上个月涨得多、成交突然放大、波动大的股票，之后更容易跑输。
- 所以每月选：近 1 个月涨得少、成交没有异常放大、波动小的股票，并排除 ST 股、停牌股、上市不满一年的股票和亏损公司。
- 沪深 300 跌破 200 日均线时全部换成现金（可以关掉），躲开大熊市。

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
    st.Page(page_stocks, title="A 类 · 多因子选股", icon=":material/trending_up:", url_path="stocks"),
    st.Page(page_ipo, title="B 类 · 新股与 IPO", icon=":material/rocket_launch:", url_path="ipo"),
    st.Page(page_help, title="使用说明", icon=":material/help:", url_path="help"),
])
st.sidebar.caption("仅供研究学习，不构成投资建议。投资有风险，可能亏损本金。")
pages.run()
