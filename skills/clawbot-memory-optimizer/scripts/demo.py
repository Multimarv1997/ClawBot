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

    engine.set_identity(tenant_id=tenant, category="facts", content="Der Assistent antwortet klar und strukturiert.", stability="stable")
    engine.set_identity(tenant_id=tenant, category="self_image", content="Heute antworte ich besonders kurz.", stability="dynamic")
    engine.set_soul(tenant_id=tenant, category="principles", content="Datenschutz vor Bequemlichkeit.")

    p1 = "Bitte antworte kurz und auf deutsch"
    p2 = "Antworte mir auf deutsch, kurz bitte"
    engine.set_semantic_cached(session, p1, "Okay, ich antworte kurz auf Deutsch.", toy_embedder(p1), user_id=user, tenant_id=tenant)

    print("Semantic Hit:", engine.get_semantic_cached(session, p2, toy_embedder, threshold=0.6, user_id=user, tenant_id=tenant))
    print("Context:\n", engine.build_context(session, query="antwort", user_id=user, tenant_id=tenant))

    print("\nReflection Queue Demo:")
    rid = engine.request_reflection(tenant_id=tenant, user_id=user, trigger_type="explicit", token_reason="demo")
    print("Requested:", rid)
    print("Pending:", engine.get_pending_reflection(tenant_id=tenant, user_id=user))
    print("Approved:", engine.approve_reflection(rid, tenant_id=tenant, user_id=user, approved_tokens=1200))
    print("Executed:", engine.execute_reflection(rid, tenant_id=tenant, user_id=user, reflection_text="Kurze Reflexion: Nutzer möchte kurze Antworten."))
    print("History:", engine.get_reflection_history(tenant_id=tenant, user_id=user, limit=3))

    print("\nProposal Workflow Demo:")
    engine.register_agent(tenant_id=tenant, agent_name="main", agent_type="main", permissions="write")
    engine.register_agent(tenant_id=tenant, agent_name="research_agent", agent_type="subagent", permissions="propose")
    pid = engine.submit_proposal(
        tenant_id=tenant,
        agent_name="research_agent",
        target_store="facts",
        proposal_type="add",
        content='{"session_id":"s1","user_id":"alice","fact":"User arbeitet an Phase D.","tag":"project","memory_scope":"user"}',
        confidence="high",
        priority=3,
    )
    print("Proposal ID:", pid)
    print("Pending Proposals:", engine.get_pending_proposals(tenant_id=tenant, limit=3))
    print("Approved Proposal:", engine.approve_proposal(proposal_id=int(pid or 0), tenant_id=tenant, reviewer_agent="main", review_comment="looks good"))


if __name__ == "__main__":
    main()
