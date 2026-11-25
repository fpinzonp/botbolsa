"""Configuración del bot, leída de variables de entorno (GitHub Secrets / Variables)."""
import os
from dataclasses import dataclass

# El bot SOLO habla con la API de paper trading. No hay variable para cambiarlo:
# pasar a dinero real debe ser una decisión explícita editando este archivo.
PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"

DEFAULT_MODELS = {"groq": "openai/gpt-oss-120b", "claude": "claude-haiku-5-5"}


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if not value:
        return default
    return value.strip().lower() in ("1", "true", "yes", "si", "sí")


@dataclass(frozen=True)
class Settings:
    alpaca_key: str
    alpaca_secret: str
    analyzer: str              # "groq" (gratis) o "claude"
    analyzer_key: str
    model: str
    # Reglas de riesgo
    min_confidence: float      # confianza mínima de Claude para actuar (0-1)
    position_pct: float        # % del portafolio por operación
    max_positions: int         # posiciones abiertas simultáneas
    stop_loss_pct: float       # cierra si la posición pierde más de esto
    take_profit_pct: float     # cierra si la posición gana más de esto
    max_hold_days: float       # cierra posiciones viejas aunque no lleguen a SL/TP
    pending_max_hours: float   # vigencia de señales de acciones con el mercado cerrado
    max_chase_pct: float       # no compra una señal en cola si el precio ya subió más de esto
    trade_stocks: bool
    trade_crypto: bool
    dry_run: bool              # analiza y registra, pero no envía órdenes


def load() -> Settings:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("Faltan ALPACA_API_KEY / ALPACA_SECRET_KEY")
    analyzer = (os.environ.get("ANALYZER") or "groq").strip().lower()
    if analyzer not in DEFAULT_MODELS:
        raise SystemExit(f"ANALYZER debe ser uno de {list(DEFAULT_MODELS)}")
    key_var = "GROQ_API_KEY" if analyzer == "groq" else "ANTHROPIC_API_KEY"
    if not os.environ.get(key_var):
        raise SystemExit(f"Falta {key_var}")
    return Settings(
        alpaca_key=key,
        alpaca_secret=secret,
        analyzer=analyzer,
        analyzer_key=os.environ[key_var],
        model=os.environ.get("MODEL") or DEFAULT_MODELS[analyzer],
        min_confidence=_float("MIN_CONFIDENCE", 0.75),
        position_pct=_float("POSITION_PCT", 0.05),
        max_positions=int(_float("MAX_POSITIONS", 10)),
        stop_loss_pct=_float("STOP_LOSS_PCT", 0.05),
        take_profit_pct=_float("TAKE_PROFIT_PCT", 0.10),
        max_hold_days=_float("MAX_HOLD_DAYS", 5),
        pending_max_hours=_float("PENDING_MAX_HOURS", 16),
        max_chase_pct=_float("MAX_CHASE_PCT", 0.03),
        trade_stocks=_bool("TRADE_STOCKS", True),
        trade_crypto=_bool("TRADE_CRYPTO", True),
        dry_run=_bool("DRY_RUN", False),
    )
