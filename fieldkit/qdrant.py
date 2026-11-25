"""qdrant.py: the one search surface. Qdrant over REST (stdlib urllib, no client library).

Hybrid shape: named dense `text` + sparse `sparse` (bm25, IDF modifier), both embedded server-side
by Qdrant Cloud Inference, fused with RRF. The client never loads a model.

Env: QDRANT_URL, QDRANT_API_KEY. Or call configure().
"""
import json, os, uuid, urllib.error, urllib.request

URL = os.environ.get("QDRANT_URL", "").rstrip("/")
KEY = os.environ.get("QDRANT_API_KEY", "")
TEXT_MODEL = "mixedbread-ai/mxbai-embed-large-v1"
SPARSE_MODEL = "qdrant/bm25"
TEXT_DIM = 1024
NS = uuid.UUID("a1b2c3d4-0000-4000-8000-000000000001")


def configure(url=None, key=None):
    global URL, KEY
    URL, KEY = (url.rstrip("/") if url else URL), (key or KEY)
    return URL


def _req(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method,
                                 headers={"api-key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def point_id(*parts):
    """Deterministic UUID from key parts, so a re-ingest upserts instead of duplicating."""
    return str(uuid.uuid5(NS, ":".join(str(p) for p in parts)))


def doc(text, model=TEXT_MODEL):
    """A Cloud Inference document: the server embeds it, as a query or as a point."""
    return {"text": text, "model": model}


def hybrid_collection(name, keyword_indexes=()):
    """Ensure the hybrid collection exists and index the payload fields that filters will hit
    (the cluster rejects a filter on an unindexed field). Returns True when created."""
    try:
        _req("GET", f"/collections/{name}")
        return False
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    _req("PUT", f"/collections/{name}", {"vectors": {"text": {"size": TEXT_DIM, "distance": "Cosine"}},
                                          "sparse_vectors": {"sparse": {"modifier": "idf"}}})
    for f in keyword_indexes:
        _req("PUT", f"/collections/{name}/index", {"field_name": f, "field_schema": "keyword"})
    return True


def hybrid_point(pid, text, payload):
    return {"id": pid, "vector": {"text": doc(text), "sparse": doc(text, SPARSE_MODEL)}, "payload": payload}


def upsert(name, points, batch=64):
    """Batches of 64: Cloud Inference embeds two vectors per point and one large PUT breaks the connection."""
    for i in range(0, len(points), batch):
        _req("PUT", f"/collections/{name}/points?wait=true", {"points": points[i:i + batch]})
    return len(points)


def query(name, body):
    return _req("POST", f"/collections/{name}/points/query", body).get("result", {}).get("points", [])


def _prefetch(q, depth, flt):
    pre = [{"query": doc(q), "using": "text", "limit": depth},
           {"query": doc(q, SPARSE_MODEL), "using": "sparse", "limit": depth}]
    if flt:
        for pf in pre:
            pf["filter"] = flt
    return pre


def hybrid_query(name, q, limit=24, flt=None, depth=None, boosts=()):
    """Dense + bm25 prefetch fused by RRF. `boosts`: [(payload_key, value_or_list, weight)] adds
    `weight` to the fused score when the condition holds: business logic as a boost instead of a hard
    filter. A rank-1 RRF hit scores about 0.016, so weights live in the 0.005 to 0.02 range."""
    depth = depth or limit * 4
    pre = _prefetch(q, depth, flt)
    if not boosts:
        return query(name, {"prefetch": pre, "query": {"fusion": "rrf"}, "limit": limit, "with_payload": True})
    terms = ["$score"]
    for key, val, w in boosts:
        cond = {"key": key, "match": ({"any": list(val)} if isinstance(val, (list, tuple, set)) else {"value": val})}
        terms.append({"mult": [w, cond]})
    return query(name, {"prefetch": [{"prefetch": pre, "query": {"fusion": "rrf"}, "limit": depth}],
                        "query": {"formula": {"sum": terms}}, "limit": limit, "with_payload": True})


def hybrid_groups(name, q, group_by, limit=18, group_size=4, depth=200, flt=None):
    """Same fusion, grouped by a payload field, so one account or source cannot flood the result."""
    body = {"prefetch": _prefetch(q, depth, flt), "query": {"fusion": "rrf"}, "group_by": group_by,
            "group_size": group_size, "limit": limit, "with_payload": True}
    return _req("POST", f"/collections/{name}/points/query/groups", body)["result"]["groups"]


def recommend(name, positive, negative=(), limit=24, flt=None, strategy="best_score"):
    """Point ids a human approved as positives (and noise as negatives) steer the search. No query
    text, so no vocabulary gap between how users ask and how the corpus is written."""
    body = {"query": {"recommend": {"positive": list(positive), "negative": list(negative), "strategy": strategy}},
            "using": "text", "limit": limit, "with_payload": True}
    if flt:
        body["filter"] = flt
    return query(name, body)


def scroll(name, flt=None, page=1000, with_payload=True):
    """Every point. Use it to freeze a golden set, compute a recall ceiling, or diff a source against the index."""
    nxt = None
    while True:
        r = _req("POST", f"/collections/{name}/points/scroll",
                 {"limit": page, "offset": nxt, "with_payload": with_payload, **({"filter": flt} if flt else {})})["result"]
        yield from r["points"]
        nxt = r.get("next_page_offset")
        if not nxt:
            return


def delete(name, ids, batch=512):
    for i in range(0, len(ids), batch):
        _req("POST", f"/collections/{name}/points/delete?wait=true", {"points": ids[i:i + batch]})
    return len(ids)


def drop(name):
    try:
        _req("DELETE", f"/collections/{name}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
