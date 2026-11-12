"""AI gateway: the only place that talks to model providers.

Pinned model IDs, retries with jitter (429/5xx/529 via SDK), prompt caching on static prefixes, cost per call,
a daily spend cap, and fixture record/replay so scripted demos and CI cost $0 and are deterministic.
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..config import settings

# USD per million tokens (Anthropic list prices, Oct 2026). cache_read is the cached-input rate.
PRICES = {
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.10, "cache_write": 2.50},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "jev": {"in": 0.042, "out": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    # Vertex AI, global endpoint. Intro rates; 3.8 Flash doubles on 1 Jan 2027, 4 Argon on 1 Dec 2026.
    "gemini-3.8-flash": {"in": 0.75, "out": 3.75, "cache_read": 0.075, "cache_write": 0.75},
    "gemini-3.5-flash-lite": {"in": 0.30, "out": 2.50, "cache_read": 0.03, "cache_write": 0.30},
    "gemini-4-argon": {"in": 2.00, "out": 10.00, "cache_read": 0.20, "cache_write": 2.00},
}


class NoBackend(Exception):
    """No live key and no recorded fixture: the caller must degrade."""


class SpendCapReached(NoBackend):
    pass


@dataclass
class CallRecord:
    kind: str
    model: str
    source: str  # live | fixture
    ms: int
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0  # at live list prices; a replayed fixture costs $0 in real spend (not added to the daily cap)


_spend_lock = threading.Lock()
_spend: dict[str, float] = {}


def _add_spend(usd: float) -> None:
    with _spend_lock:
        k = date.today().isoformat()
        _spend[k] = _spend.get(k, 0.0) + usd


def spent_today() -> float:
    return _spend.get(date.today().isoformat(), 0.0)


def cost_of(model: str, usage: dict) -> float:
    p = PRICES.get(model) or PRICES.get("jev" if "jev" in model else "claude-sonnet-5-5")
    return round((usage.get("input_tokens", 0) * p["in"] + usage.get("output_tokens", 0) * p["out"]
                  + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
                  + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]) / 1e6, 6)


def fixture_key(kind: str, payload: Any) -> str:
    return hashlib.sha256(json.dumps({"kind": kind, "payload": payload}, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _fixture_path(kind: str, key: str):
    return settings().fixtures_dir / f"{kind}-{key}.json"


def load_fixture(kind: str, key: str) -> dict | None:
    p = _fixture_path(kind, key)
    return json.loads(p.read_text()) if p.exists() else None


def save_fixture(kind: str, key: str, response: dict) -> None:
    _fixture_path(kind, key).write_text(json.dumps(response, indent=1, default=str))


_client = None


def anthropic_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(api_key=settings().anthropic_api_key, max_retries=4, timeout=90)
    return _client


def claude(kind: str, fixture_payload: Any, **create_kwargs) -> tuple[dict, CallRecord]:
    """Call Claude messages.create (or replay). `fixture_payload` identifies the request without binary data."""
    s = settings()
    model = create_kwargs["model"]
    key = fixture_key(kind, {"model": model, **({"p": fixture_payload})})
    t0 = time.monotonic()
    if s.ai_mode in ("fixture", "auto"):
        fx = load_fixture(kind, key)
        if fx is not None:
            return fx, CallRecord(kind, model, "fixture", int((time.monotonic() - t0) * 1000), fx.get("usage", {}), cost_of(model, fx.get("usage", {})))
        if s.ai_mode == "fixture":
            raise NoBackend(f"no fixture for {kind}")
    if not s.claude_enabled:
        raise NoBackend("ANTHROPIC_API_KEY not set")
    if spent_today() >= s.daily_spend_cap_usd:
        raise SpendCapReached(f"daily spend cap ${s.daily_spend_cap_usd} reached")
    msg = anthropic_client().messages.create(**create_kwargs)
    resp = msg.model_dump(mode="json")
    usage = resp.get("usage") or {}
    rec = CallRecord(kind, resp.get("model", model), "live", int((time.monotonic() - t0) * 1000), usage, cost_of(model, usage))
    _add_spend(rec.cost_usd)
    save_fixture(kind, key, resp)
    return resp, rec


def text_of(resp: dict) -> str:
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


def cached_system(text: str) -> list[dict]:
    """Static system prompt marked for prompt caching (cache hits need an identical prefix on the same model)."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


# ---------------------------------------------------------------- Gemini on Vertex AI (default provider, §17)

_genai = None


def genai_client():
    global _genai
    if _genai is None:
        from google import genai

        _genai = genai.Client(vertexai=True, api_key=settings().vertex_api_key)
    return _genai


def gemini(kind: str, fixture_payload: Any, model: str, contents: list, system: str | None = None,
           schema: dict | None = None, thinking: str | None = "low", media_resolution: str | None = None,
           max_output_tokens: int = 4096) -> tuple[dict, CallRecord]:
    """Call Gemini generate_content (or replay a fixture). Returns {"text", "usage", "model"}.
    `contents` may hold str and {"bytes": b, "mime": m} parts; binaries are identified in the fixture key by caller."""
    s = settings()
    key = fixture_key(kind, {"model": model, "p": fixture_payload})
    t0 = time.monotonic()
    if s.ai_mode in ("fixture", "auto"):
        fx = load_fixture(kind, key)
        if fx is not None:
            return fx, CallRecord(kind, model, "fixture", int((time.monotonic() - t0) * 1000), fx.get("usage", {}), cost_of(model, fx.get("usage", {})))
        if s.ai_mode == "fixture":
            raise NoBackend(f"no fixture for {kind}")
    if not s.gemini_enabled:
        raise NoBackend("VERTEX_API_KEY not set")
    if spent_today() >= s.daily_spend_cap_usd:
        raise SpendCapReached(f"daily spend cap ${s.daily_spend_cap_usd} reached")
    from google.genai import types

    parts = []
    for c in contents:
        if isinstance(c, dict) and "bytes" in c:
            parts.append(types.Part.from_bytes(data=c["bytes"], mime_type=c["mime"]))
        else:
            parts.append(c)
    cfg: dict[str, Any] = {"max_output_tokens": max_output_tokens, "temperature": 0}
    if system:
        cfg["system_instruction"] = system
    if schema:
        cfg["response_mime_type"] = "application/json"
        cfg["response_json_schema"] = schema
    if thinking:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_level=thinking)
    if media_resolution:
        cfg["media_resolution"] = media_resolution
    last_err = None
    for attempt in range(4):  # retry 429 / 5xx with jitter
        try:
            r = genai_client().models.generate_content(model=model, contents=parts, config=types.GenerateContentConfig(**cfg))
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            if code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep((2 ** attempt) * 0.5 + random.random() * 0.3)
    else:  # pragma: no cover
        raise last_err  # type: ignore[misc]
    u = r.usage_metadata
    usage = {"input_tokens": u.prompt_token_count or 0,
             "output_tokens": (u.candidates_token_count or 0) + (u.thoughts_token_count or 0),
             "cache_read_input_tokens": u.cached_content_token_count or 0}
    usage["input_tokens"] -= usage["cache_read_input_tokens"]
    resp = {"text": r.text or "", "usage": usage, "model": r.model_version or model}
    rec = CallRecord(kind, resp["model"], "live", int((time.monotonic() - t0) * 1000), usage, cost_of(model, usage))
    _add_spend(rec.cost_usd)
    save_fixture(kind, key, resp)
    return resp, rec
