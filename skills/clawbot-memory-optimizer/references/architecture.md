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


## Phase D1 (umgesetzt)
1. **Foundation-Schema erweitert**
   - `reflection_queue`, `reflection_log`, `agents`, `memory_proposals`, `audit_log` inkl. Indizes
2. **Governance-Basis**
   - Reflection-Request-Basics und Pending-Abfrage
   - Audit-Logging + Retention/Cleanup-Helfer
3. **Vorbereitung für D2/D3/D4**
   - Status-Modelle (`pending/approved/rejected/executed`) sind vorbereitet
   - API-/Workflow-Layer folgt in nächsten Phasen


## Phase D2 (umgesetzt)
1. **Reflection Queue MVP aktiv**
   - Trigger-Erkennung (`explicit|soft|scheduled`)
   - Lifecycle: `pending -> approved/rejected -> executed`
2. **Reflection API verfügbar**
   - request/approve/reject/execute/pending/history
3. **Vorbereitung für D3/D4**
   - Proposal-Tabellen und Audit-Basis aus D1 bleiben unverändert und bereit


## Phase D3 (umgesetzt)
1. **Multi-Agent Proposal-Workflow aktiv**
   - Agenten-Registrierung (`main`/`subagent`) mit Permission-Gates
   - Proposals: submit/list/review (approve/reject)
2. **Ausführungslogik bei Approve**
   - Target Stores: `facts`, `entities`, `relations`, `identity`, `soul`
   - Main-Agent (`write`) als Reviewer erforderlich
3. **Vorbereitung für D4**
   - Audit- und Retention-Basis bleibt intakt
   - Erweiterte Governance/Analytics können darauf aufbauen
