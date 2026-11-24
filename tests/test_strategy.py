from dataclasses import replace
from datetime import datetime, timedelta, timezone

from bot import strategy
from bot.config import Settings

CFG = Settings(
    alpaca_key="k", alpaca_secret="s", analyzer="groq", analyzer_key="g", model="m", min_confidence=0.75, position_pct=0.05,
    max_positions=3, stop_loss_pct=0.05, take_profit_pct=0.10, max_hold_days=5,
    pending_max_hours=16, trade_stocks=True, trade_crypto=True, dry_run=False,
)
STOCK = {"symbol": "AAPL", "class": "us_equity"}
BTC = {"symbol": "BTC/USD", "class": "crypto"}
NOW = datetime(2026, 11, 24, 15, tzinfo=timezone.utc)


def sig(symbol, action="buy", conf=0.9, impact="high", news_id=1, asset=STOCK):
    return {"news_id": news_id, "symbol": symbol, "action": action, "confidence": conf,
            "impact": impact, "asset": asset}


def test_exits_stop_loss_take_profit_and_age():
    positions = [
        {"symbol": "AAPL", "unrealized_plpc": "-0.06", "asset_class": "us_equity"},
        {"symbol": "BTCUSD", "unrealized_plpc": "0.12", "asset_class": "crypto"},
        {"symbol": "MSFT", "unrealized_plpc": "0.01", "asset_class": "us_equity"},
        {"symbol": "NVDA", "unrealized_plpc": "0.01", "asset_class": "us_equity"},
    ]
    opened = {"MSFT": (NOW - timedelta(days=6)).isoformat(), "NVDA": NOW.isoformat()}
    orders = strategy.exit_orders(positions, opened, NOW, CFG)
    assert [(o.symbol, o.reason.split(" (")[0]) for o in orders] == [
        ("AAPL", "Stop loss"), ("BTCUSD", "Toma de ganancia"), ("MSFT", "Tiempo máximo")]
    assert orders[1].is_crypto


def test_buy_sizing_and_filters():
    signals = [
        sig("AAPL"),
        sig("MSFT", conf=0.5, news_id=2),
        sig("TSLA", impact="low", news_id=3),
        sig("XYZ", asset=None, news_id=4),
        sig("BTCUSD", asset=BTC, news_id=5),
    ]
    orders, outcomes = strategy.signal_orders(signals, set(), set(), 100_000, 100_000, CFG)
    assert [(o.symbol, o.notional, o.is_crypto) for o in orders] == [
        ("AAPL", 5000, False), ("BTCUSD", 5000, True)]
    assert outcomes[(2, "MSFT")].startswith("Ignorada: confianza")
    assert outcomes[(3, "TSLA")] == "Ignorada: impacto bajo"
    assert outcomes[(4, "XYZ")] == "Ignorada: símbolo no operable"


def test_sell_only_closes_existing_and_conflicts_skip():
    signals = [sig("AAPL", action="sell"), sig("MSFT", action="sell", news_id=2),
               sig("NVDA", news_id=3), sig("NVDA", action="sell", news_id=4)]
    orders, outcomes = strategy.signal_orders(signals, {"AAPL"}, set(), 100_000, 100_000, CFG)
    assert [(o.kind, o.symbol) for o in orders] == [("close", "AAPL")]
    assert outcomes[(2, "MSFT")] == "Sin acción: no hay posición abierta"
    assert "contradictorias" in outcomes[(3, "NVDA")] or "contradictorias" in outcomes[(4, "NVDA")]


def test_max_positions_and_cash_limit():
    signals = [sig(s, news_id=i, conf=0.8 + i / 100) for i, s in enumerate(["A", "B", "C"])]
    orders, outcomes = strategy.signal_orders(signals, {"X", "Y"}, set(), 100_000, 2_000,
                                              replace(CFG, max_positions=3))
    assert len(orders) == 1 and orders[0].symbol == "C"   # la de mayor confianza
    assert orders[0].notional == 1900                      # limitado por el efectivo
    assert "máximo" in outcomes[(0, "A")]
