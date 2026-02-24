# ClawBot

Installierbarer Skill für ClawBot mit disk-first Memory, exact/semantic cache, PII-Filter, Summaries, Fact-Extraktion und Multi-Scope-Isolation.

## Phase A umgesetzt (ROI hoch)
- Decay-basiertes Vergessen mit Relevanz-Score und Status
- Natural Language Triggers (`remember`, `forget`, `reflect`)
- Trigger-/Topic-Metriken zur Beobachtung

## Für Phase B/C vorbereitet
- Datenbankschema für Wissensgraph/Identity ist angelegt (`entities`, `relations`, `identity`, `soul`)

## API
- `POST /chat` mit `{ "tenant_id":"acme", "user_id":"alice", "session_id":"s1", "prompt":"..." }`
- `GET /metrics?tenant_id=acme&user_id=alice&session_id=s1`
