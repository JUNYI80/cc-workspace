import pytest

from t212quant import cli, research
from t212quant.data import synthetic_prices


def test_build_config():
    cfg = research.build_config(["tsla", "nvda"])
    assert cfg.risk_symbols == ["TSLA", "NVDA"]
    assert cfg.cash_symbol == research.CASH_PROXY
    assert cfg.strategy.top_n == 2 and cfg.strategy.weighting == "equal"
    assert cfg.strategy.target_vol is None
    assert {s for b in cfg.benchmarks for s in b.weights} == {"TSLA", "NVDA", "SPY"}


def test_cash_proxy_cannot_be_studied():
    with pytest.raises(ValueError):
        research.build_config(["BIL"])


def _study(symbols):
    cfg = research.build_config(symbols)
    prices = synthetic_prices(cfg.all_symbols, years=8, cash_symbol=cfg.cash_symbol)
    return research.run(cfg, prices)


def test_study_summary_mentions_each_stock_and_signal():
    study = _study(["TSLA", "NVDA"])
    text = research.format_study(study)
    assert "TSLA" in text and "NVDA" in text
    assert "当前信号" in text and "最大回撤" in text
    for s in ("TSLA", "NVDA"):
        w = study.signal.weights[s]
        assert w in (0.0, pytest.approx(0.5))  # each stock is either out or its full half


def test_stock_slot_is_all_or_nothing():
    study = _study(["TSLA"])
    w = study.results["Strategy"].weights["TSLA"]
    assert set(w.round(6).unique()) <= {0.0, 1.0}  # fully in the stock, or fully out


def test_research_command_writes_chart(tmp_path, capsys):
    out = tmp_path / "chart.png"
    assert cli.main(["research", "TSLA", "NVDA", "--synthetic", "--plot", str(out)]) == 0
    assert out.exists() and out.stat().st_size > 10_000
    assert "趋势策略" in capsys.readouterr().out
