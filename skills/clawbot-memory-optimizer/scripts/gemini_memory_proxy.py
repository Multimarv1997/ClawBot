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


ENTITY_PATTERNS = [
    (re.compile(r"\b(projekt|project)\s+([A-Za-z0-9_-]{2,})", re.IGNORECASE), "project"),
    (re.compile(r"\b(kunde|client)\s+([A-Za-z0-9_-]{2,})", re.IGNORECASE), "client"),
    (re.compile(r"\b(thema|topic)\s+([A-Za-z0-9_-]{2,})", re.IGNORECASE), "topic"),
]
ENABLE_LLM_ENTITY_EXTRACTION = os.getenv("ENABLE_LLM_ENTITY_EXTRACTION", "0") == "1"


REMEMBER_TRIGGERS = ["remember", "don't forget", "keep in mind", "note that", "save this", "merke dir", "vergiss nicht", "wichtig"]
FORGET_TRIGGERS = ["forget", "never mind", "disregard", "remove from memory", "vergiss", "streichen", "egal"]
REFLECT_TRIGGERS = ["reflect", "review memory", "consolidate", "reflektiere", "gedächtnis prüfen"]

REFLECTION_EXPLICIT_TRIGGERS = [
    "reflect", "let's reflect", "reflektiere", "consolidate memories", "überprüfe mein gedächtnis", "self-review", "selbstreflexion"
]
REFLECTION_SOFT_TRIGGERS = [
    "going to sleep", "logging off", "shutting down", "mache mal pause", "bis später", "schlafe jetzt"
]
REFLECTION_SCHEDULED_TRIGGERS = [
    "daily reflection", "weekly review", "nightly reflection"
]

SOUL_PATTERNS = [
    (re.compile(r"\b(wert|werte|value|values)\b", re.IGNORECASE), "values"),
    (re.compile(r"\b(prinzip|prinzipien|principle|principles)\b", re.IGNORECASE), "principles"),
    (re.compile(r"\b(grenze|grenzen|boundary|boundaries)\b", re.IGNORECASE), "boundaries"),
]


def _has_any(text: str, patterns: list[str]) -> bool:
    t = text.lower()
    return any(p in t for p in patterns)

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




def extract_entities_rule(text: str) -> list[dict]:
    out: list[dict] = []
    for pattern, etype in ENTITY_PATTERNS:
        for m in pattern.finditer(text):
            name = m.group(2).strip()
            if len(name) >= 2:
                out.append({"name": name, "type": etype, "confidence": 0.7})
    return out


def extract_entities_with_llm(text: str) -> list[dict]:
    if not ENABLE_LLM_ENTITY_EXTRACTION:
        return []
    prompt = (
        "Extrahiere bis zu 5 Entitäten als JSON-Liste mit Feldern name, type, confidence (0..1).\n"
        f"Text:\n{text}"
    )
    raw = call_gemini(prompt)
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def update_knowledge_graph_from_fact(fact_text: str, tenant_id: str, user_id: str) -> None:
    entities = extract_entities_rule(fact_text)
    entities.extend(extract_entities_with_llm(fact_text))
    dedup = {}
    for e in entities:
        name = str(e.get("name", "")).strip()
        if not name:
            continue
        dedup[name.lower()] = {
            "name": name,
            "type": str(e.get("type", "concept")),
            "confidence": float(e.get("confidence", 0.7)),
        }
    ids: list[int] = []
    for ent in dedup.values():
        ids.append(engine.upsert_entity(tenant_id=tenant_id, user_id=user_id, entity_name=ent["name"], entity_type=ent["type"], confidence=max(0.0, min(1.0, ent["confidence"]))))
    for i in range(len(ids) - 1):
        engine.upsert_relation(tenant_id=tenant_id, user_id=user_id, source_entity_id=ids[i], target_entity_id=ids[i + 1], relation_type="co_mentioned", strength=0.6)


