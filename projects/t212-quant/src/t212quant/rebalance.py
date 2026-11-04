"""Turn target weights into Trading 212 market orders, with safety checks."""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .broker import Trading212Client
from .config import Asset, Config, ExecutionParams
from .strategy import apply_band

log = logging.getLogger(__name__)

DEFAULT_JOURNAL = Path(__file__).resolve().parents[2] / "state" / "journal.jsonl"
MAX_DATA_AGE_DAYS = 5
LONDON = ZoneInfo("Europe/London")
TRADING_WINDOW = (dtime(8, 15), dtime(16, 15))  # inside LSE hours, away from the auctions

# Reasons execution_blockers() can return.
LIVE_NOT_ALLOWED = "live_not_allowed"
MARKET_CLOSED = "market_closed"
ALREADY_TRADED = "already_traded"


def in_trading_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(LONDON)
    return now.weekday() < 5 and TRADING_WINDOW[0] <= now.time() <= TRADING_WINDOW[1]


def already_traded_this_month(env: str, journal: Path) -> bool:
    if not journal.exists():
        return False
    month = date.today().strftime("%Y-%m")
    for line in journal.read_text().splitlines():
        entry = json.loads(line)
        placed = any("response" in r for r in entry["results"])
        if entry["env"] == env and entry["time"][:7] == month and placed:
            return True
    return False


def execution_blockers(env: str, journal: Path, force: bool = False) -> list[str]:
    """Why orders must not be placed right now; an empty list means go ahead."""
    out = []
    if env == "live" and os.getenv("T212_ALLOW_LIVE") != "yes":
        out.append(LIVE_NOT_ALLOWED)
    if env == "live" and not in_trading_window():
        out.append(MARKET_CLOSED)
    if not force and already_traded_this_month(env, journal):
        out.append(ALREADY_TRADED)
    return out


def resolve_instruments(assets: tuple[Asset, ...], instruments: list[dict]) -> dict[str, dict]:
    """Map each config asset to a Trading 212 instrument by explicit ticker or ISIN."""
    by_ticker = {i["ticker"]: i for i in instruments}
    out = {}
    for a in assets:
        if a.t212_ticker:
            inst = by_ticker.get(a.t212_ticker)
            if inst is None:
                raise ValueError(f"{a.symbol}: Trading 212 ticker {a.t212_ticker!r} not found")
        else:
            cands = [i for i in instruments if i.get("isin") == a.isin]
            same_ccy = [i for i in cands if i.get("currencyCode") == a.quote_currency]
            if len(same_ccy) != 1:
                listed = ", ".join(f"{i['ticker']} ({i.get('currencyCode')})" for i in cands) or "none"
                raise ValueError(
                    f"{a.symbol} ({a.isin}): expected one {a.quote_currency} listing on "
                    f"Trading 212, found {len(same_ccy)}. Candidates: {listed}. "
                    f"Set t212_ticker for this asset in the config."
                )
            inst = same_ccy[0]
        if inst.get("currencyCode") != a.quote_currency:
            raise ValueError(
                f"{a.symbol}: Trading 212 lists {inst['ticker']} in {inst.get('currencyCode')}, "
                f"config says {a.quote_currency}"
            )
        out[a.symbol] = inst
    return out


def to_account_currency(price: float, quote_ccy: str, account_ccy: str) -> float:
    if quote_ccy == account_ccy:
        return price
    if quote_ccy == "GBX" and account_ccy == "GBP":
        return price / 100
    raise ValueError(f"No conversion from {quote_ccy} to {account_ccy}; use {account_ccy}-priced assets")


def prices_agree(data_price: float, broker_price: float, tol: float = 0.10) -> bool:
    """True if two quotes of the same instrument match, allowing a GBX/GBP 100x unit gap."""
    if broker_price <= 0 or data_price <= 0:
        return False
    return any(abs(data_price / (broker_price * k) - 1) < tol for k in (1, 100, 0.01))


def _truncate(x: float, decimals: int) -> float:
    f = 10**decimals
    return math.trunc(x * f) / f


