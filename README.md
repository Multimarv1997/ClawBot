# ClawBot

Installierbarer Skill für ClawBot mit disk-first Memory, exact/semantic cache, PII-Filter, Summaries, Fact-Extraktion und Multi-Scope-Isolation.

## Gezielt ausgebaut
- Vorhandene Robustheit/PII/Summary-Teile wurden erweitert (nicht neu erfunden)
- Cross-Session-Memory über Fact-Scopes (`session|user|tenant`)
- Intelligentes Fact-Merging über `fact_key`
- Erweiterte Analytics mit Topic-Metriken

## API
- `POST /chat` mit `{ "tenant_id":"acme", "user_id":"alice", "session_id":"s1", "prompt":"..." }`
- `GET /metrics?tenant_id=acme&user_id=alice&session_id=s1`
