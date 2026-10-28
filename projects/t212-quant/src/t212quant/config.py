"""Load and validate the YAML strategy config."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "strategy.yaml"


@dataclass(frozen=True)
class Asset:
    symbol: str  # price-data symbol (Yahoo), e.g. "VUSA.L"
    name: str
    isin: str
    quote_currency: str = "GBX"  # currency the data source quotes in (GBX = pence)
    role: str = "risk"  # "risk" or "cash"
    t212_ticker: str | None = None  # optional explicit Trading 212 ticker


@dataclass(frozen=True)
class StrategyParams:
    lookbacks: tuple[int, ...] = (63, 126, 252)  # trading days (~3, 6, 12 months)
    trend_window: int = 200
    top_n: int = 3
    vol_window: int = 63
    target_vol: float | None = 0.12  # annualised; None disables vol targeting
    max_weight: float = 0.5
    weighting: str = "inverse_vol"  # "inverse_vol" or "equal" (each slot 1/top_n)

    @property
    def warmup(self) -> int:
        return max(max(self.lookbacks), self.trend_window, self.vol_window) + 1


@dataclass(frozen=True)
class ExecutionParams:
    rebalance_band: float = 0.03  # skip trades whose weight change is smaller
    cost_bps: float = 10.0  # assumed spread + slippage per trade, in basis points
    cash_buffer: float = 0.02  # fraction of the sleeve kept as uninvested cash
    min_order_value: float = 10.0  # account currency
    max_order_value: float = 2500.0  # safety rail: refuse plans with bigger orders
    quantity_decimals: int = 2
    capital_cap: float | None = None  # max capital the bot manages; None = all free cash
    fill_timeout_s: int = 180


@dataclass(frozen=True)
class Benchmark:
    name: str
    weights: dict[str, float]


@dataclass(frozen=True)
class Config:
    assets: tuple[Asset, ...]
    strategy: StrategyParams = field(default_factory=StrategyParams)
    execution: ExecutionParams = field(default_factory=ExecutionParams)
    benchmarks: tuple[Benchmark, ...] = ()
    start: str = "2010-01-01"
    account_currency: str = "GBP"

    @property
    def risk_symbols(self) -> list[str]:
        return [a.symbol for a in self.assets if a.role == "risk"]

    @property
    def cash_symbol(self) -> str:
        return next(a.symbol for a in self.assets if a.role == "cash")

    @property
    def strategy_symbols(self) -> list[str]:
        return [a.symbol for a in self.assets]

    @property
    def all_symbols(self) -> list[str]:
        syms = list(self.strategy_symbols)
        for b in self.benchmarks:
            syms += [s for s in b.weights if s not in syms]
        return syms

    def asset(self, symbol: str) -> Asset:
        return next(a for a in self.assets if a.symbol == symbol)

    def with_strategy(self, **kw) -> "Config":
        return replace(self, strategy=replace(self.strategy, **kw))


def _build(cls, raw: dict | None):
    raw = raw or {}
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} keys: {sorted(unknown)}")
    if "lookbacks" in raw:
        raw = {**raw, "lookbacks": tuple(raw["lookbacks"])}
    return cls(**raw)


def validate(cfg: Config) -> Config:
    roles = [a.role for a in cfg.assets]
    if roles.count("cash") != 1:
        raise ValueError("Config needs exactly one asset with role: cash")
    if any(r not in ("risk", "cash") for r in roles):
        raise ValueError(f"Asset role must be 'risk' or 'cash', got {roles}")
    syms = [a.symbol for a in cfg.assets]
    if len(set(syms)) != len(syms):
        raise ValueError("Duplicate asset symbols in config")
    s = cfg.strategy
    if not 1 <= s.top_n <= len(cfg.risk_symbols):
        raise ValueError("top_n must be between 1 and the number of risk assets")
    if not 0 < s.max_weight <= 1:
        raise ValueError("max_weight must be in (0, 1]")
    if s.weighting not in ("inverse_vol", "equal"):
        raise ValueError("weighting must be 'inverse_vol' or 'equal'")
    if s.target_vol is not None and s.target_vol <= 0:
        raise ValueError("target_vol must be positive or null")
    e = cfg.execution
    if not 0 <= e.cash_buffer < 0.5:
        raise ValueError("cash_buffer must be in [0, 0.5)")
    for b in cfg.benchmarks:
        if abs(sum(b.weights.values()) - 1) > 1e-6:
            raise ValueError(f"Benchmark {b.name!r} weights must sum to 1")
    return cfg


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    raw = yaml.safe_load(path.read_text())
    cfg = Config(
        assets=tuple(_build(Asset, a) for a in raw["assets"]),
        strategy=_build(StrategyParams, raw.get("strategy")),
        execution=_build(ExecutionParams, raw.get("execution")),
        benchmarks=tuple(Benchmark(b["name"], dict(b["weights"])) for b in raw.get("benchmarks", [])),
        start=str(raw.get("start", "2010-01-01")),
        account_currency=raw.get("account_currency", "GBP"),
    )
    return validate(cfg)