@dataclass
class PlannedOrder:
    symbol: str
    ticker: str
    quantity: float  # positive buys, negative sells
    price: float  # estimated, account currency

    @property
    def value(self) -> float:
        return self.quantity * self.price


@dataclass
class Plan:
    sleeve_value: float
    free_cash: float
    current: pd.Series
    target: pd.Series
    orders: list[PlannedOrder]
    problems: list[str] = field(default_factory=list)  # any problem blocks execution

    @property
    def sells(self) -> list[PlannedOrder]:
        return [o for o in self.orders if o.quantity < 0]

    @property
    def buys(self) -> list[PlannedOrder]:
        return [o for o in self.orders if o.quantity > 0]

    def describe(self, currency: str = "GBP") -> str:
        lines = [
            f"Sleeve value: {self.sleeve_value:,.2f} {currency} (free cash {self.free_cash:,.2f})",
            "",
            f"{'Asset':<10}{'Current':>9}{'Target':>9}",
        ]
        for s in self.target.index:
            if self.current.get(s, 0) > 0 or self.target[s] > 0:
                lines.append(f"{s:<10}{self.current.get(s, 0):>9.1%}{self.target[s]:>9.1%}")
        lines.append(f"{'(cash)':<10}{1 - self.current.sum():>9.1%}{1 - self.target.sum():>9.1%}")
        lines.append("")
        if not self.orders:
            lines.append("No orders needed.")
        for o in self.sells + self.buys:
            side = "SELL" if o.quantity < 0 else "BUY "
            lines.append(
                f"{side} {abs(o.quantity):>10.4f} {o.ticker:<12} ~{abs(o.value):>10,.2f} {currency}"
            )
        for p in self.problems:
            lines.append(f"BLOCKED: {p}")
        return "\n".join(lines)


def build_plan(
    target_weights: pd.Series,
    holdings: dict[str, float],
    prices: dict[str, float],
    free_cash: float,
    tickers: dict[str, str],
    ex: ExecutionParams,
) -> Plan:
    """Orders that move the bot's sleeve from `holdings` to `target_weights`.

    holdings: symbol -> quantity held (only the strategy's own assets).
    prices: symbol -> latest price in account currency.
    The sleeve is free cash plus the value of those holdings, capped at
    `ex.capital_cap`. Positions in other instruments are never touched.
    """
    symbols = list(target_weights.index)
    held_value = pd.Series({s: holdings.get(s, 0.0) * prices[s] for s in symbols})
    sleeve = free_cash + held_value.sum()
    if ex.capital_cap is not None:
        sleeve = min(sleeve, ex.capital_cap)
    if sleeve <= 0:
        raise ValueError("Nothing to manage: no free cash and no strategy holdings")
    current = held_value / sleeve
    target = apply_band(current, target_weights * (1 - ex.cash_buffer), ex.rebalance_band)

    orders, problems = [], []
    for s in symbols:
        held = holdings.get(s, 0.0)
        if target[s] == 0 and held > 0:
            qty = -held  # full exit: sell exactly what is held
        else:
            qty = _truncate((target[s] - current[s]) * sleeve / prices[s], ex.quantity_decimals)
        order = PlannedOrder(s, tickers[s], qty, prices[s])
        if qty == 0 or abs(order.value) < ex.min_order_value:
            continue
        if abs(order.value) > ex.max_order_value:
            problems.append(
                f"{s} order of {abs(order.value):,.2f} exceeds max_order_value "
                f"{ex.max_order_value:,.2f} (raise it in the config if intended)"
            )
        orders.append(order)
    return Plan(sleeve, free_cash, current, target, orders, problems)


