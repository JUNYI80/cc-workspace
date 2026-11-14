"""The app's look: colour tokens for light and dark mode, charts and small HTML pieces.

Colours follow one validated palette (categorical slots in a fixed order, the
same hues stepped for the dark surface). Up/down is never shown as red/green:
that pair is unreadable for colour-blind readers and means the opposite in
Chinese and Western markets, so charts use blue/orange plus a text label.
"""
from __future__ import annotations

import html
import json
from typing import Callable

import altair as alt
import pandas as pd
import streamlit as st

FONT = 'system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif'
TOKENS = {
    "light": {
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
        "surface": "#fcfcfb", "text": "#0b0b0b", "text2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "axis": "#c3c2b7", "primary": "#2a78d6",
    },
    "dark": {
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500"],
        "surface": "#1a1a19", "text": "#f0efec", "text2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835", "primary": "#3987e5",
    },
}

LOG_TICKS = [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 5000]
FINE_TICKS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5]

CSS = """
<style>
.block-container {padding-top: 2.2rem; max-width: 1180px;}
[data-testid="stMetric"] {padding: 0.9rem 1rem;}
[data-testid="stMetricValue"] {font-size: 1.7rem;}
.qa-steps {display: flex; margin: 0.4rem 0 0.2rem; font-family: inherit;}
.qa-step {flex: 1; text-align: center; position: relative; font-size: 0.8rem; opacity: 0.45;}
.qa-step::before {content: ""; position: absolute; top: 9px; left: -50%; width: 100%; height: 2px;
                  background: currentColor; opacity: 0.35;}
.qa-step:first-child::before {display: none;}
.qa-dot {position: relative; display: inline-flex; align-items: center; justify-content: center;
         width: 20px; height: 20px; border-radius: 50%; border: 2px solid currentColor;
         background: var(--qa-surface); font-size: 11px; line-height: 1; font-weight: 700;}
.qa-lbl {display: block; margin-top: 0.3rem;}
.qa-step.done, .qa-step.now {opacity: 1;}
.qa-step.done::before, .qa-step.now::before {background: var(--qa-primary); opacity: 1;}
.qa-step.done .qa-dot {background: var(--qa-primary); border-color: var(--qa-primary); color: #fff;}
.qa-step.now .qa-dot {border-color: var(--qa-primary); color: var(--qa-primary);
                      box-shadow: 0 0 0 4px color-mix(in srgb, var(--qa-primary) 22%, transparent);}
.qa-step.now.official .qa-dot {background: var(--qa-primary); color: #fff;}
.qa-step.now .qa-lbl {font-weight: 700;}
.qa-brand {font-size: 1.15rem; font-weight: 700; margin: 0 0 0.1rem;}
.qa-sub {font-size: 0.8rem; opacity: 0.6; margin: 0 0 0.6rem;}
</style>
"""


def mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:  # older Streamlit, or no browser session (tests)
        return "light"


def tokens() -> dict:
    return TOKENS[mode()]


def inject_css() -> None:
    t = tokens()
    st.html(CSS + f"<style>:root {{--qa-primary: {t['primary']}; --qa-surface: {t['surface']};}}</style>")


def brand(title: str, subtitle: str) -> None:
    st.sidebar.html(f'<p class="qa-brand">{html.escape(title)}</p>'
                    f'<p class="qa-sub">{html.escape(subtitle)}</p>')


def clean(name: str) -> str:
    """Series names become Vega field names in tooltips, where a dot means a nested field."""
    return str(name).replace(".", "·").replace("[", "（").replace("]", "）")


def _style(chart, t: dict):
    return (
        chart.configure(background="transparent", font=FONT)
        .configure_view(stroke=None)
        .configure_axis(labelColor=t["muted"], titleColor=t["text2"], gridColor=t["grid"],
                        domainColor=t["axis"], tickColor=t["axis"], labelFontSize=12,
                        titleFontSize=12, titleFontWeight="normal", gridWidth=1)
        .configure_legend(labelColor=t["text"], labelFontSize=13, symbolStrokeWidth=3,
                          symbolSize=160, columnPadding=18)
        .configure_title(color=t["text2"], fontSize=13, fontWeight="normal", anchor="start",
                         offset=10)
    )


def _show(chart) -> None:
    st.altair_chart(_style(chart, tokens()), theme=None)


