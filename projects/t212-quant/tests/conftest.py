import numpy as np
import pandas as pd
import pytest

from t212quant.config import Asset, Config, ExecutionParams, StrategyParams


@pytest.fixture
def params():
    return StrategyParams(lookbacks=(21, 63), trend_window=50, top_n=2, vol_window=21,
                          target_vol=None, max_weight=1.0)


@pytest.fixture
def small_cfg(params):
    assets = (
        Asset("UP", "rising", "ISIN_UP"),
        Asset("UP2", "rising slower", "ISIN_UP2"),
        Asset("DOWN", "falling", "ISIN_DOWN"),
        Asset("CASH", "cash", "ISIN_CASH", role="cash"),
    )
    return Config(assets=assets, strategy=params, execution=ExecutionParams())


def trend_prices(n=300, start="2020-01-01", seed=0) -> pd.DataFrame:
    """UP rises fast, UP2 rises slowly, DOWN falls, CASH creeps up."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    noise = rng.normal(0, 0.004, (n, 4))
    drift = np.array([0.0015, 0.0007, -0.001, 0.0001])
    noise[:, 3] = 0.00002
    return pd.DataFrame(100 * np.exp(np.cumsum(drift + noise, axis=0)), index=idx,
                        columns=["UP", "UP2", "DOWN", "CASH"])
