"""grounded_answer: retrieve -> draft -> gate -> remember. Four atoms, one recipe.

You supply `draft(question, evidence, past) -> [{"text", "source_id", "quote"}]`: your one LLM call.
Everything else is code. Needs QDRANT_URL / QDRANT_API_KEY and a hybrid collection with a `text` payload.

    from recipes.grounded_answer import answer
    out = answer("How do I rotate API keys?", "docs", draft=my_llm_call)
    out["gate"]["accepted"]   # claims whose source resolves; the rest never ship
"""
from fieldkit import gate, memory, qdrant
from fieldkit.dag import Dag


def answer(question, collection, draft, scope="default", k=8):
    dag = Dag()

    @dag.node("retrieve", cache=False)
    def _(_inp):
        return {str(p["id"]): p["payload"]["text"] for p in qdrant.hybrid_query(collection, question, limit=k)}

    @dag.node("draft", deps=["retrieve"], kind="agent", cache=False)
    def _(inp):
        return draft(question, inp["retrieve"], memory.prompt_block(question))

    @dag.node("gate", deps=["retrieve", "draft"], cache=False)
    def _(inp):
        ok, bad = gate.crossref_gate(inp["draft"], inp["retrieve"], hard=False)
        return {"accepted": ok, "rejected": [{"claim": c, "why": why} for c, why in bad]}

    @dag.node("remember", deps=["gate"], cache=False)
    def _(inp):
        for c in inp["gate"]["accepted"]:
            memory.record(scope, "claim", c["text"], "pass")
        for r in inp["gate"]["rejected"]:
            memory.record(scope, "claim", r["claim"]["text"], "fail", r["why"])

    return dag.run(log=lambda *_: None)
