# Architektur: zuverlässiges Memory für ClawBot

## Phase 2
- Summaries in `summaries` bei Trigger (z. B. viele Turns).
- Fact-Extraktion (Regel + LLM-Hybrid), Speicherung mit `confidence`, `source_turn_id`, `expires_at`.
- Kontextpipeline: `summary -> top facts -> recent turns`.

## Phase 3
- Scope überall: `tenant_id`, `user_id`, `session_id`.
- Indizes auf Scope-Felder für Performance.
- Retention möglich über TTL (`expires_at`) und periodischen Purge.

## Kern-Tabellen
- `interactions(tenant_id, user_id, session_id, role, content, created_at)`
- `facts(..., confidence, source_turn_id, expires_at)`
- `summaries(session_id, user_id, tenant_id, summary, from_turn_id, to_turn_id)`
- `exact_cache_entries(tenant_id, user_id, session_id, prompt_hash, ...)`
- `semantic_cache_entries(tenant_id, user_id, session_id, prompt, embedding_json, ...)`
- `metrics(tenant_id, user_id, session_id, metric_name, value, created_at)`