def apply_identity_and_soul_rules(fact_text: str, tag: str, tenant_id: str) -> None:
    text = fact_text.strip()
    if not text:
        return

    # Stabile Fakten (langfristig konsistent)
    if tag in {"identity", "preference"} or re.search(r"\b(mein name ist|ich heiße|my name is)\b", text, re.IGNORECASE):
        engine.set_identity(tenant_id=tenant_id, category="facts", content=text[:280], stability="stable")

    # Dynamisches Self-Image (stil-/zustandsnah, veränderbar)
    if tag in {"style", "llm"} or re.search(r"\b(antworte|ton|stil|style|today|heute|aktuell)\b", text, re.IGNORECASE):
        engine.set_identity(tenant_id=tenant_id, category="self_image", content=text[:280], stability="dynamic")

    for pattern, category in SOUL_PATTERNS:
        if pattern.search(text):
            engine.set_soul(tenant_id=tenant_id, category=category, content=text[:280])



class ReflectionTriggerHandler:
    def __init__(self, memory_engine: MemoryEngine):
        self.engine = memory_engine

    def detect_trigger(self, user_input: str) -> Optional[str]:
        t = user_input.lower()
        if any(x in t for x in REFLECTION_EXPLICIT_TRIGGERS):
            return "explicit"
        if any(x in t for x in REFLECTION_SOFT_TRIGGERS):
            return "soft"
        if any(x in t for x in REFLECTION_SCHEDULED_TRIGGERS):
            return "scheduled"
        return None

    def handle(self, user_input: str, tenant_id: str, user_id: str) -> Optional[dict]:
        trigger = self.detect_trigger(user_input)
        if not trigger:
            return None
        if trigger == "soft":
            return {"action": "prompt_approval", "trigger_type": "soft", "message": "Soll ich vor dem Beenden eine kurze Reflexion anfordern?"}
        rid = self.engine.request_reflection(
            tenant_id=tenant_id,
            user_id=user_id,
            trigger_type=trigger,
            token_reason=f"{trigger} trigger",
        )
        return {
            "action": "request_approval",
            "trigger_type": trigger,
            "request_id": rid,
            "message": "Reflexion angefragt. Bitte genehmigen oder ablehnen.",
        }


reflection_handler = ReflectionTriggerHandler(engine)

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

    # D4 hardening: periodic cleanup hooks
    engine.cleanup_stale_phase_d_state()

    if _has_any(user_prompt, FORGET_TRIGGERS):
        deleted = engine.forget_facts(session_id=session_id, pattern=user_prompt, user_id=user_id, tenant_id=tenant_id)
        engine.record_metric(session_id, "trigger_forget_hit", 1, user_id=user_id, tenant_id=tenant_id)
        return jsonify({"response": f"Ich habe {deleted} passende Erinnerungen entfernt.", "source": "trigger_forget", "session_id": session_id, "user_id": user_id, "tenant_id": tenant_id})

    reflection_signal = reflection_handler.handle(user_prompt, tenant_id=tenant_id, user_id=user_id)
    if reflection_signal:
        engine.record_metric(session_id, "trigger_reflect_hit", 1, user_id=user_id, tenant_id=tenant_id)
        return jsonify({
            "response": reflection_signal["message"],
            "source": "trigger_reflect",
            "session_id": session_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "reflection": reflection_signal,
        })

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
        update_knowledge_graph_from_fact(fact_text, tenant_id=tenant_id, user_id=user_id)
        apply_identity_and_soul_rules(fact_text, tag=tag, tenant_id=tenant_id)
        if _has_any(user_prompt, REMEMBER_TRIGGERS):
            engine.record_metric(session_id, "trigger_remember_hit", 1, user_id=user_id, tenant_id=tenant_id)

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
        update_knowledge_graph_from_fact(fact_text, tenant_id=tenant_id, user_id=user_id)
        apply_identity_and_soul_rules(fact_text, tag=str(f.get("tag", "llm")), tenant_id=tenant_id)

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
    include_governance = bool(body.get("include_governance_context", False))
    context = engine.build_context(session_id=session_id, query=user_prompt, user_id=user_id, tenant_id=tenant_id, include_reflection_queue=include_governance)
    answer = scrub_pii(call_gemini(f"{context}\n\nUser: {user_prompt}"))

    engine.remember_turn(session_id, "assistant", answer, user_id=user_id, tenant_id=tenant_id)
    engine.set_cached(session_id, user_prompt, answer, user_id=user_id, tenant_id=tenant_id)
    engine.set_semantic_cached(session_id, user_prompt, answer, embed_text(user_prompt), user_id=user_id, tenant_id=tenant_id)
    engine.record_metric(session_id, "gemini_call", 1, user_id=user_id, tenant_id=tenant_id)
    engine.record_metric(session_id, "cache_miss", 1, user_id=user_id, tenant_id=tenant_id)
    engine.record_metric(session_id, "request_latency_ms", (time.perf_counter() - start) * 1000, user_id=user_id, tenant_id=tenant_id)
    return jsonify({"response": answer, "source": "gemini", "session_id": session_id, "user_id": user_id, "tenant_id": tenant_id})


