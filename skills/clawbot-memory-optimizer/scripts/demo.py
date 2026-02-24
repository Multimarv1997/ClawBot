#!/usr/bin/env python3
from memory_engine import MemoryEngine


def toy_embedder(text: str):
    dims = ["antwortstil", "kurz", "deutsch", "technik", "preis"]
    t = text.lower()
    return [1.0 if w in t else 0.0 for w in dims]


def main() -> None:
    engine = MemoryEngine()
    engine.init_db()

    session = "s1"
    user = "alice"
    tenant = "acme"

    tid = engine.remember_turn(session, "user", "Bitte antworte kurz auf deutsch", user_id=user, tenant_id=tenant)
    engine.remember_fact(
        session,
        "Nutzer bevorzugt kurze deutsche Antworten.",
        tag="preference",
        priority=3,
        confidence=0.9,
        source_turn_id=tid,
        user_id=user,
        tenant_id=tenant,
    )
    engine.create_summary(session, "- User möchte kurze deutsche Antworten.", 1, 1, user_id=user, tenant_id=tenant)

    p1 = "Bitte antworte kurz und auf deutsch"
    p2 = "Antworte mir auf deutsch, kurz bitte"
    engine.set_semantic_cached(session, p1, "Okay, ich antworte kurz auf Deutsch.", toy_embedder(p1), user_id=user, tenant_id=tenant)

    print("Semantic Hit:", engine.get_semantic_cached(session, p2, toy_embedder, threshold=0.6, user_id=user, tenant_id=tenant))
    print("Context:\n", engine.build_context(session, query="antwort", user_id=user, tenant_id=tenant))


if __name__ == "__main__":
    main()
