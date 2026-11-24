import json

import pytest

from bot import analyzer

NEWS = [{"id": 1, "headline": "Apple beats", "symbols": ["AAPL"]},
        {"id": 2, "headline": "BTC hack", "symbols": ["BTCUSD"]}]


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


def ok(signals):
    return Resp(200, {"choices": [{"message": {"content": json.dumps({"signals": signals})}}]})


def fake_post(monkeypatch, responses):
    calls = []

    def post(url, json, timeout, headers):
        calls.append(json)
        return responses.pop(0)
    monkeypatch.setattr(analyzer.requests, "post", post)
    monkeypatch.setattr(analyzer.time, "sleep", lambda s: None)
    return calls


def test_groq_filters_and_normalizes(monkeypatch):
    calls = fake_post(monkeypatch, [ok([
        {"news_id": 1, "symbol": "aapl", "action": "buy", "confidence": 1.4, "impact": "high", "rationale": "ok"},
        {"news_id": 1, "symbol": "TSLA", "action": "buy", "confidence": 0.9, "impact": "high", "rationale": "no venía"},
        {"news_id": 2, "symbol": "BTC/USD", "action": "sell", "confidence": 0.8, "impact": "medium", "rationale": "x"},
    ])])
    sigs = analyzer.analyze("groq", "openai/gpt-oss-120b", "key", NEWS, [])
    assert [(s.symbol, s.action, s.confidence) for s in sigs] == [("AAPL", "buy", 1.0), ("BTCUSD", "sell", 0.8)]
    fmt = calls[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True


def test_groq_retries_short_rate_limit(monkeypatch):
    calls = fake_post(monkeypatch, [Resp(429, {}, {"retry-after": "5"}), ok([])])
    assert analyzer.analyze("groq", "m", "key", NEWS, []) == []
    assert len(calls) == 2


def test_groq_gives_up_on_daily_limit(monkeypatch):
    fake_post(monkeypatch, [Resp(429, {}, {"retry-after": "3600"})])
    with pytest.raises(analyzer.AnalyzerError, match="límite"):
        analyzer.analyze("groq", "m", "key", NEWS, [])


def test_groq_invalid_json_is_an_error(monkeypatch):
    fake_post(monkeypatch, [Resp(200, {"choices": [{"message": {"content": "no es json"}}]})])
    with pytest.raises(analyzer.AnalyzerError):
        analyzer.analyze("groq", "m", "key", NEWS, [])


def test_chunks_split_by_size():
    news = [{"id": i, "headline": "x" * 100, "summary": "y" * 1000} for i in range(30)]
    batches = analyzer.chunks(news, max_chars=2000)
    assert sum(len(b) for b in batches) == 30
    assert all(len(b) <= 3 for b in batches)
