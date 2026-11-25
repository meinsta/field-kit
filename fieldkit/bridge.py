#!/usr/bin/env python3
"""
bridge.py: the calls only an interactive connector can make (an OAuth MCP tool your pipeline has no
credentials for), as a cache-first queue the pipeline reads and a headless cron fulfils.
A pending entry is {key, tool, input}; a headless Claude session issues exactly that tool call; the
session transcript holds the RAW result; this stores it under the entry's key; the next build reads it.
The model never transcribes data, and a paraphrased call simply fails to match and is reported.

    python3 -m fieldkit.bridge --pending                      # what is queued and unfulfilled
    python3 -m fieldkit.bridge --pending-json out.json        # the cron's batch input
    python3 -m fieldkit.bridge --from-transcript s.jsonl      # store every matching tool result
    python3 -m fieldkit.bridge --fulfil <key> --from-json r.json

Extracts live in <data root>/.cache/connector-extracts/<key>.json = {tool, input, ts, result}.
"""
import argparse, hashlib, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.environ.get("ATOMS_DATA_ROOT") or os.getcwd()
EXTRACTS = os.path.join(DATA_ROOT, ".cache", "connector-extracts")
PENDING = os.path.join(EXTRACTS, "pending.jsonl")
STALE_DAYS = float(os.environ.get("CONNECTOR_EXTRACT_STALE_DAYS", "7"))
_DROP = {"intent"}
# Calls that change something (a sent Slack message, a draft). Their extract never goes stale: a stale extract
# re-queues the call, and the weekly cron would send the same message again every 7 days.
WRITES = {"mcp__claude_ai_Slack__slack_send_message", "mcp__claude_ai_Slack__slack_send_message_draft"}


def _fresh(tool, have):
    return bool(have) and (tool in WRITES or have["age_days"] < STALE_DAYS)            # analytics-only arguments a connector adds; never part of the identity of a call


