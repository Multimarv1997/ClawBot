# ClawBot

Dieses Repo enthält einen installierbaren Skill für ClawBot, um zuverlässiges Kurzzeit- und Langzeitgedächtnis einzubauen und Gemini API-Calls zu reduzieren.

## Skill
Pfad: `skills/clawbot-memory-optimizer`

## Phase 2 + Phase 3 umgesetzt
- **Summarizer:** automatische Zusammenfassung bei vielen Turns in Tabelle `summaries`.
- **Fact-Extraktion:** regelbasiert + LLM-ready Hybrid, Facts mit `confidence`, `source_turn_id`, `expires_at`.
- **Kontextreihenfolge:** `summary -> top facts -> recent turns`.
- **Multi-Session-Management:** Scope jetzt über `tenant_id`, `user_id`, `session_id` auf Kern-Tabellen.

## API
- `POST /chat` mit JSON: `{ "tenant_id":"acme", "user_id":"alice", "session_id":"s1", "prompt":"..." }`
- `GET /metrics?tenant_id=acme&user_id=alice&session_id=s1`
