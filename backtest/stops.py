"""Simulación histórica: ¿conviene el stop loss en acciones del S&P 500?

Descarga precios diarios ajustados (splits y dividendos) de las acciones actuales del
S&P 500 y simula compras en muchas fechas, comparando reglas de salida a 5 días
(como el bot de noticias) y a 1 año (largo plazo).

Uso (en GitHub Actions, con los secrets de Alpaca): python -m backtest.stops
Escribe backtest/results.md y backtest/results.json.

Limitaciones (se repiten en el informe):
- Solo empresas que HOY están en el S&P 500: las que cayeron y salieron del índice no
  están, así que los resultados sin stop salen mejores que en la realidad.
- Las compras son en fechas al azar, no por noticias.
- El stop se revisa con el cierre diario; el bot real revisa cada 15 minutos.
"""
import csv
import io
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import requests
from numpy.lib.stride_tricks import sliding_window_view

from bot.config import DATA_BASE_URL

OUT = Path(__file__).resolve().parent
START = os.environ.get("BT_START", "2016-01-01")
CONSTITUENTS_URL = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
                    "main/data/constituents.csv")
BENCHMARK = "SPY"
STEP_DAYS = 5          # una compra simulada cada 5 días hábiles por acción

# (nombre, horizonte en días hábiles, tipo, parámetros)
RULES = [
    ("Sin stop", 5, "none", {}),
    ("Bot actual: stop −5 % / ganancia +10 %", 5, "fixed", {"sl": 0.05, "tp": 0.10}),
    ("Sin stop", 252, "none", {}),
    ("Stop −5 %", 252, "fixed", {"sl": 0.05}),
    ("Stop −15 %", 252, "fixed", {"sl": 0.15}),
    ("Stop −25 %", 252, "fixed", {"sl": 0.25}),
    ("Trailing −15 % desde el máximo", 252, "trailing", {"sl": 0.15}),
    ("Trailing −25 % desde el máximo", 252, "trailing", {"sl": 0.25}),
]


# --- Datos ----------------------------------------------------------------------
def sp500_symbols() -> list[str]:
    text = requests.get(CONSTITUENTS_URL, timeout=30).text
    return sorted({row["Symbol"].strip() for row in csv.DictReader(io.StringIO(text))})


def daily_closes(symbols: list[str], key: str, secret: str) -> dict[str, np.ndarray]:
    """Cierres diarios ajustados por splits y dividendos (retorno total aproximado)."""
    http = requests.Session()
    http.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
    closes: dict[str, list[float]] = {}
    for i in range(0, len(symbols), 50):
        params = {"symbols": ",".join(symbols[i:i + 50]), "timeframe": "1Day", "start": START,
                  "adjustment": "all", "feed": "sip", "limit": 10000}
        while True:
            resp = http.get(f"{DATA_BASE_URL}/v2/stocks/bars", params=params, timeout=60)
            resp.raise_for_status()
            page = resp.json()
            for sym, bars in (page.get("bars") or {}).items():
                closes.setdefault(sym, []).extend(b["c"] for b in bars)
            if not page.get("next_page_token"):
                break
            params["page_token"] = page["next_page_token"]
        print(f"precios: {min(i + 50, len(symbols))}/{len(symbols)}", file=sys.stderr)
    return {s: np.asarray(c, dtype=float) for s, c in closes.items()}


# --- Simulación -----------------------------------------------------------------
def simulate(close: np.ndarray, horizon: int, kind: str, p: dict) -> dict[str, np.ndarray]:
    """Retorno de cada compra simulada y si salió antes de tiempo por la regla."""
    if len(close) < horizon + 1:
        return {"ret": np.empty(0), "stopped": np.empty(0, bool), "hold": np.empty(0)}
    paths = sliding_window_view(close, horizon + 1)[::STEP_DAYS]
    rel = paths / paths[:, :1]                     # precio relativo a la compra
    hold = rel[:, -1] - 1                          # resultado si se aguanta hasta el final
    if kind == "none":
        return {"ret": hold, "stopped": np.zeros(len(hold), bool), "hold": hold}
    if kind == "fixed":
        hit = rel <= 1 - p["sl"]
        if "tp" in p:
            hit |= rel >= 1 + p["tp"]
    else:  # trailing: cae sl desde el máximo alcanzado
        hit = rel <= np.maximum.accumulate(rel, axis=1) * (1 - p["sl"])
    hit[:, 0] = False
    stopped = hit.any(axis=1)
    first = np.where(stopped, hit.argmax(axis=1), horizon)
    ret = rel[np.arange(len(rel)), first] - 1
    return {"ret": ret, "stopped": stopped, "hold": hold}


