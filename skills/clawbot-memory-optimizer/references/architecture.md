# Architektur: zuverlässiges Memory für ClawBot

## Fokus dieser Iteration
1. **Robustheit zuerst**
   - SQLite-Retry bei `database is locked`
   - Logging für DB/HTTP/Embedding-Fehler
   - Validierung von Embedding-Dimensionen
2. **PII-Filter erweitert**
   - E-Mail, Telefon, IBAN, Kreditkarte, SSN werden maskiert
3. **Summary/Fact Qualität**
   - Summary aus Turns seit letztem Summary (ID-basiert)
   - Hybrid Fact-Extraction: Regeln + optional LLM-JSON

## Kontextpipeline
`summary -> top facts -> recent turns`

## Scope
Alle Daten bleiben isoliert über `tenant_id`, `user_id`, `session_id`.
