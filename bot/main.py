"""Una ejecución del bot. GitHub Actions la corre cada 15 minutos.

Flujo: salidas por riesgo -> noticias nuevas -> LLM -> órdenes -> datos del dashboard.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import analyzer, config, strategy
from .alpaca import Alpaca, norm

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "state.json"
DASHBOARD_FILE = ROOT / "docs" / "data.json"

MAX_SEEN_IDS = 2000
MAX_DECISIONS = 300
MAX_TRADES = 500
MAX_RUNS = 200
MAX_EQUITY_POINTS = 4000
MAX_FETCH_PER_RUN = 500   # el feed trae miles de noticias por hora, casi todas de otros mercados
MAX_ANALYZE_PER_RUN = 40  # para no agotar el cupo diario gratis de Groq
MAX_SYMBOLS_PER_NEWS = 3  # más símbolos suele ser un resumen de mercado: ruido que gasta tokens
STALE_HOURS = 6           # noticias más viejas ya no se analizan (p. ej. tras un límite diario)


def load_json(path: Path, default: dict) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run() -> None:
    cfg = config.load()
    now = datetime.now(timezone.utc)
    state = load_json(STATE_FILE, {"news_cursor": None, "seen_ids": [], "opened_at": {}, "pending": []})
    dash = load_json(DASHBOARD_FILE, {"equity_history": [], "decisions": [], "trades": [], "runs": []})

    alpaca = Alpaca(cfg.alpaca_key, cfg.alpaca_secret)

    account = alpaca.account()
    positions = alpaca.positions()
    market_open = bool(alpaca.clock().get("is_open"))
    held = {norm(p["symbol"]) for p in positions}
    manual = frozenset(held - set(state["opened_at"]))  # posiciones que no abrió el bot
    run_log = {"t": iso(now), "fetched": 0, "news": 0, "signals": 0, "orders": 0, "errors": []}
    trades: list[dict] = []

    def can_trade(is_crypto: bool) -> bool:
        return cfg.trade_crypto if is_crypto else (cfg.trade_stocks and market_open)

    def execute(order: strategy.Order, asset: dict | None = None) -> str:
        """Envía la orden y devuelve un texto con el resultado."""
        record = {"t": iso(now), "symbol": order.symbol, "side": order.kind, "reason": order.reason,
                  "notional": order.notional, "news_id": order.news_id, "crypto": order.is_crypto}
        if cfg.dry_run:
            record["status"] = "simulada (DRY_RUN)"
        else:
            try:
                if order.kind == "buy":
                    alpaca.buy_notional(asset, order.notional)
                    state["opened_at"][order.symbol] = iso(now)
                else:
                    alpaca.close_position(order.symbol)
                    state["opened_at"].pop(order.symbol, None)
                record["status"] = "enviada"
            except requests.HTTPError as e:
                detail = e.response.text[:200] if e.response is not None else str(e)
                record["status"] = f"error: {detail}"
                run_log["errors"].append(f"{order.kind} {order.symbol}: {detail}")
        trades.append(record)
        run_log["orders"] += 1
        return record["status"]

    # 1) Salidas por riesgo (no dependen del LLM).
    closing: set[str] = set()
    for order in strategy.exit_orders(positions, state["opened_at"], now, cfg):
        if can_trade(order.is_crypto):
            execute(order)
            closing.add(order.symbol)

    # 2) Noticias nuevas desde la última ejecución (de la más nueva a la más vieja).
    cursor = state["news_cursor"] or iso(now - timedelta(hours=1))
    seen = set(state["seen_ids"])
    fresh = [n for n in alpaca.news(cursor, MAX_FETCH_PER_RUN) if n["id"] not in seen]
    # Filtro sin costo antes del LLM: noticias recientes sobre 1-3 símbolos que Alpaca opera.
    stale_limit = now - timedelta(hours=STALE_HOURS)
    news = []
    for n in fresh:
        symbols = n.get("symbols") or []
        tradable = [norm(s) for s in symbols if alpaca.tradable_asset(s)]
        if tradable and len(symbols) <= MAX_SYMBOLS_PER_NEWS \
                and datetime.fromisoformat(n["created_at"]) >= stale_limit:
            news.append({**n, "symbols": tradable})
    news = news[:MAX_ANALYZE_PER_RUN]
    run_log["fetched"] = len(fresh)

    # Se analiza por lotes; si un lote falla (p. ej. límite de Groq), el resto queda para después.
    signals: list[analyzer.Signal] = []
    analyzed: list[dict] = []
    for batch in analyzer.chunks(news):
        try:
            signals += analyzer.analyze(cfg.analyzer, cfg.model, cfg.analyzer_key, batch,
                                        sorted(held - manual))
        except analyzer.AnalyzerError as e:
            run_log["errors"].append(str(e))
            break
        analyzed += batch
    run_log["news"] = len(analyzed)
    run_log["signals"] = len(signals)
    run_log["tokens"] = analyzer.usage["tokens"]

    enriched = [{**s.model_dump(), "asset": alpaca.tradable_asset(s.symbol), "t": iso(now)}
                for s in signals]

    # Las señales de acciones con el mercado cerrado quedan en cola hasta la apertura.
    limit = now - timedelta(hours=cfg.pending_max_hours)
    pending = [p for p in state["pending"] if datetime.fromisoformat(p["t"]) >= limit]
    # Al abrir el mercado se compara el precio actual con el de la señal: si ya subió más de
    # MAX_CHASE_PCT, reject_reason la descarta (el movimiento ya ocurrió sin nosotros).
    if pending and market_open:
        prices = alpaca.latest_stock_prices([p["symbol"] for p in pending if p.get("ref_price")])
        pending = [{**p, "price_now": prices.get(p["symbol"])} for p in pending]
    actionable, queued = [], []
    for s in pending + enriched:
        is_crypto = bool(s["asset"]) and s["asset"].get("class") == "crypto"
        if strategy.reject_reason(s, cfg) or can_trade(is_crypto):
            actionable.append(s)
        elif not is_crypto and cfg.trade_stocks:
            queued.append(s)
    # Precio de referencia de las señales que entran a la cola (el de este momento).
    prices = alpaca.latest_stock_prices([s["symbol"] for s in queued if not s.get("ref_price")])
    for s in queued:
        if not s.get("ref_price"):
            s["ref_price"] = prices.get(s["symbol"])
            if not s["ref_price"]:
                run_log["errors"].append(f"Sin precio de referencia para {s['symbol']}")
    state["pending"] = queued

    cash = float(account.get("non_marginable_buying_power") or account.get("cash") or 0)
    orders, outcomes = strategy.signal_orders(
        actionable, held, closing, float(account["equity"]), cash, cfg, manual)
    assets = {s["symbol"]: s["asset"] for s in actionable}
    for order in orders:
        status = execute(order, assets.get(order.symbol))
        key = (order.news_id, order.symbol)
        outcomes[key] = f"{outcomes[key]} ${order.notional:,.0f} · {status}" if order.kind == "buy" \
            else f"{outcomes[key]} · {status}"
    for s in queued:
        outcomes.setdefault((s["news_id"], s["symbol"]), "En cola: mercado cerrado")
    if not cfg.trade_crypto:
        for s in enriched:
            if s["asset"] and s["asset"].get("class") == "crypto":
                outcomes.setdefault((s["news_id"], s["symbol"]), "Ignorada: cripto desactivado")

    # Registro de decisiones para el dashboard (una entrada por noticia analizada).
    by_news: dict[int, list] = {}
    for s in enriched:
        by_news.setdefault(s["news_id"], []).append({
            "symbol": s["symbol"], "action": s["action"], "confidence": s["confidence"],
            "impact": s["impact"], "rationale": s["rationale"],
            "outcome": outcomes.get((s["news_id"], s["symbol"]), ""),
        })
    # Las señales que salieron de la cola actualizan el resultado de su noticia ya publicada.
    for d in dash["decisions"]:
        for sig in d["signals"]:
            key = (d["news_id"], sig["symbol"])
            if key in outcomes and sig.get("outcome", "").startswith("En cola"):
                sig["outcome"] = outcomes[key]
    for n in analyzed:
        dash["decisions"].append({
            "t": n.get("created_at"), "news_id": n["id"], "headline": n.get("headline"),
            "url": n.get("url"), "source": n.get("source"), "symbols": n.get("symbols", []),
            "signals": by_news.get(n["id"], []),
        })

    # El cursor avanza hasta la noticia más vieja que quedó sin analizar por un error del
    # proveedor (para reintentarla), o hasta la más nueva si no hubo errores.
    analyzed_ids = {n["id"] for n in analyzed}
    left_ids = {n["id"] for n in news} - analyzed_ids
    if left_ids:
        state["news_cursor"] = min(n["created_at"] for n in news if n["id"] in left_ids)
    elif fresh:
        state["news_cursor"] = max(n["created_at"] for n in fresh)
    done = [n["id"] for n in fresh if n["id"] not in left_ids]
    state["seen_ids"] = (state["seen_ids"] + done)[-MAX_SEEN_IDS:]

    # 3) Datos para el dashboard. Si se enviaron órdenes, primero se espera a que Alpaca las
    # ejecute; si no, el dashboard mostraría la cuenta como estaba antes de operar.
    if any(t["status"] == "enviada" for t in trades) and not alpaca.wait_for_fills():
        run_log["errors"].append("Órdenes aún pendientes al guardar el dashboard")
    account = alpaca.account()
    positions = alpaca.positions()
    # Olvida posiciones que ya no existen, salvo las compradas en esta ejecución: la orden
    # de mercado puede tardar unos segundos en ejecutarse y aún no aparecer como posición.
    current = {norm(p["symbol"]) for p in positions}
    for sym, opened in list(state["opened_at"].items()):
        if sym not in current and opened != iso(now):
            state["opened_at"].pop(sym)
    equity = float(account["equity"])
    dash.setdefault("starting_equity", equity)
    dash["equity_history"] = (dash["equity_history"] + [{"t": iso(now), "v": equity}])[-MAX_EQUITY_POINTS:]
    dash["decisions"] = dash["decisions"][-MAX_DECISIONS:]
    dash["trades"] = (dash["trades"] + trades)[-MAX_TRADES:]
    dash["runs"] = (dash["runs"] + [run_log])[-MAX_RUNS:]
    dash.update({
        "updated_at": iso(now),
        "market_open": market_open,
        "dry_run": cfg.dry_run,
        "model": f"{cfg.analyzer} · {cfg.model}",
        "rules": {
            "min_confidence": cfg.min_confidence, "position_pct": cfg.position_pct,
            "max_positions": cfg.max_positions, "stop_loss_pct": cfg.stop_loss_pct,
            "take_profit_pct": cfg.take_profit_pct, "max_hold_days": cfg.max_hold_days,
        },
        "account": {k: float(account.get(k) or 0) for k in ("equity", "cash", "last_equity", "buying_power")},
        "pending": len(state["pending"]),
        "positions": [{
            "symbol": norm(p["symbol"]),
            "crypto": p.get("asset_class") == "crypto",
            "qty": float(p["qty"]),
            "avg_entry_price": float(p["avg_entry_price"]),
            "current_price": float(p["current_price"]),
            "market_value": float(p["market_value"]),
            "unrealized_pl": float(p["unrealized_pl"]),
            "unrealized_plpc": float(p["unrealized_plpc"]),
            "opened_at": state["opened_at"].get(norm(p["symbol"])),
        } for p in positions],
    })

    save_json(STATE_FILE, state)
    save_json(DASHBOARD_FILE, dash)
    print(json.dumps(run_log, ensure_ascii=False))


if __name__ == "__main__":
    run()
