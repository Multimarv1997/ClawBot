---
name: clawbot-memory-optimizer
description: "Installierbarer Skill für ClawBot mit RAM-sparendem SQLite-Memory, exact+semantischem Cache, erweitertem PII-Filter, robuster Fehlerbehandlung, Summarizer, Fact-Extraktion und Multi-Session-Scopes (tenant/user/session)."
---

# ClawBot Memory Optimizer

1. Installiere Abhängigkeiten aus `scripts/requirements.txt`.
2. Initialisiere DB: `python scripts/memory_engine.py --init-db`.
3. Setze `GEMINI_API_KEY` als ENV Variable.
4. Starte Proxy: `python scripts/gemini_memory_proxy.py`.
5. Sende `tenant_id`, `user_id`, `session_id` pro Request in `POST /chat`.
6. Nutze Cache-Reihenfolge: exact -> semantic -> Gemini.
7. Nutze Kontextaufbau: summary -> top facts -> recent turns.
8. Prüfe Metriken mit `GET /metrics?...`.
9. Plane Cleanup: `python scripts/memory_engine.py --purge-cache`.
10. Nutze Logging/Retry für robusten Betrieb bei transienten DB/API-Fehlern.
11. Nutze `memory_scope` in Facts (`session|user|tenant`) für Cross-Session-Erinnerung pro Nutzer.

12. Nutze den race-sicheren Summarizer-Lock (`summary_locks`) bei parallelen Requests.
13. Nutze aktive Cross-Session-Context-Blöcke (letzte Session + Tenant-Trends) für besseres Recall.
