# ClawBot

Dieses Repo enthält einen installierbaren Skill für ClawBot, um zuverlässiges Kurzzeit- und Langzeitgedächtnis einzubauen und Gemini API-Calls zu reduzieren.

## Skill
Pfad: `skills/clawbot-memory-optimizer`

### Warum der Gemini API Key nicht im Code steht
Der API-Key muss **nur als Environment Variable** gesetzt werden (`GEMINI_API_KEY`) und wird absichtlich nicht hardcoded gespeichert. So vermeidest du Leaks in Git/Logs und kannst Keys einfach rotieren.

### 4 MB RAM-freundliches Design
- **Disk-first Speicher:** Chat-Historie und Facts liegen in SQLite auf Festplatte.
- **STM aus DB:** Kurzzeitkontext wird aus den letzten N Turns gelesen statt im RAM gehalten.
- **Cache in DB:** Prompt-Response-Cache mit TTL liegt ebenfalls auf Festplatte.

### Schnellstart
```bash
cd skills/clawbot-memory-optimizer/scripts
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python memory_engine.py --init-db
python demo.py
export GEMINI_API_KEY="..."
python gemini_memory_proxy.py
```

### Nächste Ausbaustufen
1. Semantischer Cache (Embeddings + Similarity statt nur exact match).
2. PII-Filter vor dem Speichern.
3. Summarizer, der lange Verläufe automatisch verdichtet.
4. Metriken: Cache-Hit-Rate, Tokens, durchschnittliche Antwortzeit.
