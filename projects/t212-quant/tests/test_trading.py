import base64
import json

import pandas as pd
import pytest

from t212quant.broker import T212Error, Trading212Client
from t212quant.config import Asset, ExecutionParams
from t212quant.rebalance import (
    build_plan,
    execute,
    prices_agree,
    resolve_instruments,
    to_account_currency,
    wait_for_fills,
)


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)
        self.content = b"" if payload is None else self.text.encode()

    def json(self):
        return self._payload


class FakeSession:
    """Records requests and replays queued responses per (method, path)."""

    def __init__(self, routes):
        self.headers = {}
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []

    def request(self, method, url, timeout=None, json=None, **kw):
        path = url.split("/api/v0", 1)[1]
        self.calls.append((method, path, json))
        queue = self.routes[(method, path)]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def make_client(routes, env="demo"):
    return Trading212Client("KEY", "SECRET", env=env, session=FakeSession(routes),
                            sleep=lambda s: None)


# -- broker -------------------------------------------------------------------

def test_basic_auth_header_and_base_url():
    c = make_client({("GET", "/equity/account/cash"): [FakeResponse(payload={"free": 5})]})
    expected = "Basic " + base64.b64encode(b"KEY:SECRET").decode()
    assert c.session.headers["Authorization"] == expected
    assert c.base_url == "https://demo.trading212.com/api/v0"
    assert c.cash() == {"free": 5}


def test_rejects_unknown_environment():
    with pytest.raises(ValueError):
        Trading212Client("KEY", "SECRET", env="prod")


def test_sell_is_negative_quantity():
    c = make_client({("POST", "/equity/orders/market"): [FakeResponse(payload={"id": 1})]})
    c.place_market_order("VUSAl_EQ", -1.5)
    assert c.session.calls[-1] == ("POST", "/equity/orders/market",
                                   {"ticker": "VUSAl_EQ", "quantity": -1.5})


def test_retries_on_429_then_succeeds():
    c = make_client({("GET", "/equity/portfolio"): [
        FakeResponse(429, {"error": "slow down"}, {"x-ratelimit-reset": "0"}),
        FakeResponse(payload=[]),
    ]})
    assert c.portfolio() == []
    assert len(c.session.calls) == 2


def test_http_error_raises():
    c = make_client({("GET", "/equity/portfolio"): [FakeResponse(401, {"error": "bad key"})]})
    with pytest.raises(T212Error) as e:
        c.portfolio()
    assert e.value.status == 401


# -- instrument resolution --------------------------------------------------------

INSTRUMENTS = [
    {"ticker": "VUSAl_EQ", "isin": "IE00B3XXRP09", "currencyCode": "GBX", "name": "S&P 500 (GBP)"},
    {"ticker": "VUSAd_EQ", "isin": "IE00B3XXRP09", "currencyCode": "EUR", "name": "S&P 500 (EUR)"},
    {"ticker": "ERNSl_EQ", "isin": "IE00BCRY6557", "currencyCode": "GBX", "name": "Ultrashort"},
]
ASSETS = (Asset("VUSA.L", "S&P", "IE00B3XXRP09"),
          Asset("ERNS.L", "cash", "IE00BCRY6557", role="cash"))


def test_resolve_by_isin_and_currency():
    r = resolve_instruments(ASSETS, INSTRUMENTS)
    assert r["VUSA.L"]["ticker"] == "VUSAl_EQ"
    assert r["ERNS.L"]["ticker"] == "ERNSl_EQ"


def test_resolve_fails_loudly_when_missing():
    with pytest.raises(ValueError, match="Candidates"):
        resolve_instruments((Asset("X.L", "x", "IE_NOPE"),), INSTRUMENTS)


def test_resolve_explicit_ticker_currency_mismatch():
    with pytest.raises(ValueError):
        resolve_instruments((Asset("VUSA.L", "S&P", "IE00B3XXRP09", t212_ticker="VUSAd_EQ"),),
                            INSTRUMENTS)


def test_currency_helpers():
    assert to_account_currency(9000, "GBX", "GBP") == 90
    with pytest.raises(ValueError):
        to_account_currency(10, "USD", "GBP")
    assert prices_agree(9000, 90.5) and prices_agree(9000, 9010)
    assert not prices_agree(9000, 70)


# -- planning ---------------------------------------------------------------------

TICKERS = {"A": "Al_EQ", "B": "Bl_EQ", "CASH": "Cl_EQ"}
EX = ExecutionParams(rebalance_band=0.03, cash_buffer=0.02, min_order_value=10,
                     max_order_value=10_000, quantity_decimals=2)


def test_plan_from_all_cash():
    target = pd.Series({"A": 0.5, "B": 0.3, "CASH": 0.2})
    plan = build_plan(target, {}, {"A": 10.0, "B": 20.0, "CASH": 100.0}, 1000.0, TICKERS, EX)
    qty = {o.symbol: o.quantity for o in plan.orders}
    assert qty == {"A": 49.0, "B": 14.7, "CASH": 1.96}
    assert sum(o.value for o in plan.buys) <= 1000 * (1 - EX.cash_buffer) + 1e-9
    assert not plan.problems


