# Architektur: zuverlässiges Memory für ClawBot

## Phase B (umgesetzt)
1. **Wissensgraph erweitert**
   - Tabellen `entities`, `relations` aktiv genutzt
   - Entity-Extraktion aus Facts: Regel-basiert + optional LLM (`ENABLE_LLM_ENTITY_EXTRACTION=1`)
2. **Graph-Recall im Kontext**
   - `build_context()` enthält Top-Entitäten und Top-Relationen (top-k)
3. **Vorhandene Basis beibehalten**
   - Decay/Trigger/PII/Locks aus Phase A bleiben unverändert aktiv

## Für Phase C vorbereitet
- Tabellen `identity`, `soul` sind vorhanden
- Fact- und Metrics-Basis erlaubt Identity-Injektion in den Kontext

## Kontextpipeline
`summary -> top facts -> previous-session facts -> tenant trends -> graph entities/relations -> recent turns`