def _long(results: dict, labels: dict[str, str]) -> tuple[pd.DataFrame, list[str]]:
    names = [clean(labels[k]) for k in results]
    frames = []
    for k, r in results.items():
        nav = r.nav.resample("W").last().dropna()
        dd = (r.nav / r.nav.cummax() - 1).resample("W").min().reindex(nav.index)
        frames.append(pd.DataFrame({"date": nav.index, "series": clean(labels[k]),
                                    "nav": nav.to_numpy(), "dd": dd.to_numpy()}))
    return pd.concat(frames, ignore_index=True), names


def _crosshair(df: pd.DataFrame, base: alt.Chart, field: str, fmt: str, names: list[str], t: dict,
               name: str) -> list[alt.Chart]:
    """Hover layer: a vertical rule with one tooltip for all series, and a dot on each line."""
    hover = alt.selection_point(name=name, fields=["date"], nearest=True, on="pointerover",
                                clear="pointerout", empty=False)
    tips = [alt.Tooltip("date:T", title="日期", format="%Y-%m-%d")]
    tips += [alt.Tooltip(field=n, type="quantitative", format=fmt) for n in names]
    rule = (alt.Chart(df).transform_pivot("series", value=field, groupby=["date"])
            .mark_rule(color=t["muted"], strokeWidth=1)
            .encode(x="date:T", opacity=alt.condition(hover, alt.value(1), alt.value(0)), tooltip=tips)
            .add_params(hover))
    dots = base.mark_point(size=70, filled=True, stroke=t["surface"], strokeWidth=2).encode(
        y=alt.Y(f"{field}:Q"), opacity=alt.condition(hover, alt.value(1), alt.value(0)))
    return [dots, rule]


def performance_charts(results: dict, labels: dict[str, str]) -> None:
    """Growth of 1 (log scale) and, underneath, how far each line was below its high."""
    t = tokens()
    df, names = _long(results, labels)
    ends = {clean(labels[k]): f"{clean(labels[k])}　{r.nav.iloc[-1]:.1f} 倍" for k, r in results.items()}
    scale = alt.Scale(domain=names, range=t["series"][: len(names)])
    legend = alt.Legend(orient="top", title=None,
                        labelExpr=f"{json.dumps(ends, ensure_ascii=False)}[datum.label]")
    x = alt.X("date:T", title=None, axis=alt.Axis(grid=False, format="%Y"))

    base = alt.Chart(df).encode(x=x, color=alt.Color("series:N", scale=scale, legend=legend))
    lo, hi = df["nav"].min(), df["nav"].max()
    ticks = [v for v in LOG_TICKS if lo / 1.6 <= v <= hi * 1.6]
    if len(ticks) < 3:  # a narrow range needs closer steps
        ticks = [v for v in FINE_TICKS if lo / 1.15 <= v <= hi * 1.15]
    growth = base.mark_line(strokeWidth=2).encode(
        y=alt.Y("nav:Q", scale=alt.Scale(type="log", nice=False), title=None,
                axis=alt.Axis(values=ticks, labelExpr="format(datum.value, '~g') + ' 倍'")))
    _show(alt.layer(growth, *_crosshair(df, base, "nav", ".2f", names, t, "h1"))
          .properties(height=340, title="本金变成多少倍（对数坐标；图例里是最终倍数）"))

    base2 = alt.Chart(df).encode(x=x, color=alt.Color("series:N", scale=scale, legend=None))
    first = df[df["series"] == names[0]]
    area = (alt.Chart(first).mark_area(opacity=0.14, color=t["series"][0])
            .encode(x=x, y=alt.Y("dd:Q")))
    lines = base2.mark_line(strokeWidth=1.5).encode(
        y=alt.Y("dd:Q", title=None, axis=alt.Axis(format=".0%", tickCount=4)))
    _show(alt.layer(area, lines, *_crosshair(df, base2, "dd", ".1%", names, t, "h2"))
          .properties(height=170, title="回撤：比之前的最高点低多少（越接近 0 越好）"))


