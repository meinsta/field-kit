"""recall.py: ceiling before score. Before reporting recall, count how many golden answers are in
the index at all. A target the corpus never ingested is an ingest bug, and no ranker can fix it.

The label set must be independent of the ranker: freeze the targets first, then measure once per variant.
"""
from .approvals import norm


def reach(target, queries, fetch, corpus=None):
    """target: golden texts. fetch(query) -> [text] is the variant under test. corpus: every text in
    the index (qdrant.scroll), or None to skip the ceiling."""
    t = {norm(x) for x in target}
    reachable = t & {norm(x) for x in corpus} if corpus is not None else t
    got = t & {norm(x) for q in queries for x in fetch(q)}
    return {"target": len(t), "reachable": len(reachable), "reached": len(got),
            "ceiling": len(reachable) / max(1, len(t)), "reach": len(got) / max(1, len(t)),
            "not_in_index": sorted(t - reachable), "missed": sorted(reachable - got)}


def hit_rate(gold, fetch, corpus=None):
    """gold: [{"q", "target": [text]}]. A question hits when every one of its targets is in fetch(q).
    Stricter than reach(): each question has to find its own answer, not borrow another question's hits."""
    t = {norm(x) for g in gold for x in g["target"]}
    reachable = t & {norm(x) for x in corpus} if corpus is not None else t
    missed = [g["q"] for g in gold if not {norm(x) for x in g["target"]} <= {norm(x) for x in fetch(g["q"])}]
    return {"questions": len(gold), "hits": len(gold) - len(missed), "hit_rate": (len(gold) - len(missed)) / max(1, len(gold)),
            "ceiling": len(reachable) / max(1, len(t)), "not_in_index": sorted(t - reachable), "missed": missed}
