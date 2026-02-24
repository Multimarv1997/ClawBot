# Architektur: zuverlässiges Memory für ClawBot (RAM-sparend)

## Ziele
- **Kurzzeitgedächtnis (STM):** Kontext der letzten Turns für Gesprächskohärenz.
- **Langzeitgedächtnis (LTM):** Persistente Fakten und Präferenzen über Sessions.
- **API-Reduktion:** Antworten cachen und identische Requests wiederverwenden.
- **4 MB RAM Budget:** Memory-Pfad disk-first auf SQLite.

## Datenmodell (alles auf Festplatte)
- `interactions`: vollständige Turn-Historie (user, assistant, timestamp).
- `facts`: verdichtete Fakten mit Tags und Priorität.
- `cache`: Prompt-Hash, Antwort, Ablaufzeitpunkt.

## Request-Fluss
1. Nutzerinput kommt beim Proxy an.
2. Prompt wird normalisiert und gehasht.
3. Cache wird geprüft.
4. Falls miss:
   - letzte N Turns aus SQLite (STM) laden.
   - relevante LTM-Facts laden.
   - Kontext in Request integrieren.
   - Request an Gemini senden.
5. Antwort in `interactions` + `cache` speichern.

## Gemini API Key
- Key wird **nicht** im Code gespeichert.
- Key nur per `GEMINI_API_KEY`-Umgebungsvariable setzen.
- Vorteil: kein Leak im Repo, einfache Rotation, getrennte Secrets pro Umgebung.

## Was zuerst wichtig ist
1. **Datenschutz:** nur nötige Daten speichern, sensible Felder anonymisieren.
2. **TTL/Invalidierung:** Cache darf nicht veraltete Fakten ausliefern.
3. **Relevanzfilter:** Nicht alles in Prompt schieben, sonst Tokenkosten steigen.
4. **Beobachtbarkeit:** Cache-Hit-Rate, API-Call-Rate, Latenz tracken.

## Machbarkeit
Ja, sehr gut machbar. Exact-match Cache + DB-STM spart meist deutlich Calls; semantischer Cache erhöht Trefferquote weiter.
