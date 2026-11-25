"""gate.py: deterministic sourcing gate. Not an LLM.

A schema makes a claim well-formed (it has a source_id). This gate makes it sourced: the source_id
resolves to a real evidence item. Schema = format, gate = sourcing.

claims:   [{"text", "source_id", "quote"?, "confidence"?}]
evidence: {id: text}
"""
import re
import sys


class CrossrefError(Exception):
    """A claim cites a source_id that is not in the evidence."""


def _norm(s):
    return " ".join(re.findall(r"[a-z0-9]+", str(s or "").lower()))


def _repair(sid, quote, evidence):
    """Models mistype ids. Repair only when exactly one evidence id shares the tail after the first
    '-', and either the tail is a long hash or the evidence holds the claim's quote verbatim. This
    never admits an unsourced claim; it normalises the id of a sourced one."""
    tail = str(sid or "").split("-", 1)[-1]
    same_tail = [i for i in evidence if str(i).endswith("-" + tail)]
    if len(tail) >= 8 and "-" not in tail and len(same_tail) == 1:
        return same_tail[0]
    q = _norm(quote)[:40]
    if len(q) < 20:
        return None
    hits = [i for i in same_tail if q in _norm(evidence[i])]
    return hits[0] if len(hits) == 1 else None


def crossref_gate(claims, evidence, hard=True):
    """Returns (accepted, rejected). With hard=True any rejection raises: unsourced claims never ship."""
    accepted, rejected = [], []
    for c in claims:
        sid = c.get("source_id")
        fixed = sid if sid in evidence else _repair(sid, c.get("quote"), evidence)
        if fixed is None:
            rejected.append((c, f"source_id '{sid}' not in evidence"))
            continue
        if fixed != sid:
            print(f"  [crossref] repaired '{sid}' -> '{fixed}'", file=sys.stderr)
            c["source_id"] = fixed
        accepted.append(c)
    if rejected and hard:
        raise CrossrefError(f"{len(rejected)} unsourced claim(s): " + "; ".join(w for _, w in rejected))
    return accepted, rejected


def quote_gate(claims, evidence, min_words=4):
    """Run after crossref_gate. An id proves the source exists, not that it says this: the cited
    source must hold the claim's quote verbatim (case and punctuation aside). Returns (accepted, rejected)."""
    accepted, rejected = [], []
    for c in claims:
        q = _norm(c.get("quote"))
        if len(q.split()) < min_words:
            rejected.append((c, f"quote shorter than {min_words} words"))
        elif q not in _norm(evidence.get(c.get("source_id"))):
            rejected.append((c, f"quote is not in source '{c.get('source_id')}'"))
        else:
            accepted.append(c)
    return accepted, rejected


def confidence_guard(claims):
    """Range-check confidence in [0, 1]. Constrained decoding strips numeric bounds from a schema."""
    bad = [c for c in claims if not 0.0 <= c["confidence"] <= 1.0]
    if bad:
        raise ValueError(f"{len(bad)} claim(s) with confidence outside [0,1]")
    return claims
