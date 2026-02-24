# Architektur: zuverlässiges Memory für ClawBot

## Fokus dieser Iteration
1. **Gezielter Ausbau statt Rewrite**
   - bestehende Robustheit/PII/Summary-Funktionen beibehalten und präzise erweitert
2. **Race-sicherer Summarizer**
   - Summary-Lock pro `(tenant,user,session)` verhindert doppelte Summaries bei parallelen Requests
3. **Aktives Cross-Session-Recall**
   - Kontext enthält zusätzlich Facts aus letzter Session und Tenant-Topic-Trends
4. **Intelligente Fact-Verwaltung**
   - Merge per `fact_key`, Konfliktmarkierung (`conflict_group`), Alterung/Pruning über Wartungslogik
5. **Embedding-Hygiene**
   - fehlerhafte/inkonsistente Embedding-Records werden erkannt und bereinigt

## Kontextpipeline
`summary -> top facts -> previous-session facts -> tenant trends -> recent turns`

## Scope
Alle Daten bleiben isoliert über `tenant_id`, `user_id`, `session_id`; Facts können zusätzlich scope-übergreifend gespeichert werden (`memory_scope`).