def yearly_chart(results: dict, labels: dict[str, str],
                 calendar_returns: Callable[[pd.Series], pd.Series]) -> None:
    """Calendar-year returns, one bar per series per year."""
    t = tokens()
    names = [clean(labels[k]) for k in results]
    rows = [{"year": str(year), "series": clean(labels[k]), "ret": float(v)}
            for k, r in results.items() for year, v in calendar_returns(r.nav).items()]
    df = pd.DataFrame(rows)
    scale = alt.Scale(domain=names, range=t["series"][: len(names)])
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("year:O", title=None, axis=alt.Axis(labelAngle=0, grid=False),
                scale=alt.Scale(paddingInner=0.22)),
        xOffset=alt.XOffset("series:N", sort=names, scale=alt.Scale(paddingInner=0.12)),
        y=alt.Y("ret:Q", title=None, axis=alt.Axis(format="+.0%", tickCount=6)),
        color=alt.Color("series:N", scale=scale, legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("year:O", title="年份"), alt.Tooltip("series:N", title="组合"),
                 alt.Tooltip("ret:Q", title="当年收益", format="+.1%")],
    )
    zero = alt.Chart(pd.DataFrame({"y": [0.0]})).mark_rule(color=t["axis"]).encode(y="y:Q")
    _show(alt.layer(bars, zero).properties(height=260, title="每一年的收益"))


def hbar(df: pd.DataFrame, category: str, value: str, title: str, fmt: str = "d",
         sign_labels: tuple[str, str] | None = None, height_per_row: int = 26) -> None:
    """Horizontal bars with the value written at the end of each bar.

    With sign_labels=(above, below), bars are coloured by sign and a legend names both.
    """
    t = tokens()
    d = df[[category, value]].copy()
    y = alt.Y(f"{category}:N", sort=list(d[category]), title=None,
              axis=alt.Axis(labelLimit=220, grid=False, ticks=False, domain=False))
    x = alt.X(f"{value}:Q", title=None, axis=None)  # every bar carries its value, so no axis
    tip = [alt.Tooltip(f"{category}:N", title=""), alt.Tooltip(f"{value}:Q", title=title, format=fmt)]
    if sign_labels:
        d["sign"] = [sign_labels[0] if v >= 0 else sign_labels[1] for v in d[value]]
        color = alt.Color("sign:N", scale=alt.Scale(domain=list(sign_labels), range=t["series"][:2]),
                          legend=alt.Legend(orient="top", title=None, symbolType="square"))
    else:
        color = alt.value(t["series"][0])
    bars = alt.Chart(d).mark_bar(cornerRadiusEnd=3, height={"band": 0.7}).encode(
        y=y, x=x, color=color, tooltip=tip)
    pos = alt.Chart(d[d[value] >= 0]).mark_text(align="left", dx=5, color=t["text"], fontSize=12).encode(
        y=y, x=x, text=alt.Text(f"{value}:Q", format=fmt))
    neg = alt.Chart(d[d[value] < 0]).mark_text(align="right", dx=-5, color=t["text"], fontSize=12).encode(
        y=y, x=x, text=alt.Text(f"{value}:Q", format=fmt))
    zero = alt.Chart(pd.DataFrame({"x": [0.0]})).mark_rule(color=t["axis"]).encode(
        x=alt.X("x:Q", axis=None))  # every layer must agree on "no axis" or Vega-Lite fails
    _show(alt.layer(bars, zero, pos, neg).properties(height=alt.Step(height_per_row), title=title))


def stepper(stage: int, steps: list[str], official: bool) -> None:
    """Six-step progress line. `stage` 0 means nothing yet; the current step is ringed
    (filled when backed by official data), earlier steps are ticked."""
    parts = []
    for i, label in enumerate(steps, start=1):
        cls = "done" if i < stage else ("now official" if i == stage and official
                                        else "now" if i == stage else "")
        mark = "✓" if i < stage or (i == stage and official) else str(i)
        parts.append(f'<div class="qa-step {cls}"><span class="qa-dot">{mark}</span>'
                     f'<span class="qa-lbl">{html.escape(label)}</span></div>')
    st.html(f'<div class="qa-steps">{"".join(parts)}</div>')


def sparkline_rows(prices: pd.DataFrame, symbols: list[str], days: int = 126) -> list[list[float]]:
    """Last `days` closes of each symbol, rebased to 100, for a table's trend column."""
    out = []
    for s in symbols:
        if s not in prices.columns:
            out.append([])
            continue
        tail = prices[s].dropna().iloc[-days:]
        out.append([] if tail.empty else (tail / tail.iloc[0] * 100).round(2).tolist())
    return out
