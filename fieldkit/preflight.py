"""preflight.py: probe every dependency a run uses before the run starts, so an expired credential
is caught up front instead of thinning the output halfway through.

probes: [(name, fn, blocking)]. fn returns (ok, detail) and may raise. A blocking failure halts the
run; an advisory one is reported and the run degrades honestly.
"""
import os
import socket
import sys

from . import bridge as B, qdrant as Q

TIMEOUT = float(os.environ.get("PREFLIGHT_TIMEOUT_S", 8))


def check(probes, log=print):
    out = []
    prev = socket.getdefaulttimeout()
    socket.setdefaulttimeout(TIMEOUT)       # one unreachable endpoint must not hang the whole run
    try:
        for name, fn, blocking in probes:
            try:
                ok, detail = fn()
            except (Exception, SystemExit) as e:
                ok, detail = False, f"{type(e).__name__}: {str(e)[:70]}"
            out.append((name, ok, detail, blocking))
            log(f"  {'OK   ' if ok else 'STALE'} {name:10} {detail}")
    finally:
        socket.setdefaulttimeout(prev)
    return out


def gate(probes, log=lambda m: print(m, file=sys.stderr)):
    res = check(probes, log)
    bad = [n for n, ok, _, blocking in res if not ok and blocking]
    if bad:
        raise SystemExit(f"preflight: blocking failure in {', '.join(bad)}")
    return res


def qdrant():
    r = Q._req("GET", "/collections", timeout=TIMEOUT)     # explicit: urlopen's own timeout overrides the socket default
    return True, f"{len(r['result']['collections'])} collections"


def bridge():
    """Health of a cache-first bridge: can it serve, and what is waiting. Never 'is the session fresh'."""
    n = len([f for f in os.listdir(B.EXTRACTS) if f.endswith(".json")]) if os.path.isdir(B.EXTRACTS) else 0
    return True, f"{n} extracts, {len(B.pending())} pending"
