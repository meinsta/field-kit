#!/usr/bin/env python3
"""
approvals.py — shared primitives for the propose → approve → bind workflow used by
both docs_harvest and persona_mine. Only the genuinely-identical pieces live here;
each module keeps its own curate()/render_proposal()/apply() (their shapes differ).
"""
import os, re


def norm(s):
    """Lowercase, collapse non-alphanumerics to single spaces, strip. Text-dedup key."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def read_id_list(path):
    """Approved ids from a text file (one per line; '#' comments and blanks ignored).
    Empty set if the file is absent."""
    if not os.path.exists(path):
        return set()
    out = set()
    for line in open(path):
        line = line.split("#")[0].strip()
        if line:
            out.add(line)
    return out


def select_approved(report, approved):
    """Filter each report row's candidates to the ids in `approved`, preserving row keys."""
    return [{**r, "candidates": [c for c in r["candidates"] if c["id"] in approved]} for r in report]
