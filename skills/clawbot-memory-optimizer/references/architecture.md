# Architektur: zuverlässiges Memory für ClawBot

## Phase B (umgesetzt)
1. **Wissensgraph erweitert**
   - Tabellen `entities`, `relations` aktiv genutzt
   - Entity-Extraktion aus Facts: Regel-basiert + optional LLM (`ENABLE_LLM_ENTITY_EXTRACTION=1`)
2. **Graph-Recall im Kontext**
   - `build_context()` enthält Top-Entitäten und Top-Relationen (top-k)
3. **Vorhandene Basis beibehalten**
   - Decay/Trigger/PII/Locks aus Phase A bleiben unverändert aktiv

## Phase C (umgesetzt)
1. **Identität & Konsistenz aktiviert**
   - `identity`: stabile Fakten vs. dynamisches Self-Image
   - `soul`: Werte/Prinzipien/Boundaries
2. **Änderungsregeln**
   - Stabile Identitätsfakten werden konservativ fortgeführt (`stability=stable`)
   - Stil-/Zustandsnahe Aussagen landen als dynamisches Self-Image (`stability=dynamic`)
3. **Striktes Tokenbudget**
   - pro Kontextblock (Identity, Soul, Summary, Facts, Graph, Turns)
   - globales Budget als harte Obergrenze

## Kontextpipeline
`identity -> soul -> summary -> top facts -> previous-session facts -> tenant trends -> graph entities/relations -> recent turns`