def summarize(ret: np.ndarray, stopped: np.ndarray, hold: np.ndarray) -> dict:
    s = stopped
    return {
        "compras": int(len(ret)),
        "promedio": float(ret.mean()),
        "mediana": float(np.median(ret)),
        "ganadoras": float((ret > 0).mean()),
        "peor_5pct": float(np.percentile(ret, 5)),
        "mejor_5pct": float(np.percentile(ret, 95)),
        "salidas_por_regla": float(s.mean()),
        # De las que la regla vendió antes: cuántas habrían terminado mejor aguantando.
        "vendidas_que_se_recuperaron": float((hold[s] > ret[s]).mean()) if s.any() else 0.0,
    }


def run(closes: dict[str, np.ndarray]) -> dict:
    results = []
    for name, horizon, kind, p in RULES:
        parts = [simulate(c, horizon, kind, p) for s, c in closes.items() if s != BENCHMARK]
        ret = np.concatenate([x["ret"] for x in parts])
        stopped = np.concatenate([x["stopped"] for x in parts])
        hold = np.concatenate([x["hold"] for x in parts])
        row = {"regla": name, "horizonte_dias": horizon, **summarize(ret, stopped, hold)}
        if BENCHMARK in closes:
            b = simulate(closes[BENCHMARK], horizon, "none", {})
            row["spy_promedio"] = float(b["ret"].mean())
        results.append(row)
    nvda = {}
    if "NVDA" in closes:
        for name, horizon, kind, p in RULES:
            x = simulate(closes["NVDA"], horizon, kind, p)
            nvda[f"{name} ({horizon}d)"] = summarize(x["ret"], x["stopped"], x["hold"])
    return {"desde": START, "hasta": str(date.today()), "acciones": len(closes) - (BENCHMARK in closes),
            "resultados": results, "nvda": nvda}


# --- Informe --------------------------------------------------------------------
def pct(x: float) -> str:
    return f"{x * 100:+.1f} %"


def markdown(r: dict) -> str:
    lines = [f"# Simulación de stop loss — S&P 500 ({r['desde']} a {r['hasta']}, {r['acciones']} acciones)", ""]
    for horizon, title in ((5, "A 5 días (bot de noticias)"), (252, "A 1 año (largo plazo)")):
        lines += [f"## {title}", "",
                  "| Regla | Promedio | Mediana | Ganadoras | Peor 5 % | Mejor 5 % | Salió por la regla | De esas, se recuperaron | SPY promedio |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for x in r["resultados"]:
            if x["horizonte_dias"] == horizon:
                lines.append(f"| {x['regla']} | {pct(x['promedio'])} | {pct(x['mediana'])} | "
                             f"{x['ganadoras']:.0%} | {pct(x['peor_5pct'])} | {pct(x['mejor_5pct'])} | "
                             f"{x['salidas_por_regla']:.0%} | {x['vendidas_que_se_recuperaron']:.0%} | "
                             f"{pct(x.get('spy_promedio', float('nan')))} |")
        lines.append("")
    if r["nvda"]:
        lines += ["## Solo NVDA", "", "| Regla | Promedio | Peor 5 % | Salió por la regla | De esas, se recuperaron |",
                  "|---|---|---|---|---|"]
        for k, x in r["nvda"].items():
            lines.append(f"| {k} | {pct(x['promedio'])} | {pct(x['peor_5pct'])} | "
                         f"{x['salidas_por_regla']:.0%} | {x['vendidas_que_se_recuperaron']:.0%} |")
        lines.append("")
    lines += ["Limitaciones: solo empresas que hoy están en el S&P 500 (sesgo de supervivencia: "
              "favorece aguantar sin stop); compras en fechas al azar, no por noticias; el stop se "
              "revisa con el cierre diario. Precios ajustados por dividendos."]
    return "\n".join(lines)


def main() -> None:
    key, secret = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
    closes = daily_closes(sp500_symbols() + [BENCHMARK], key, secret)
    result = run(closes)
    (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    report = markdown(result)
    (OUT / "results.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
