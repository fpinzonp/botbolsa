"""Análisis de noticias con un LLM: convierte titulares en señales de compra/venta.

Proveedores: "groq" (plan gratis, modelo gpt-oss) o "claude" (API de Anthropic, de pago).
"""
import json
import time
from typing import Literal

import anthropic
import requests
from pydantic import BaseModel, ValidationError

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """\
Eres un analista financiero cuantitativo y conservador. Recibes noticias recientes \
(acciones de EE.UU. y criptomonedas) y decides si alguna justifica operar a corto plazo \
(horas a pocos días).

Reglas:
- Analiza solo los símbolos que aparecen en el campo "symbols" de cada noticia.
- La gran mayoría de las noticias es ruido: notas de rutina, artículos de opinión, \
resúmenes de mercado, noticias ya descontadas por el precio. En esos casos no emitas señal.
- "buy": la noticia es un catalizador concreto, nuevo y material que probablemente suba \
el precio (resultados muy por encima de lo esperado, aprobación regulatoria, adquisición \
con prima, contrato grande, etc.).
- "sell": la noticia es claramente negativa y material para el símbolo (el bot solo opera \
en largo, así que "sell" significa cerrar una posición existente).
- confidence (0 a 1) debe estar calibrada: 0.9 o más solo para catalizadores inequívocos. \
Ante la duda, baja la confianza.
- impact: magnitud esperada del movimiento ("low", "medium" o "high").
- rationale: una o dos frases en español que expliquen la decisión.
- Devuelve una señal por cada par (noticia, símbolo) que merezca acción. Si ninguna lo \
merece, devuelve una lista vacía.
"""

# Esquema explícito (sin $ref) para el modo estricto de Groq.
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "news_id": {"type": "integer"},
                    "symbol": {"type": "string"},
                    "action": {"type": "string", "enum": ["buy", "sell"]},
                    "confidence": {"type": "number"},
                    "impact": {"type": "string", "enum": ["low", "medium", "high"]},
                    "rationale": {"type": "string"},
                },
                "required": ["news_id", "symbol", "action", "confidence", "impact", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["signals"],
    "additionalProperties": False,
}


class Signal(BaseModel):
    news_id: int
    symbol: str
    action: Literal["buy", "sell"]
    confidence: float
    impact: Literal["low", "medium", "high"]
    rationale: str


class Analysis(BaseModel):
    signals: list[Signal]


class AnalyzerError(Exception):
    """El proveedor no respondió; las noticias se reintentan en la próxima ejecución."""


def _user_message(news: list[dict], held: list[str]) -> str:
    payload = [
        {
            "id": n["id"],
            "headline": n.get("headline", ""),
            "summary": (n.get("summary") or "")[:400],
            "symbols": n.get("symbols", []),
            "source": n.get("source", ""),
        }
        for n in news
    ]
    return (
        f"Posiciones abiertas actualmente: {', '.join(held) or 'ninguna'}\n\n"
        f"Noticias:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def chunks(news: list[dict], max_chars: int = 9000) -> list[list[dict]]:
    """Agrupa noticias en lotes pequeños para respetar el límite de tokens por minuto."""
    batches: list[list[dict]] = [[]]
    size = 0
    for n in news:
        cost = len(n.get("headline", "")) + min(len(n.get("summary") or ""), 400) + 120
        if batches[-1] and size + cost > max_chars:
            batches.append([])
            size = 0
        batches[-1].append(n)
        size += cost
    return [b for b in batches if b]


def _groq(api_key: str, model: str, user: str) -> Analysis:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "analysis", "strict": True, "schema": ANALYSIS_SCHEMA},
        },
        "reasoning_effort": "low",
        "max_completion_tokens": 3000,
    }
    for attempt in range(3):
        try:
            resp = requests.post(GROQ_URL, json=body, timeout=90,
                                 headers={"Authorization": f"Bearer {api_key}"})
        except requests.RequestException as e:
            raise AnalyzerError(f"Groq: {e}") from e
        if resp.status_code == 429 and attempt < 2:
            # Límite por minuto: esperar lo que indique Groq. Si es el límite diario,
            # la espera es larga y es mejor rendirse y reintentar en otra ejecución.
            wait = float(resp.headers.get("retry-after") or 30)
            if wait > 90:
                raise AnalyzerError(f"Groq: límite alcanzado, reintento en {wait / 60:.0f} min")
            time.sleep(wait + 1)
            continue
        if resp.status_code != 200:
            raise AnalyzerError(f"Groq {resp.status_code}: {resp.text[:200]}")
        content = resp.json()["choices"][0]["message"].get("content") or ""
        try:
            return Analysis.model_validate_json(content)
        except ValidationError as e:
            raise AnalyzerError(f"Groq devolvió JSON inválido: {e.errors()[:1]}") from e
    raise AnalyzerError("Groq: límite de peticiones por minuto")


def _claude(model: str, user: str) -> Analysis:
    try:
        response = anthropic.Anthropic().messages.parse(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
            output_format=Analysis,
        )
    except anthropic.APIError as e:
        raise AnalyzerError(f"Claude: {e}") from e
    if response.stop_reason == "refusal" or response.parsed_output is None:
        print(f"Claude no devolvió un análisis (stop_reason={response.stop_reason})")
        return Analysis(signals=[])
    return response.parsed_output


def analyze(provider: str, model: str, api_key: str, news: list[dict], held: list[str]) -> list[Signal]:
    if not news:
        return []
    user = _user_message(news, held)
    result = _groq(api_key, model, user) if provider == "groq" else _claude(model, user)

    # Descarta señales sobre símbolos que no venían en la noticia correspondiente.
    allowed = {(n["id"], s.upper()) for n in news for s in n.get("symbols", [])}
    signals = []
    for s in result.signals:
        s.symbol = s.symbol.upper().replace("/", "")
        s.confidence = min(max(s.confidence, 0.0), 1.0)
        if (s.news_id, s.symbol) in allowed:
            signals.append(s)
    return signals
