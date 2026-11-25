"""memory.py: outcome memory in Qdrant. Embed the task, pull similar past failures and successes,
fold them into the prompt, run, judge in code, upsert the outcome. Lessons learned in one scope
apply to every scope: origin is payload, never a filter.

payload: scope, slot, text, verdict (pass | fail), reason, source, ts
"""
import datetime
from . import qdrant as Q

COLL = "outcome_memory"


def record(scope, slot, text, verdict, reason="", source="run"):
    text = " ".join((text or "").split())[:600]
    if not text:
        return None
    Q.hybrid_collection(COLL, keyword_indexes=("scope", "verdict", "slot", "source"))
    pid = Q.point_id(COLL, scope, slot, text)
    Q.upsert(COLL, [Q.hybrid_point(pid, f"{slot}: {text}. {reason}", {
        "scope": scope, "slot": slot, "text": text, "verdict": verdict, "reason": reason[:300], "source": source,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()})])
    return pid


def _nearest(query, k, key, value):
    try:
        pts = Q.hybrid_query(COLL, query, limit=k, flt={"must": [{"key": key, "match": {"value": value}}]})
    except Exception:      # no collection yet, or the cluster is unreachable: an empty memory, not a failed run
        return []
    return [p.get("payload") or {} for p in pts]


def retrieve(query, k=4):
    return {v: _nearest(query, k, "verdict", v) for v in ("fail", "pass")}


def lessons(query, k=3):
    """Lessons are records with source="lesson": what a failure taught, in a sentence."""
    return _nearest(query, k, "source", "lesson")


def prompt_block(query, k=4):
    """AVOID / REPEAT / LESSON lines to prepend to a prompt. Empty string when memory has nothing."""
    r = retrieve(query, k)
    lines = [f"  LESSON (learned on {p.get('scope')}): {p.get('text', '')[:220]}" for p in lessons(query)]
    lines += [f"  AVOID ({p.get('slot')}): \"{p.get('text', '')[:140]}\" ({p.get('reason', '')[:120]})" for p in r["fail"]]
    lines += [f"  REPEAT ({p.get('slot')}): \"{p.get('text', '')[:140]}\"" for p in r["pass"]]
    return ("PAST OUTCOMES on this subject (judged in code; learn from them):\n"
            + "\n".join(dict.fromkeys(lines)) + "\n\n") if lines else ""
