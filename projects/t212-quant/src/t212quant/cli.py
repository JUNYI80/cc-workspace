"""Command line: t212q {backtest,sweep,signal,resolve,account,rebalance}."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from . import backtest, data, rebalance, research
from .broker import Trading212Client
from .config import Config, load_config
from .rebalance import DEFAULT_JOURNAL, execute, resolve_instruments
from .strategy import compute_signal

PROJECT_DIR = Path(__file__).resolve().parents[2]
BLOCKER_MESSAGES = {
    rebalance.LIVE_NOT_ALLOWED:
        "Refusing to trade real money: set T212_ALLOW_LIVE=yes to allow live orders.",
    rebalance.MARKET_CLOSED:
        "LSE is closed (live orders only Mon-Fri 08:15-16:15 London time); "
        "market orders would queue and fill at the open.",
    rebalance.ALREADY_TRADED:
        "Already traded this month (see state/journal.jsonl). Use --force to trade again.",
}

SYNTHETIC_WARNING = (
    "*** SYNTHETIC DATA: random prices for testing the code. "
    "These numbers say nothing about real markets. ***"
)


def _prices(cfg: Config, args, symbols: list[str], refresh: bool = False) -> pd.DataFrame:
    if getattr(args, "synthetic", False):
        print(SYNTHETIC_WARNING + "\n")
        return data.synthetic_prices(symbols, start=cfg.start, cash_symbol=cfg.cash_symbol)
    return data.load_prices(symbols, cfg.start, refresh=refresh or args.refresh)


def cmd_backtest(cfg: Config, args) -> int:
    prices = _prices(cfg, args, cfg.all_symbols)
    results = backtest.run(cfg, prices)
    print(backtest.report(cfg, prices, results))
    if args.plot:
        from .plot import plot_results

        print(f"\nChart saved to {plot_results(results, Path(args.plot))}")
    return 0


def cmd_research(_cfg: Config, args) -> int:
    cfg = research.build_config(args.symbols)
    study = research.run(cfg, _prices(cfg, args, cfg.all_symbols))
    print(research.format_study(study))
    from .plot import plot_results

    stocks = " + ".join(cfg.risk_symbols)
    path = Path(args.plot or PROJECT_DIR / "reports" / f"research_{'_'.join(cfg.risk_symbols)}.png")
    try:
        plot_results(study.results, path, title=f"Trend rule on {stocks}")
    except ImportError:
        print('\n(Install matplotlib for a chart: pip install -e ".[plot]")')
        return 0
    print(f"\n图表：{path}")
    return 0


def cmd_sweep(cfg: Config, args) -> int:
    prices = _prices(cfg, args, cfg.all_symbols)
    df = backtest.sweep(cfg, prices)
    pd.set_option("display.width", 140)
    print(df.sort_values("Sharpe", ascending=False).to_string(index=False, float_format="%.3f"))
    print("\nAcross all parameter sets:")
    print(df[["CAGR", "Volatility", "Sharpe", "Max drawdown"]].describe().loc[
        ["min", "25%", "50%", "75%", "max"]].to_string(float_format="%.3f"))
    print("\nJudge the strategy by the median and the worst rows, not the best one.")
    return 0


def _latest_signal(cfg: Config, args):
    prices = data.completed_closes(_prices(cfg, args, cfg.strategy_symbols, refresh=True))
    sig = compute_signal(prices, cfg.risk_symbols, cfg.cash_symbol, cfg.strategy)
    return prices, sig


def cmd_signal(cfg: Config, args) -> int:
    prices, sig = _latest_signal(cfg, args)
    print(f"Signal as of close {sig.as_of.date()}")
    if sig.table.empty:
        print("Not enough history yet; target is 100% cash asset.")
    else:
        t = sig.table.copy()
        t["name"] = [cfg.asset(s).name for s in t.index]
        print(t[["name", "score", "vs_sma200", "vol", "eligible", "weight"]].to_string(
            float_format=lambda v: f"{v:.3f}"))
    print(f"\nExpected portfolio volatility: {sig.expected_vol:.1%}")
    print("Target weights:")
    for s, w in sig.weights[sig.weights > 0].sort_values(ascending=False).items():
        print(f"  {s:<8} {w:6.1%}  {cfg.asset(s).name}")
    return 0


def cmd_resolve(cfg: Config, args) -> int:
    client = Trading212Client.from_env(args.env)
    resolved = resolve_instruments(cfg.assets, client.instruments())
    for a in cfg.assets:
        i = resolved[a.symbol]
        print(f"{a.symbol:<8} -> {i['ticker']:<12} {i.get('currencyCode', ''):<4} {i.get('name', '')}")
    print("\nCheck each name above matches the ETF you intend to trade.")
    return 0


def cmd_account(cfg: Config, args) -> int:
    client = Trading212Client.from_env(args.env)
    info, cash = client.account_info(), client.cash()
    print(f"Account {info.get('id')} ({info.get('currencyCode')}) on {args.env}")
    print(json.dumps(cash, indent=2))
    for p in client.portfolio():
        print(f"{p['ticker']:<14} qty {p['quantity']:>12.4f}  price {p['currentPrice']:>10.2f}  "
              f"P/L {p.get('ppl', 0):>10.2f}")
    return 0


def cmd_rebalance(cfg: Config, args) -> int:
    if args.execute:
        blockers = rebalance.execution_blockers(args.env, DEFAULT_JOURNAL, force=args.force)
        if blockers:
            print(BLOCKER_MESSAGES[blockers[0]])
            return 2

    prices, sig = _latest_signal(cfg, args)
    client = Trading212Client.from_env(args.env)
    try:
        plan = rebalance.plan_from_account(cfg, client, prices, sig.weights)
    except ValueError as exc:
        print(f"Cannot plan: {exc}")
        return 1
    print(f"[{args.env.upper()}] signal as of close {sig.as_of.date()}\n")
    print(plan.describe(cfg.account_currency))

    if not args.execute:
        print("\nDry run only. Add --execute to place these orders.")
        return 0
    if plan.problems:
        print("\nNot executing because of the problems above.")
        return 1
    if not plan.orders:
        return 0
    if not args.yes:
        answer = input(f"\nPlace {len(plan.orders)} order(s) on {args.env.upper()}? Type 'yes': ")
        if answer.strip().lower() != "yes":
            print("Cancelled.")
            return 1
    rows = execute(plan, client, cfg.execution, journal=DEFAULT_JOURNAL)
    failed = [r for r in rows if "error" in r or "abort" in r]
    print(f"\nDone: {len(rows) - len(failed)} ok, {len(failed)} failed/aborted. Journal: {DEFAULT_JOURNAL}")
    for r in failed:
        print("  ", r.get("error") or r.get("abort"))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="t212q", description="Momentum ETF rotation for Trading 212")
    p.add_argument("--config", help="path to strategy.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def data_opts(sp):
        sp.add_argument("--synthetic", action="store_true", help="use random prices (offline test)")
        sp.add_argument("--refresh", action="store_true", help="re-download price data")

    sp = sub.add_parser("backtest", help="backtest strategy vs benchmarks")
    data_opts(sp)
    sp.add_argument("--plot", nargs="?", const=str(PROJECT_DIR / "reports" / "backtest.png"),
                    help="save an equity/drawdown chart (PNG)")
    sp = sub.add_parser("research", help="study the trend rule on stocks, e.g. research TSLA NVDA")
    sp.add_argument("symbols", nargs="+", help="Yahoo Finance tickers, e.g. TSLA NVDA AAPL")
    sp.add_argument("--plot", help="where to save the chart (PNG)")
    data_opts(sp)
    data_opts(sub.add_parser("sweep", help="robustness check over a parameter grid"))
    data_opts(sub.add_parser("signal", help="today's target weights"))

    for name, helptext in [("resolve", "map assets to Trading 212 tickers"),
                           ("account", "show cash and positions")]:
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--env", choices=["demo", "live"], default="demo")
    sp = sub.add_parser("rebalance", help="plan (and optionally place) this month's orders")
    sp.add_argument("--env", choices=["demo", "live"], default="demo")
    sp.add_argument("--execute", action="store_true", help="actually place orders")
    sp.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    sp.add_argument("--force", action="store_true", help="trade even if already traded this month")
    sp.set_defaults(synthetic=False, refresh=True)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    handler = {
        "backtest": cmd_backtest, "research": cmd_research, "sweep": cmd_sweep,
        "signal": cmd_signal,
        "resolve": cmd_resolve, "account": cmd_account, "rebalance": cmd_rebalance,
    }[args.cmd]
    return handler(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