def key(tool, inp):
    canon = json.dumps({k: v for k, v in (inp or {}).items() if k not in _DROP}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(f"{tool} {canon}".encode()).hexdigest()[:16]


def _path(k):
    return os.path.join(EXTRACTS, k + ".json")


def load(tool, inp):
    """The stored extract for this exact call, with its age in days; None when never fulfilled."""
    try:
        d = json.load(open(_path(key(tool, inp))))
    except (OSError, ValueError):
        return None
    d["age_days"] = (time.time() - d.get("ts", 0)) / 86400
    return d


def queue(tool, inp, refresh=False):
    """Record the call as wanted (idempotent). Returns (key, extract-or-None). refresh=True drops a stored
    read so it is fetched again (a poll, e.g. reactions); never use it on a write."""
    k = key(tool, inp)
    if refresh and tool not in WRITES:
        try:
            os.remove(_path(k))
        except OSError:
            pass
    have = load(tool, inp)
    if _fresh(tool, have):
        return k, have
    os.makedirs(EXTRACTS, exist_ok=True)
    seen = {json.loads(l).get("key") for l in open(PENDING)} if os.path.exists(PENDING) else set()
    if k not in seen:
        with open(PENDING, "a") as f:
            f.write(json.dumps({"key": k, "tool": tool, "input": inp, "ts": time.time()}) + "\n")
    return k, have


def pending():
    """Queued calls with no fresh extract, oldest first, one per key."""
    out, seen = [], set()
    for line in (open(PENDING) if os.path.exists(PENDING) else []):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("key") in seen:
            continue
        seen.add(d["key"])
        if _fresh(d["tool"], load(d["tool"], d["input"])):
            continue
        out.append({"key": d["key"], "tool": d["tool"], "input": d["input"]})
    return out


def store(tool, inp, result):
    os.makedirs(EXTRACTS, exist_ok=True)
    p = _path(key(tool, inp))
    json.dump({"tool": tool, "input": inp, "ts": time.time(), "result": result}, open(p, "w"))
    return p


def _parse(raw):
    if isinstance(raw, str) and "exceeds maximum" in raw:          # Claude Code parked the oversized result on disk
        m = re.search(r"saved to (\S+?\.txt)", raw)
        if m and os.path.exists(m.group(1)):
            raw = open(m.group(1)).read()
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def from_transcript(path):
    """Store every tool result in a headless session's transcript whose (tool, input) is a queued call."""
    wanted = {p["key"]: p for p in pending()}
    uses, results = {}, {}
    for line in open(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        c = (d.get("message") or {}).get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if b.get("type") == "tool_use":
                uses[b["id"]] = (b.get("name"), b.get("input") or {})
            elif b.get("type") == "tool_result":
                cc = b.get("content")
                if isinstance(cc, list):
                    cc = "".join(x.get("text", "") for x in cc if isinstance(x, dict))
                results[b.get("tool_use_id")] = cc
    ok, skipped = 0, 0
    for tid, (tool, inp) in uses.items():
        if not str(tool).startswith("mcp__"):        # Read / ToolSearch: the session's own plumbing, never a queued call
            continue
        k = key(tool, inp)
        if k not in wanted or tid not in results:
            skipped += 1
            print(f"  [{k}] skipped — {'no result' if tid in uses and tid not in results else 'not a queued call (model changed the arguments?)'}: {tool}")
            continue
        res = _parse(results[tid])
        if isinstance(res, dict) and res.get("error"):
            skipped += 1; print(f"  [{k}] skipped — connector error: {str(res['error'])[:80]}"); continue
        store(tool, wanted[k]["input"], res); ok += 1
        print(f"  [{k}] stored {tool}")
    return ok, skipped


def _self_check():
    import tempfile
    global EXTRACTS, PENDING
    EXTRACTS = tempfile.mkdtemp(); PENDING = os.path.join(EXTRACTS, "pending.jsonl")
    t, i = "mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql", {"cloudId": "x", "jql": "text ~ \"acme\""}
    assert key(t, i) == key(t, dict(i, intent="why I call"))          # the connector's analytics arg is not identity
    k, have = queue(t, i); assert have is None and pending()[0]["key"] == k
    tr = os.path.join(EXTRACTS, "s.jsonl")
    with open(tr, "w") as f:
        f.write(json.dumps({"message": {"content": [{"type": "tool_use", "id": "u1", "name": t, "input": dict(i, intent="...")}]}}) + "\n")
        f.write(json.dumps({"message": {"content": [{"type": "tool_result", "tool_use_id": "u1", "content": [{"type": "text", "text": "{\"issues\": [{\"key\": \"PM-1\"}]}"}]}]}}) + "\n")
    assert from_transcript(tr) == (1, 0) and load(t, i)["result"]["issues"][0]["key"] == "PM-1" and pending() == []
    w, wi = "mcp__claude_ai_Slack__slack_send_message", {"channel_id": "U1", "message": "hi"}
    queue(w, wi); store(w, wi, {"ok": True})
    d = json.load(open(_path(key(w, wi)))); d["ts"] -= 30 * 86400; json.dump(d, open(_path(key(w, wi)), "w"))
    assert pending() == [] and queue(w, wi, refresh=True)[1]           # a month-old send is never re-sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending", action="store_true"); ap.add_argument("--pending-json")
    ap.add_argument("--from-transcript"); ap.add_argument("--fulfil"); ap.add_argument("--from-json")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        _self_check(); print("ok"); return
    if a.pending_json:
        # CONNECTOR_FULFIL_ONLY=<regex on tool name>: a caller that needs its own calls now fulfils only those; everything else waits for the weekly cron instead of riding along at ~$0.7/5.
        only = os.environ.get("CONNECTOR_FULFIL_ONLY")
        todo = [p for p in pending() if not only or re.search(only, p["tool"])]
        json.dump(todo, open(a.pending_json, "w"), indent=1); print(f"{len(todo)} pending -> {a.pending_json}"); return
    if a.from_transcript:
        ok, sk = from_transcript(a.from_transcript); print(f"stored {ok}, skipped {sk}"); return
    if a.fulfil:
        p = next((p for p in pending() if p["key"] == a.fulfil), None)
        if not p:
            raise SystemExit(f"{a.fulfil}: not a pending key")
        print(store(p["tool"], p["input"], _parse(open(a.from_json).read()))); return
    for p in pending():
        print(f"{p['key']}  {p['tool']}  {json.dumps(p['input'])[:120]}")
    print(f"{len(pending())} pending")


if __name__ == "__main__":
    main()
