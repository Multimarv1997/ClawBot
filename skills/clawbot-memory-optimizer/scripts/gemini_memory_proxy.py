#!/usr/bin/env python3
"""HTTP-Proxy mit exact+semantic cache, verbessertem Cross-Session-Memory und Fact-Management."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from typing import Any, List, Optional

import requests
from flask import Flask, jsonify, request

from memory_engine import MemoryEngine


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "text-embedding-004")
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GEMINI_EMBED_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_EMBED_MODEL}:embedContent"

PII_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"\+?\d[\d\s\-/()]{6,}\d"), "[PHONE]"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), "[IBAN]"),
    (re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b"), "[CARD]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
]

FACT_PATTERNS = [
    (re.compile(r"\bich bevorzuge\b", re.IGNORECASE), "preference", 0.85, "user"),
    (re.compile(r"\bmein name ist\b", re.IGNORECASE), "identity", 0.70, "user"),
    (re.compile(r"\bbitte antworte\b", re.IGNORECASE), "style", 0.9, "session"),
]

app = Flask(__name__)
engine = MemoryEngine()
engine.init_db()


def get_api_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def scrub_pii(text: str) -> str:
    out = text
    for pattern, token in PII_PATTERNS:
        out = pattern.sub(token, out)
    return out


def local_fallback_embedding(text: str, dim: int = 64) -> List[float]:
    vec = [0.0] * dim
    for tok in text.lower().split():
        vec[hash(tok) % dim] += 1.0
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec] if norm else vec


def embed_text(text: str) -> List[float]:
    api_key = get_api_key()
    if not api_key:
        return local_fallback_embedding(text)
    try:
        resp = requests.post(
            GEMINI_EMBED_ENDPOINT,
            params={"key": api_key},
            json={"content": {"parts": [{"text": text}]}},
            timeout=30,
        )
        resp.raise_for_status()
        values = resp.json().get("embedding", {}).get("values", [])
        if isinstance(values, list) and values:
            cleaned = [float(v) for v in values if isinstance(v, (int, float))]
            if cleaned and all(math.isfinite(v) for v in cleaned):
                return cleaned
            logger.warning("Embedding response invalid, fallback used")
    except requests.RequestException as err:
        logger.warning("Embedding endpoint failed, fallback used: %s", err)
    return local_fallback_embedding(text)


def call_gemini(prompt: str) -> str:
    api_key = get_api_key()
    if not api_key:
        return "[Demo-Antwort] GEMINI_API_KEY fehlt."
    try:
        resp = requests.post(
            GEMINI_ENDPOINT,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as err:
        logger.error("Gemini call failed: %s", err)
        return "[Fehler] Gemini aktuell nicht erreichbar."
    data = resp.json()
    try:
        return str(data["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError):
        return str(data)


def maybe_extract_fact(user_prompt: str) -> Optional[tuple[str, str, float, str]]:
    text = user_prompt.strip()
    if len(text) < 12:
        return None
    for pattern, tag, conf, scope in FACT_PATTERNS:
        if pattern.search(text):
            return (text[:400], tag, conf, scope)
    return None


def maybe_generate_summary(session_id: str, user_id: str, tenant_id: str) -> None:
    if not engine.needs_summary(session_id=session_id, user_id=user_id, tenant_id=tenant_id):
        return
    if not engine.try_acquire_summary_lock(session_id=session_id, user_id=user_id, tenant_id=tenant_id):
        return
    try:
        turns = engine.turns_since_last_summary(session_id=session_id, user_id=user_id, tenant_id=tenant_id, limit=24)
        if len(turns) < 8:
            return
        lines = [f"{r['role']}: {r['content']}" for r in turns]
        summary_prompt = (
            "Fasse die folgenden Dialogturns in 4 Stichpunkten zusammen, neutral und kurz.\n"
            "Falls möglich wichtige stabile Präferenzen benennen.\n\n" + "\n".join(lines)
        )
        summary_text = scrub_pii(call_gemini(summary_prompt))
        engine.create_summary(
            session_id,
            summary_text,
            int(turns[0]["id"]),
            int(turns[-1]["id"]),
            user_id=user_id,
            tenant_id=tenant_id,
        )
        engine.record_metric(session_id, "summary_created", 1, user_id=user_id, tenant_id=tenant_id)
    finally:
        engine.release_summary_lock(session_id=session_id, user_id=user_id, tenant_id=tenant_id)


def extract_facts_with_llm(text: str) -> list[dict]:
    payload = (
        "Extrahiere bis zu 3 wichtige Fakten als JSON-Liste mit Feldern fact, tag, confidence (0..1) und scope (session|user).\n"
        f"Text:\n{text}"
    )
    raw = call_gemini(payload)
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def track_topic_metrics(session_id: str, prompt: str, user_id: str, tenant_id: str) -> None:
    for pattern, tag, _, _ in FACT_PATTERNS:
        if pattern.search(prompt):
            engine.record_metric(session_id, f"topic_{tag}", 1, user_id=user_id, tenant_id=tenant_id)


@app.post("/chat")
def chat() -> Any:
    start = time.perf_counter()
    body = request.get_json(force=True)
    session_id = str(body.get("session_id", "default"))
    user_id = str(body.get("user_id", "anonymous"))
    tenant_id = str(body.get("tenant_id", "default"))
    user_prompt = scrub_pii(str(body.get("prompt", "")).strip())
    if not user_prompt:
        return jsonify({"error": "prompt fehlt"}), 400

    engine.apply_fact_maintenance()
    track_topic_metrics(session_id, user_prompt, user_id, tenant_id)
    turn_id = engine.remember_turn(session_id, "user", user_prompt, user_id=user_id, tenant_id=tenant_id)

    rule_fact = maybe_extract_fact(user_prompt)
    if rule_fact:
        fact_text, tag, confidence, scope = rule_fact
        engine.remember_fact(
            session_id,
            fact_text,
            tag=tag,
            confidence=confidence,
            source_turn_id=turn_id,
            expires_at=int(time.time()) + 30 * 24 * 3600,
            memory_scope=scope,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        engine.record_metric(session_id, "fact_extracted_rule", 1, user_id=user_id, tenant_id=tenant_id)

    llm_facts = extract_facts_with_llm(user_prompt)
    for f in llm_facts[:3]:
        fact_text = scrub_pii(str(f.get("fact", "")).strip())
        if not fact_text:
            continue
        scope = str(f.get("scope", "session"))
        if scope not in {"session", "user", "tenant"}:
            scope = "session"
        try:
            conf = float(f.get("confidence", 0.7))
        except (TypeError, ValueError):
            conf = 0.7
        engine.remember_fact(
            session_id,
            fact_text,
            tag=str(f.get("tag", "llm")),
            confidence=max(0.0, min(1.0, conf)),
            source_turn_id=turn_id,
            expires_at=int(time.time()) + 30 * 24 * 3600,
            memory_scope=scope,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        engine.record_metric(session_id, "fact_extracted_llm", 1, user_id=user_id, tenant_id=tenant_id)

    exact = engine.get_cached(session_id, user_prompt, user_id=user_id, tenant_id=tenant_id)
    if exact:
        engine.remember_turn(session_id, "assistant", exact, user_id=user_id, tenant_id=tenant_id)
        engine.record_metric(session_id, "exact_cache_hit", 1, user_id=user_id, tenant_id=tenant_id)
        engine.record_metric(session_id, "request_latency_ms", (time.perf_counter() - start) * 1000, user_id=user_id, tenant_id=tenant_id)
        return jsonify({"response": exact, "source": "exact_cache", "session_id": session_id, "user_id": user_id, "tenant_id": tenant_id})

    semantic = engine.get_semantic_cached(session_id, user_prompt, embedder=embed_text, user_id=user_id, tenant_id=tenant_id)
    if semantic:
        engine.remember_turn(session_id, "assistant", semantic, user_id=user_id, tenant_id=tenant_id)
        engine.set_cached(session_id, user_prompt, semantic, user_id=user_id, tenant_id=tenant_id)
        engine.record_metric(session_id, "semantic_cache_hit", 1, user_id=user_id, tenant_id=tenant_id)
        engine.record_metric(session_id, "request_latency_ms", (time.perf_counter() - start) * 1000, user_id=user_id, tenant_id=tenant_id)
        return jsonify({"response": semantic, "source": "semantic_cache", "session_id": session_id, "user_id": user_id, "tenant_id": tenant_id})

    maybe_generate_summary(session_id=session_id, user_id=user_id, tenant_id=tenant_id)
    context = engine.build_context(session_id=session_id, query=user_prompt, user_id=user_id, tenant_id=tenant_id)
    answer = scrub_pii(call_gemini(f"{context}\n\nUser: {user_prompt}"))

    engine.remember_turn(session_id, "assistant", answer, user_id=user_id, tenant_id=tenant_id)
    engine.set_cached(session_id, user_prompt, answer, user_id=user_id, tenant_id=tenant_id)
    engine.set_semantic_cached(session_id, user_prompt, answer, embed_text(user_prompt), user_id=user_id, tenant_id=tenant_id)
    engine.record_metric(session_id, "gemini_call", 1, user_id=user_id, tenant_id=tenant_id)
    engine.record_metric(session_id, "cache_miss", 1, user_id=user_id, tenant_id=tenant_id)
    engine.record_metric(session_id, "request_latency_ms", (time.perf_counter() - start) * 1000, user_id=user_id, tenant_id=tenant_id)
    return jsonify({"response": answer, "source": "gemini", "session_id": session_id, "user_id": user_id, "tenant_id": tenant_id})


@app.get("/metrics")
def metrics() -> Any:
    session_id = str(request.args.get("session_id", "default"))
    user_id = str(request.args.get("user_id", "anonymous"))
    tenant_id = str(request.args.get("tenant_id", "default"))
    return jsonify({"session_id": session_id, "user_id": user_id, "tenant_id": tenant_id, "metrics": engine.metrics_summary(session_id, user_id=user_id, tenant_id=tenant_id)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
