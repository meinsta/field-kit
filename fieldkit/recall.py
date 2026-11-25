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
