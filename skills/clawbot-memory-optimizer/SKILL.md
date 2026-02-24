---
name: clawbot-memory-optimizer
description: "Installierbarer Skill für ClawBot, um RAM-sparendes Kurzzeit- und Langzeitgedächtnis mit SQLite aufzubauen, Antworten zu cachen und Gemini API-Aufrufe zu reduzieren. Verwenden bei 4-MB-RAM-Limits, Conversation-Storage auf Festplatte und Kostenoptimierung mit Gemini."
---

# ClawBot Memory Optimizer

1. Installiere Python-Abhängigkeiten aus `scripts/requirements.txt`.
2. Initialisiere die Datenbank mit `python scripts/memory_engine.py --init-db`.
3. Setze Gemini Secret als Environment Variable: `export GEMINI_API_KEY="..."`.
4. Starte den lokalen Proxy mit `python scripts/gemini_memory_proxy.py`.
5. Route ClawBot-Requests auf den Proxy statt direkt auf Gemini.
6. Speichere Interaktionen/Facts/Cache disk-first in SQLite statt im RAM.
7. Nutze `python scripts/demo.py` für lokalen Funktionstest.
8. Nutze `python scripts/memory_engine.py --purge-cache` als geplanten Cleanup-Job.

Nutze `references/architecture.md` für Integrationsdetails in bestehende Bot-Pipelines.
