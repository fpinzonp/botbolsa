"""Reglas de trading puras (sin llamadas de red) para poder probarlas fácilmente."""
from dataclasses import dataclass
from datetime import datetime

from .alpaca import norm
from .config import Settings


@dataclass
class Order:
    kind: str           # "buy" | "close"
    symbol: str         # símbolo normalizado (BTCUSD, AAPL)
    reason: str
    notional: float = 0.0
    news_id: int | None = None
    is_crypto: bool = False


def exit_orders(positions: list[dict], opened_at: dict[str, str], now: datetime,
                cfg: Settings) -> list[Order]:
    """Stop loss, take profit y tiempo máximo de permanencia."""
    orders = []
    for p in positions:
        sym = norm(p["symbol"])
        plpc = float(p.get("unrealized_plpc") or 0)
        crypto = p.get("asset_class") == "crypto"
        if plpc <= -cfg.stop_loss_pct:
            orders.append(Order("close", sym, f"Stop loss ({plpc:+.1%})", is_crypto=crypto))
        elif plpc >= cfg.take_profit_pct:
            orders.append(Order("close", sym, f"Toma de ganancia ({plpc:+.1%})", is_crypto=crypto))
        elif sym in opened_at:
            days = (now - datetime.fromisoformat(opened_at[sym])).total_seconds() / 86400
            if days >= cfg.max_hold_days:
                orders.append(Order("close", sym, f"Tiempo máximo ({days:.1f} días, {plpc:+.1%})",
                                    is_crypto=crypto))
    return orders


def reject_reason(s: dict, cfg: Settings) -> str | None:
    """Motivo para descartar una señal antes de considerarla, o None si pasa los filtros."""
    if s.get("asset") is None:
        return "Ignorada: símbolo no operable"
    if s["confidence"] < cfg.min_confidence:
        return f"Ignorada: confianza {s['confidence']:.0%} < {cfg.min_confidence:.0%}"
    if s["action"] == "buy" and s["impact"] == "low":
        return "Ignorada: impacto bajo"
    return None


def signal_orders(signals: list[dict], held: set[str], closing: set[str], equity: float,
                  cash: float, cfg: Settings) -> tuple[list[Order], dict[tuple[int, str], str]]:
    """Convierte señales en órdenes. Devuelve también el resultado de cada señal para el registro.

    signals: dicts con news_id, symbol, action, confidence, impact y la clave extra
    "asset" (activo operable de Alpaca o None).
    """
    outcomes: dict[tuple[int, str], str] = {}
    best: dict[str, dict] = {}
    conflicted: set[str] = set()

    for s in signals:
        reason = reject_reason(s, cfg)
        if reason:
            outcomes[(s["news_id"], s["symbol"])] = reason
        else:
            prev = best.get(s["symbol"])
            if prev and prev["action"] != s["action"]:
                conflicted.add(s["symbol"])
            if not prev or s["confidence"] > prev["confidence"]:
                best[s["symbol"]] = s

    orders: list[Order] = []
    open_slots = cfg.max_positions - len(held - closing)
    for sym, s in sorted(best.items(), key=lambda kv: -kv[1]["confidence"]):
        key = (s["news_id"], sym)
        crypto = s["asset"].get("class") == "crypto"
        if sym in conflicted:
            outcomes[key] = "Ignorada: señales contradictorias"
        elif s["action"] == "sell":
            if sym in held and sym not in closing:
                orders.append(Order("close", sym, "Noticia negativa", news_id=s["news_id"], is_crypto=crypto))
                closing.add(sym)
                outcomes[key] = "Venta"
            else:
                outcomes[key] = "Sin acción: no hay posición abierta"
        elif sym in held:
            outcomes[key] = "Sin acción: ya hay posición abierta"
        elif open_slots <= 0:
            outcomes[key] = f"Ignorada: máximo de {cfg.max_positions} posiciones"
        else:
            notional = min(equity * cfg.position_pct, cash * 0.95)
            if notional < 1:
                outcomes[key] = "Ignorada: efectivo insuficiente"
                continue
            orders.append(Order("buy", sym, "Noticia positiva", notional=round(notional, 2),
                                news_id=s["news_id"], is_crypto=crypto))
            cash -= notional
            open_slots -= 1
            outcomes[key] = "Compra"
    # Las señales que no fueron la mejor de su símbolo quedan sin resultado; se marcan aquí.
    for s in signals:
        outcomes.setdefault((s["news_id"], s["symbol"]), "Agrupada con otra señal del mismo símbolo")
    return orders, outcomes
