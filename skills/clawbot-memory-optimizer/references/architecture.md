# Architektur: zuverlässiges Memory für ClawBot

## Phase A (umgesetzt)
1. **Decay-basiertes Vergessen**
   - `relevance_score` + `memory_status` (`active|fading|dormant|archived`)
   - Decay-Logik bei Fact-Zugriff und in Wartungsjobs
2. **Natural Language Triggers**
   - Trigger für `remember`, `forget`, `reflect` im Proxy-Flow
   - Trigger-Metriken zur Auswertung
3. **Stabilität und Hygiene beibehalten**
   - Summary-Locks, Embedding-Cleanup, PII-Schutz bleiben aktiv

## Für Phase B/C vorbereitet
- Tabellen vorbereitet: `entities`, `relations`, `identity`, `soul`
- Fact-Schema vorbereitet mit `memory_type`, `relevance_score`, `memory_status`

## Kontextpipeline
`summary -> top facts -> previous-session facts -> tenant trends -> recent turns`
