# ClawBot

Installierbarer Skill für ClawBot mit disk-first Memory, exact/semantic cache, PII-Filter, Summaries, Fact-Extraktion und Multi-Scope-Isolation.

## Gezielt ausgebaut
- Vorhandene Funktionen wurden erweitert (kein Rewrite)
- Race-sicherer Summarizer mit Session-Lock
- Aktives Cross-Session-Recall im Kontextaufbau
- Fact-Merge, Conflict-Markierung und Wartungslogik für Vergessen
- Embedding-Hygiene inklusive Cleanup fehlerhafter Einträge

## API
- `POST /chat` mit `{ "tenant_id":"acme", "user_id":"alice", "session_id":"s1", "prompt":"..." }`
- `GET /metrics?tenant_id=acme&user_id=alice&session_id=s1`
