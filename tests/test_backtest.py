import numpy as np

from backtest import stops


def test_fixed_stop_exits_at_first_hit():
    close = np.array([100, 97, 94, 120, 130, 140], dtype=float)
    stops.STEP_DAYS = 100  # una sola compra, el día 0
    x = stops.simulate(close, 5, "fixed", {"sl": 0.05})
    assert x["stopped"].tolist() == [True]
    assert np.isclose(x["ret"][0], -0.06)        # sale el día 2 a 94
    assert np.isclose(x["hold"][0], 0.40)        # aguantando habría ganado 40 %
    s = stops.summarize(x["ret"], x["stopped"], x["hold"])
    assert s["vendidas_que_se_recuperaron"] == 1.0


def test_take_profit_and_trailing():
    close = np.array([100, 112, 90, 95, 95, 95], dtype=float)
    stops.STEP_DAYS = 100
    tp = stops.simulate(close, 5, "fixed", {"sl": 0.05, "tp": 0.10})
    assert np.isclose(tp["ret"][0], 0.12)
    trail = stops.simulate(close, 5, "trailing", {"sl": 0.15})
    assert np.isclose(trail["ret"][0], -0.10)    # 112 -> 90 es −19.6 % desde el máximo
    none = stops.simulate(close, 5, "none", {})
    assert not none["stopped"][0] and np.isclose(none["ret"][0], -0.05)


def test_run_produces_every_rule():
    rng = np.random.default_rng(0)
    closes = {s: 100 * np.cumprod(1 + rng.normal(0.0004, 0.02, 600)) for s in ("AAA", "NVDA", "SPY")}
    stops.STEP_DAYS = 5
    r = stops.run(closes)
    assert len(r["resultados"]) == len(stops.RULES) and r["acciones"] == 2
    assert "spy_promedio" in r["resultados"][0] and r["nvda"]
    assert "## A 1 año" in stops.markdown(r)
