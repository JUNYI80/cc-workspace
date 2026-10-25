import re
from datetime import datetime

import pandas as pd
import pytest
from pandas.tseries.offsets import BDay

from t212quant import cli
from t212quant.config import load_config
from t212quant.data import synthetic_prices


class FakeBroker:
    env = "demo"

    def __init__(self, cfg, holdings=None, free=5000.0):
        self.instruments_ = [
            {"ticker": a.symbol.replace(".L", "l_EQ"), "isin": a.isin, "currencyCode": "GBX",
             "name": a.name} for a in cfg.assets
        ]
        self.positions = holdings or []
        self.free = free
        self.orders = []

    def instruments(self):
        return self.instruments_

    def portfolio(self):
        return self.positions

    def cash(self):
        return {"free": self.free}

    def pending_orders(self):
        return []

    def place_market_order(self, ticker, quantity):
        self.orders.append((ticker, quantity))
        return {"id": len(self.orders), "status": "NEW"}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    cfg = load_config()
    end = pd.Timestamp.today().normalize() - BDay(1)
    n = 700
    prices = synthetic_prices(cfg.strategy_symbols, start=str((end - BDay(n - 1)).date()),
                              years=n / 252, cash_symbol=cfg.cash_symbol) * 50  # pence
    monkeypatch.setattr(cli, "_prices", lambda c, a, symbols, refresh=False: prices[symbols])
    monkeypatch.setattr(cli, "DEFAULT_JOURNAL", tmp_path / "journal.jsonl")
    broker = FakeBroker(cfg)
    monkeypatch.setattr(cli.Trading212Client, "from_env", classmethod(lambda cls, env: broker))
    monkeypatch.delenv("T212_ALLOW_LIVE", raising=False)
    return broker


def test_dry_run_places_nothing(setup, capsys):
    assert cli.main(["rebalance"]) == 0
    out = capsys.readouterr().out
    assert "Dry run only" in out
    assert setup.orders == []


def test_execute_on_demo_then_blocks_second_run(setup, capsys):
    assert cli.main(["rebalance", "--execute", "--yes"]) == 0
    assert setup.orders, "expected orders from an all-cash account"
    assert all(q > 0 for _, q in setup.orders)
    n = len(setup.orders)
    assert cli.main(["rebalance", "--execute", "--yes"]) == 2  # already traded this month
    assert len(setup.orders) == n


def test_live_requires_explicit_opt_in(setup, capsys):
    assert cli.main(["rebalance", "--env", "live", "--execute", "--yes"]) == 2
    assert "T212_ALLOW_LIVE" in capsys.readouterr().out
    assert setup.orders == []


def test_live_refused_outside_market_hours(setup, monkeypatch, capsys):
    monkeypatch.setenv("T212_ALLOW_LIVE", "yes")
    monkeypatch.setattr(cli, "_in_trading_window", lambda: False)
    assert cli.main(["rebalance", "--env", "live", "--execute", "--yes"]) == 2
    assert setup.orders == []


def test_trading_window():
    assert cli._in_trading_window(datetime(2026, 10, 26, 10, 0, tzinfo=cli.LONDON))  # Monday
    assert not cli._in_trading_window(datetime(2026, 10, 25, 10, 0, tzinfo=cli.LONDON))  # Sunday
    assert not cli._in_trading_window(datetime(2026, 10, 26, 7, 0, tzinfo=cli.LONDON))


def test_price_mismatch_blocks_execution(setup, capsys):
    setup.positions = [{"ticker": "VUSAl_EQ", "quantity": 5.0, "currentPrice": 1.0}]
    assert cli.main(["rebalance", "--execute", "--yes"]) == 1
    assert "disagree" in capsys.readouterr().out
    assert setup.orders == []


def test_pie_shares_are_not_managed(setup, capsys):
    data_price = float(cli._prices(None, None, ["VUSA.L"])["VUSA.L"].iloc[-1])
    setup.positions = [{"ticker": "VUSAl_EQ", "quantity": 30.0, "pieQuantity": 30.0,
                        "currentPrice": data_price}]
    assert cli.main(["rebalance"]) == 0
    assert not re.search(r"SELL\s+\S+\s+VUSAl_EQ", capsys.readouterr().out)


def test_missing_price_stops_cleanly(setup, monkeypatch, capsys):
    prices = cli._prices(None, None, list(load_config().strategy_symbols)).copy()
    prices.iloc[-10:, 0] = float("nan")
    monkeypatch.setattr(cli, "_prices", lambda c, a, symbols, refresh=False: prices[symbols])
    assert cli.main(["rebalance", "--execute", "--yes"]) == 1
    assert "no recent price" in capsys.readouterr().out
    assert setup.orders == []