@app.post("/api/reflection/request")
def api_reflection_request() -> Any:
    body = request.get_json(force=True)
    tenant_id = str(body.get("tenant_id", "default"))
    user_id = str(body.get("user_id", "anonymous"))
    trigger_type = str(body.get("trigger_type", "explicit"))
    token_reason = str(body.get("token_reason", ""))
    priority = int(body.get("priority", 1))
    rid = engine.request_reflection(tenant_id=tenant_id, user_id=user_id, trigger_type=trigger_type, token_reason=token_reason, priority=priority)
    return jsonify({"status": "pending", "request_id": rid})


@app.post("/api/reflection/approve")
def api_reflection_approve() -> Any:
    body = request.get_json(force=True)
    ok = engine.approve_reflection(
        reflection_id=int(body.get("reflection_id", 0)),
        tenant_id=str(body.get("tenant_id", "default")),
        user_id=str(body.get("user_id", "anonymous")),
        approved_tokens=body.get("approved_tokens"),
        custom_elements=body.get("custom_elements"),
    )
    return (jsonify({"status": "approved"}), 200) if ok else (jsonify({"error": "approve failed"}), 400)


@app.post("/api/reflection/reject")
def api_reflection_reject() -> Any:
    body = request.get_json(force=True)
    ok = engine.reject_reflection(
        reflection_id=int(body.get("reflection_id", 0)),
        tenant_id=str(body.get("tenant_id", "default")),
        user_id=str(body.get("user_id", "anonymous")),
        rejection_reason=str(body.get("rejection_reason", "")),
    )
    return (jsonify({"status": "rejected"}), 200) if ok else (jsonify({"error": "reject failed"}), 400)


@app.post("/api/reflection/execute")
def api_reflection_execute() -> Any:
    body = request.get_json(force=True)
    ok = engine.execute_reflection(
        reflection_id=int(body.get("reflection_id", 0)),
        tenant_id=str(body.get("tenant_id", "default")),
        user_id=str(body.get("user_id", "anonymous")),
        reflection_text=str(body.get("reflection_text", "")),
    )
    return (jsonify({"status": "executed"}), 200) if ok else (jsonify({"error": "execute failed"}), 400)


@app.get("/api/reflection/pending")
def api_reflection_pending() -> Any:
    tenant_id = str(request.args.get("tenant_id", "default"))
    user_id = str(request.args.get("user_id", "anonymous"))
    item = engine.get_pending_reflection(tenant_id=tenant_id, user_id=user_id)
    return jsonify({"pending": item})


@app.get("/api/reflection/history")
def api_reflection_history() -> Any:
    tenant_id = str(request.args.get("tenant_id", "default"))
    user_id = str(request.args.get("user_id", "anonymous"))
    limit = int(request.args.get("limit", "10"))
    return jsonify({"items": engine.get_reflection_history(tenant_id=tenant_id, user_id=user_id, limit=limit)})


