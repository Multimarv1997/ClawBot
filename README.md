# ClawBot

Installierbarer Skill für ClawBot mit disk-first Memory, exact/semantic cache, PII-Filter, Summaries, Fact-Extraktion und Multi-Scope-Isolation.

## Phase B umgesetzt (Wissensqualität)
- Entity-Extraktion aus neuen Facts (Regel + optional LLM)
- Wissensgraph mit `entities` und `relations` aktiv
- Graph-gestütztes Recall im Context (`Top Entitäten`, `Top Relationen`)

## Phase C umgesetzt (Identität & Konsistenz)
- `identity`/`soul` werden aktiv gepflegt (Regeln für stabile Fakten vs. dynamisches Self-Image)
- Kontext enthält zusätzliche Blöcke: `Identity (stabil)` und `Soul (Werte/Prinzipien)`
- Striktes Tokenbudget pro Kontextblock + globales Budget

## API
- `POST /chat` mit `{ "tenant_id":"acme", "user_id":"alice", "session_id":"s1", "prompt":"..." }`
- `GET /metrics?tenant_id=acme&user_id=alice&session_id=s1`


## Phase D1 umgesetzt (Foundation)
- Neue Tabellen/Indizes: `reflection_queue`, `reflection_log`, `agents`, `memory_proposals`, `audit_log`
- Basis-Methoden für Queue/Audit: `request_reflection`, `get_pending_reflection`, `get_audit_log`, Cleanup-Helfer
- D2/D3/D4 vorbereitet durch Status- und Governance-Struktur (noch ohne API-Workflow)


## Phase D2 umgesetzt (Reflection Queue MVP)
- Reflection-Trigger-Handler (explicit/soft/scheduled) mit Queue-Anlage
- Reflection Approval-Workflow: `approve`, `reject`, `execute`, `history`
- API-Endpunkte: `/api/reflection/request|approve|reject|execute|pending|history`
- D3/D4 vorbereitet: Proposal/Audit-Fundament aus D1 bleibt intakt


## Phase D3 umgesetzt (Multi-Agent Proposal Workflow)
- Agent-Registry mit Rollen/Rechten (`read|propose|write`, inkl. Main-Agent-Review-Regel)
- Proposal-Lifecycle: `submit -> pending -> approve/reject` mit direkter Ausführung bei Approve
- Proposal-API: `/api/agent/register`, `/api/proposal/submit`, `/api/proposal/pending`, `/api/proposal/review`
- Für D4 vorbereitet: Audit-/Retention-Basis und Governance-Statusfelder sind vorhanden
