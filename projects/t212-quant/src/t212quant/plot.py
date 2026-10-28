"""Equity-curve and drawdown chart (PNG) for a backtest."""
from __future__ import annotations

from pathlib import Path

from . import metrics
from .backtest import BacktestResult

# Categorical slots 1-3 of the reference data-viz palette (light mode).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"


def plot_results(results: dict[str, BacktestResult], path: Path,
                 title: str = "Strategy vs benchmarks") -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    names = list(results)[: len(SERIES)]
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]},
        facecolor=SURFACE,
    )
    for ax in (ax1, ax2):
        ax.set_facecolor(SURFACE)
        ax.grid(True, color=GRID, linewidth=0.8)
        ax.tick_params(which="both", colors=TEXT_2, labelsize=9)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)

    for name, color in zip(names, SERIES):
        nav = results[name].nav
        ax1.plot(nav.index, nav, color=color, linewidth=2, label=name)
        ax1.annotate(
            f"{nav.iloc[-1]:.2f}x", (nav.index[-1], nav.iloc[-1]), xytext=(4, 0),
            textcoords="offset points", va="center", fontsize=9, color=TEXT,
        )
        ax2.plot(nav.index, metrics.drawdown(nav) * 100, color=color, linewidth=1.5, label=name)

    ax1.set_yscale("log")
    fmt = FuncFormatter(lambda v, _: f"{v:g}x")
    ax1.yaxis.set_major_formatter(fmt)
    ax1.yaxis.set_minor_formatter(fmt)
    ax1.set_ylabel("Growth of 1 (log scale)", color=TEXT_2, fontsize=10)
    ax1.set_title(title, loc="left", color=TEXT, fontsize=13)
    ax1.legend(frameon=False, fontsize=9, labelcolor=TEXT, loc="upper left")
    ax2.set_ylabel("Drawdown (%)", color=TEXT_2, fontsize=10)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return path
