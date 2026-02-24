# ClawBot

Installierbarer Skill für ClawBot mit disk-first Memory, exact/semantic cache, PII-Filter, Summaries, Fact-Extraktion und Multi-Scope-Isolation.

## Phase B umgesetzt (Wissensqualität)
- Entity-Extraktion aus neuen Facts (Regel + optional LLM)
- Wissensgraph mit `entities` und `relations` aktiv
- Graph-gestütztes Recall im Context (`Top Entitäten`, `Top Relationen`)

## Für Phase C vorbereitet
- Identitäts-Tabellen (`identity`, `soul`) sind vorhanden

## API
- `POST /chat` mit `{ "tenant_id":"acme", "user_id":"alice", "session_id":"s1", "prompt":"..." }`
- `GET /metrics?tenant_id=acme&user_id=alice&session_id=s1`
