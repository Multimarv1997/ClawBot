# ClawBot Memory Optimizer

Installierbarer Memory-Skill für ClawBot mit SQLite-gestütztem Langzeitgedächtnis, mehrstufigem Cache, Triggern (`remember`/`forget`/`reflect`) und Governance-Workflows für Reflection/Proposals.

## Zielbild

Der Skill erweitert ClawBot um einen **disk-first Memory-Layer**:
- **Session-Kontext** für aktuelle Unterhaltung.
- **User-/Tenant-Kontext** für wiederkehrendes Wissen über Sessions hinweg.
- **Robuste Laufzeit** durch Caches, Locks, Retry-Handling und Wartungsendpunkte.
- **Governance** durch Reflection Queue, Proposal-Review und Audit-Log.

---

## Architektur (technisch)

### Komponenten

1. **`memory_engine.py`**
   - SQLite-Datenmodell (Interaktionen, Facts, Summaries, Graph, Reflection, Proposals, Audit).
   - Kontextaufbau und Retrieval-Logik.
   - Cache-Layer (exact + semantic) und Memory-Maintenance.

2. **`gemini_memory_proxy.py`**
   - HTTP-API (`/chat` + Admin-/Governance-Endpunkte).
   - Aufbereitung von Prompt, Triggern, PII-Scrubbing, LLM-Aufrufen.
   - Orchestrierung: Cache → Kontext → Gemini → Persistenz.

3. **`demo.py`**
   - End-to-End-Demo für zentrale Flows (Memory, Reflection, Proposal).

### Datenfluss pro Chat-Request

`POST /chat` läuft vereinfacht so:

1. Input validieren (`tenant_id`, `user_id`, `session_id`, `prompt`).
2. PII im Prompt maskieren.
3. Trigger prüfen (`remember`, `forget`, `reflect`).
4. Caches prüfen:
   - exact cache hit → direkte Antwort
   - semantic cache hit → semantisch ähnliche Antwort
5. Bei Cache-Miss:
   - Kontext bauen (Summary, Facts, Graph, Identity/Soul, Turns, optional Governance)
   - Gemini aufrufen
   - Antwort persistieren + Caches aktualisieren
6. Maintenance/Telemetry aktualisieren.

---

## Phasen & Feature-Umfang

### Phase B (Wissensqualität)
- Entity-Extraktion (Regeln + optional LLM).
- Wissensgraph mit `entities` und `relations`.
- Graph-Blöcke im Kontext.

### Phase C (Identität & Konsistenz)
- `identity` und `soul` als eigene Speicherbereiche.
- Stabiles + dynamisches Selbstbild im Kontext.
- Blockbudgets + globales Kontextbudget.

### Phase D1–D4 (Governance & Hardening)
- Reflection Queue (`request`, `approve`, `reject`, `execute`, `history`).
- Agent Registry + Proposal Lifecycle (`submit`, `pending`, `review`).
- Audit-Log, Health-Endpoint, Admin-Maintenance.

---

## Installation in ClawBot

## A) Direkt aus Repository (Entwicklung/Lokal)

1. **Repository bereitstellen** (dieses Repo in ClawBot-Umgebung verfügbar machen).
2. **Python-Abhängigkeiten installieren**:

```bash
pip install -r skills/clawbot-memory-optimizer/scripts/requirements.txt
```

3. **Umgebungsvariablen setzen** (mindestens):

```bash
export GEMINI_API_KEY="<dein_key>"
# optional
export GEMINI_MODEL="gemini-1.5-flash"
export GEMINI_EMBED_MODEL="text-embedding-004"
```

4. **DB initialisieren**:

```bash
python skills/clawbot-memory-optimizer/scripts/memory_engine.py --init-db
```

5. **Proxy starten**:

```bash
python skills/clawbot-memory-optimizer/scripts/gemini_memory_proxy.py
```

6. **ClawBot auf Proxy routen** (HTTP-Integration/Tooling je nach Setup), dann `/chat` nutzen.

## B) Als Skill-ZIP in ClawBot einspielen

Wenn ihr im ClawBot-Setup Skill-ZIPs verteilt:

1. ZIP bauen:

```bash
zip -r dist/clawbot-memory-optimizer.zip skills/clawbot-memory-optimizer -x '*/__pycache__/*' '*.pyc'
```

2. ZIP in eure ClawBot-Skill-Import-Strecke laden.
3. Danach Schritte „Abhängigkeiten“, „ENV“, „DB init“, „Proxy starten“ wie oben durchführen.

---

## API-Quickstart

### Chat

```http
POST /chat
Content-Type: application/json

{
  "tenant_id": "acme",
  "user_id": "alice",
  "session_id": "s1",
  "prompt": "Bitte antworte kurz auf Deutsch"
}
```

### Metriken

```http
GET /metrics?tenant_id=acme&user_id=alice&session_id=s1
```

### Reflection (Beispiele)
- `POST /api/reflection/request`
- `POST /api/reflection/approve`
- `POST /api/reflection/reject`
- `POST /api/reflection/execute`
- `GET /api/reflection/pending`
- `GET /api/reflection/history`

### Proposals / Agents
- `POST /api/agent/register`
- `POST /api/proposal/submit`
- `GET /api/proposal/pending`
- `POST /api/proposal/review`

### Governance / Betrieb
- `GET /api/audit`
- `POST /api/admin/maintenance`
- `GET /api/health/phase-d`

---

## Betriebs-Hinweise (wichtig)

- **Scope-Isolation:** Immer `tenant_id`, `user_id`, `session_id` sauber setzen.
- **Concurrency:** SQLite wird per Locking abgesichert; für Lasttests weiterhin Monitoring aktiv halten.
- **PII-Scrubbing:** Vor LLM-Aufruf wird maskiert; Pattern regelmäßig gegen eure Daten prüfen.
- **Budgeting:** Kontextblöcke sind token-budgetiert, um harte Prompt-Explosion zu verhindern.
- **Wartung:** Regelmäßig `/api/admin/maintenance` oder CLI-Cleanup-Tasks ausführen.

---

## Lokale Verifikation

```bash
python -m py_compile skills/clawbot-memory-optimizer/scripts/memory_engine.py \
  skills/clawbot-memory-optimizer/scripts/gemini_memory_proxy.py \
  skills/clawbot-memory-optimizer/scripts/demo.py

python skills/clawbot-memory-optimizer/scripts/memory_engine.py --init-db
python skills/clawbot-memory-optimizer/scripts/demo.py
```

Damit prüft ihr DB-Schema, Kernlogik und den integrierten Demo-Flow.
