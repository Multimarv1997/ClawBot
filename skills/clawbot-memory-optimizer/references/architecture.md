# Architektur: zuverlässiges Memory für ClawBot

## Fokus dieser Iteration
1. **Gezielter Ausbau statt Rewrite**
   - vorhandene Robustheit/PII/Summary-Funktionen beibehalten
   - Cross-Session-Memory über `memory_scope` (`session|user|tenant`) ergänzt
2. **Intelligente Fact-Verwaltung**
   - Facts werden per `fact_key` zusammengeführt (Merge statt Duplikate)
   - Scores über `priority`, `confidence`, `hit_count`, `updated_at`
3. **Summary/Fact Qualität**
   - Summary aus Turns seit letztem Summary (ID-basiert)
   - Hybrid Fact-Extraction: Regeln + optional LLM-JSON inkl. scope
4. **Analytics erweitert**
   - Topic-Metriken (`topic_*`) zusätzlich zu Cache/Latency

## Kontextpipeline
`summary -> top facts -> recent turns`

## Scope
Alle Daten bleiben isoliert über `tenant_id`, `user_id`, `session_id`; Facts können zusätzlich scope-übergreifend gespeichert werden (`memory_scope`).
