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

14. Aktiviere Trigger-Flow: `remember`, `forget`, `reflect` für automatische Memory-Aktionen.
15. Nutze Decay-Model (`relevance_score`, `memory_status`) für priorisiertes Gedächtnis.
16. Halte Phase-B/C-Tabellen (`entities`, `relations`, `identity`, `soul`) für spätere Ausbaustufe bereit.

17. Aktiviere Phase B: Entity-Extraktion (Regel + optional LLM) und Graph-Recall über `entities`/`relations`.
18. Nutze Phase C aktiv: `identity` (stabil + dynamisch) und `soul` für Werte/Prinzipien im Kontext.
19. Erzwinge striktes Kontextbudget mit globalem Token-Limit plus Block-Budgets pro Speicherquelle.

20. Starte Phase D1 nur als Foundation: neue Queue/Proposal/Audit-Tabellen + Basis-Methoden.
21. Verschiebe Trigger-Handler/Approval-Endpunkte bewusst in D2/D3/D4.

22. Setze D2 um: Reflection Queue MVP (Trigger + approve/reject/execute + history + Reflection-API).
23. Belasse Multi-Agent-Proposals (D3) und erweiterte Governance/Analytics (D4) weiterhin als nächste Schritte.

24. Setze D3 um: Multi-Agent Proposal-Workflow (Registry, Submit, Review/Execute).
25. Halte D4 für Governance-Feinschliff, Analytics und Retention-Policies vorbereitet.
