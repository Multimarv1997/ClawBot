# ClawBot

Installierbarer Skill für ClawBot mit disk-first Memory, exact/semantic cache, PII-Filter, Summaries, Fact-Extraktion und Multi-Scope-Isolation.

## Fokus jetzt umgesetzt
- Robustheit (DB-Retry, Logging, robustere Fehlerpfade)
- Erweiterter PII-Filter
- Verbesserte Summary/Fact-Qualität

## API
- `POST /chat` mit `{ "tenant_id":"acme", "user_id":"alice", "session_id":"s1", "prompt":"..." }`
- `GET /metrics?tenant_id=acme&user_id=alice&session_id=s1`