def test_plan_full_exit_and_ignores_foreign_holdings():
    target = pd.Series({"A": 0.0, "B": 0.98 / 0.98, "CASH": 0.0})
    holdings = {"A": 3.333, "B": 1.0}
    plan = build_plan(target, holdings, {"A": 100.0, "B": 100.0, "CASH": 100.0}, 0.0, TICKERS, EX)
    sell = plan.sells[0]
    assert sell.symbol == "A" and sell.quantity == -3.333  # exact held quantity
    assert plan.sleeve_value == pytest.approx(433.3)


def test_plan_skips_tiny_orders():
    target = pd.Series({"A": 0.5, "B": 0.5, "CASH": 0.0})
    plan = build_plan(target, {"A": 0.49, "B": 0.49}, {"A": 100.0, "B": 100.0, "CASH": 1.0},
                      2.0, TICKERS, EX)
    assert plan.orders == []


def test_plan_blocks_oversized_order():
    ex = ExecutionParams(max_order_value=100)
    plan = build_plan(pd.Series({"A": 1.0, "B": 0.0, "CASH": 0.0}), {},
                      {"A": 10.0, "B": 10.0, "CASH": 10.0}, 1000.0, TICKERS, ex)
    assert plan.problems


def test_capital_cap_limits_sleeve():
    ex = ExecutionParams(capital_cap=500, cash_buffer=0.0)
    plan = build_plan(pd.Series({"A": 1.0, "B": 0.0, "CASH": 0.0}), {},
                      {"A": 10.0, "B": 10.0, "CASH": 10.0}, 10_000.0, TICKERS, ex)
    assert plan.sleeve_value == 500
    assert plan.buys[0].quantity == 50


# -- execution ----------------------------------------------------------------------

def test_execute_sells_first_then_scales_buys_to_cash(tmp_path):
    target = pd.Series({"A": 0.0, "B": 0.98, "CASH": 0.0})
    plan = build_plan(target, {"A": 10.0}, {"A": 100.0, "B": 50.0, "CASH": 1.0}, 0.0, TICKERS, EX)
    client = make_client({
        ("POST", "/equity/orders/market"): [FakeResponse(payload={"id": 1, "status": "NEW"}),
                                            FakeResponse(payload={"id": 2, "status": "NEW"})],
        ("GET", "/equity/orders"): [FakeResponse(payload=[{"id": 1}]), FakeResponse(payload=[])],
        ("GET", "/equity/account/cash"): [FakeResponse(payload={"free": 900.0})],  # filled lower
    })
    rows = execute(plan, client, EX, journal=tmp_path / "j.jsonl", sleep=lambda s: None)
    posts = [c for c in client.session.calls if c[0] == "POST"]
    assert posts[0][2] == {"ticker": "Al_EQ", "quantity": -10.0}
    buy_qty = posts[1][2]["quantity"]
    assert buy_qty * 50 <= 900 - EX.cash_buffer * plan.sleeve_value + 1e-9
    assert all("response" in r for r in rows)
    assert (tmp_path / "j.jsonl").read_text().count("\n") == 1


def test_execute_skips_buys_if_sells_do_not_fill(tmp_path):
    plan = build_plan(pd.Series({"A": 0.0, "B": 0.98, "CASH": 0.0}), {"A": 10.0},
                      {"A": 100.0, "B": 50.0, "CASH": 1.0}, 0.0, TICKERS,
                      ExecutionParams(fill_timeout_s=0, max_order_value=10_000))
    client = make_client({
        ("POST", "/equity/orders/market"): [FakeResponse(payload={"id": 1})],
        ("GET", "/equity/orders"): [FakeResponse(payload=[{"id": 1}])],
    })
    rows = execute(plan, client, ExecutionParams(fill_timeout_s=0), journal=tmp_path / "j.jsonl",
                   sleep=lambda s: None)
    assert sum(1 for c in client.session.calls if c[0] == "POST") == 1
    assert "abort" in rows[-1]


def test_execute_refuses_blocked_plan(tmp_path):
    plan = build_plan(pd.Series({"A": 1.0, "B": 0.0, "CASH": 0.0}), {},
                      {"A": 10.0, "B": 10.0, "CASH": 10.0}, 1000.0, TICKERS,
                      ExecutionParams(max_order_value=1))
    with pytest.raises(RuntimeError):
        execute(plan, make_client({}), EX, journal=tmp_path / "j.jsonl")


def test_wait_for_fills_returns_unfilled():
    client = make_client({("GET", "/equity/orders"): [FakeResponse(payload=[{"id": 7}])]})
    assert wait_for_fills(client, [7, 8], timeout_s=0, sleep=lambda s: None) == [7]