def plan_from_account(cfg: Config, client: Trading212Client, prices: pd.DataFrame,
                      weights: pd.Series) -> Plan:
    """Read the live account and plan the orders, flagging stale or inconsistent data."""
    problems = []
    age = (pd.Timestamp(date.today()) - prices.index[-1]).days
    if age > MAX_DATA_AGE_DAYS:
        problems.append(f"price data is {age} days old")
    last = prices.iloc[-1]
    latest = last[cfg.strategy_symbols]
    if latest.isna().any():
        raise ValueError(f"no recent price for {list(latest[latest.isna()].index)}")

    resolved = resolve_instruments(cfg.assets, client.instruments())
    tickers = {s: i["ticker"] for s, i in resolved.items()}
    by_ticker = {p["ticker"]: p for p in client.portfolio()}
    free_cash = float(client.cash()["free"])

    px, holdings = {}, {}
    for a in cfg.assets:
        px[a.symbol] = to_account_currency(float(last[a.symbol]), a.quote_currency, cfg.account_currency)
        pos = by_ticker.get(tickers[a.symbol])
        if pos:
            # Shares inside a Pie belong to the Pie, not to this bot.
            holdings[a.symbol] = float(pos["quantity"]) - float(pos.get("pieQuantity") or 0)
            if not prices_agree(float(last[a.symbol]), float(pos["currentPrice"])):
                problems.append(f"{a.symbol}: data price {last[a.symbol]:.2f} vs Trading 212 "
                                f"{pos['currentPrice']:.2f} disagree")

    plan = build_plan(weights, holdings, px, free_cash, tickers, cfg.execution)
    plan.problems += problems
    return plan


def wait_for_fills(client: Trading212Client, order_ids: list[int], timeout_s: float,
                   poll_s: float = 6.0, sleep=time.sleep) -> list[int]:
    """Poll pending orders until the given ids are gone. Returns ids still pending."""
    deadline = time.monotonic() + timeout_s
    waiting = set(order_ids)
    while True:
        pending = {o["id"] for o in client.pending_orders()}
        waiting &= pending
        if not waiting or time.monotonic() >= deadline:
            return sorted(waiting)
        sleep(poll_s)


def execute(plan: Plan, client: Trading212Client, ex: ExecutionParams,
            journal: Path = DEFAULT_JOURNAL, sleep=time.sleep) -> list[dict]:
    """Place sells, wait for them to fill, then place buys sized to the cash actually available."""
    if plan.problems:
        raise RuntimeError("Plan is blocked: " + "; ".join(plan.problems))
    log_rows: list[dict] = []

    def place(o: PlannedOrder) -> dict | None:
        try:
            resp = client.place_market_order(o.ticker, o.quantity)
            log_rows.append({"order": asdict(o), "response": resp})
            log.info("Placed %s %s: %s", o.ticker, o.quantity, resp.get("status"))
            return resp
        except Exception as exc:  # keep going; every outcome is journaled
            log_rows.append({"order": asdict(o), "error": str(exc)})
            log.error("Order %s %s failed: %s", o.ticker, o.quantity, exc)
            return None

    try:
        sell_ids = [r["id"] for o in plan.sells if (r := place(o)) and "id" in r]
        if sell_ids:
            still = wait_for_fills(client, sell_ids, ex.fill_timeout_s, sleep=sleep)
            if still:
                log_rows.append({"abort": f"sells still pending after {ex.fill_timeout_s}s: {still}"})
                log.error("Sells %s not filled yet (market closed?). Buys skipped; rerun later.", still)
                return log_rows

        buys = plan.buys
        if buys:
            free = float(client.cash()["free"])
            budget = free - ex.cash_buffer * plan.sleeve_value
            need = sum(o.value for o in buys)
            scale = max(0.0, min(1.0, budget / need)) if need > 0 else 1.0
            if scale < 1:
                log.warning("Free cash %.2f covers %.0f%% of planned buys; scaling down", free, scale * 100)
            for o in buys:
                qty = _truncate(o.quantity * scale, ex.quantity_decimals)
                if qty * o.price >= ex.min_order_value:
                    place(PlannedOrder(o.symbol, o.ticker, qty, o.price))
        return log_rows
    finally:
        _journal(journal, client.env, plan, log_rows)


def _journal(path: Path, env: str, plan: Plan, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "env": env,
        "sleeve_value": plan.sleeve_value,
        "target": plan.target.round(4).to_dict(),
        "results": rows,
    }
    with path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
