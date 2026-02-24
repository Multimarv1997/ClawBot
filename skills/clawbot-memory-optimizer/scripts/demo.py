#!/usr/bin/env python3
from memory_engine import MemoryEngine


def main() -> None:
    engine = MemoryEngine()
    engine.init_db()

    engine.remember_fact("Nutzer bevorzugt kurze deutsche Antworten.", tag="preference", priority=3)
    engine.remember_turn("user", "Wie war nochmal mein Antwortstil?")

    context = engine.build_context(query="Antwortstil")
    print("=== Kontext ===")
    print(context)

    prompt = "Wie war nochmal mein Antwortstil?"
    print("\nCache vorher:", engine.get_cached(prompt))
    engine.set_cached(prompt, "Du bevorzugst kurze deutsche Antworten.")
    print("Cache nachher:", engine.get_cached(prompt))


if __name__ == "__main__":
    main()