@app.post("/api/agent/register")
def api_agent_register() -> Any:
    body = request.get_json(force=True)
    aid = engine.register_agent(
        tenant_id=str(body.get("tenant_id", "default")),
        agent_name=str(body.get("agent_name", "")),
        agent_type=str(body.get("agent_type", "subagent")),
        permissions=str(body.get("permissions", "read")),
    )
    return jsonify({"agent_id": aid})


@app.post("/api/proposal/submit")
def api_proposal_submit() -> Any:
    body = request.get_json(force=True)
    if not str(body.get("agent_name", "")).strip():
        return jsonify({"error": "agent_name fehlt"}), 400
    if not str(body.get("content", "")).strip():
        return jsonify({"error": "content fehlt"}), 400
    pid = engine.submit_proposal(
        tenant_id=str(body.get("tenant_id", "default")),
        agent_name=str(body.get("agent_name", "")),
        target_store=str(body.get("target_store", "facts")),
        proposal_type=str(body.get("proposal_type", "add")),
        content=str(body.get("content", "")),
        confidence=str(body.get("confidence", "medium")),
        priority=int(body.get("priority", 1)),
    )
    if pid is None:
        return jsonify({"error": "not authorized or too many pending proposals"}), 403
    return jsonify({"proposal_id": pid, "status": "pending"})


@app.get("/api/proposal/pending")
def api_proposal_pending() -> Any:
    tenant_id = str(request.args.get("tenant_id", "default"))
    target_store = request.args.get("target_store")
    agent_name = request.args.get("agent_name")
    limit = int(request.args.get("limit", "20"))
    items = engine.get_pending_proposals(tenant_id=tenant_id, target_store=target_store, agent_name=agent_name, limit=limit)
    return jsonify({"items": items})


@app.post("/api/proposal/review")
def api_proposal_review() -> Any:
    body = request.get_json(force=True)
    action = str(body.get("action", "reject"))
    if action == "approve":
        ok = engine.approve_proposal(
            proposal_id=int(body.get("proposal_id", 0)),
            tenant_id=str(body.get("tenant_id", "default")),
            reviewer_agent=str(body.get("reviewer_agent", "")),
            review_comment=str(body.get("comment", "")),
        )
    else:
        ok = engine.reject_proposal(
            proposal_id=int(body.get("proposal_id", 0)),
            tenant_id=str(body.get("tenant_id", "default")),
            reviewer_agent=str(body.get("reviewer_agent", "")),
            rejection_reason=str(body.get("comment", "")),
        )
    return (jsonify({"status": "success"}), 200) if ok else (jsonify({"error": "review failed"}), 400)


@app.get("/api/audit")
def api_audit() -> Any:
    tenant_id = str(request.args.get("tenant_id", "default"))
    action_type = request.args.get("action_type")
    target_store = request.args.get("target_store")
    limit = int(request.args.get("limit", "100"))
    return jsonify({"items": engine.get_audit_log(tenant_id=tenant_id, action_type=action_type, target_store=target_store, limit=limit)})


@app.post("/api/admin/maintenance")
def api_admin_maintenance() -> Any:
    result = engine.cleanup_stale_phase_d_state()
    return jsonify({"status": "ok", "maintenance": result})


@app.get("/api/health/phase-d")
def api_phase_d_health() -> Any:
    tenant_id = str(request.args.get("tenant_id", "default"))
    user_id = str(request.args.get("user_id", "anonymous"))
    return jsonify(engine.phase_d_health(tenant_id=tenant_id, user_id=user_id))


@app.get("/metrics")
def metrics() -> Any:
    session_id = str(request.args.get("session_id", "default"))
    user_id = str(request.args.get("user_id", "anonymous"))
    tenant_id = str(request.args.get("tenant_id", "default"))
    return jsonify({"session_id": session_id, "user_id": user_id, "tenant_id": tenant_id, "metrics": engine.metrics_summary(session_id, user_id=user_id, tenant_id=tenant_id)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
