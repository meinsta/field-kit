"""Offline checks, no cluster. Run: python3 test_fieldkit.py (or pytest)."""
import tempfile

from fieldkit import approvals, bridge, gate, memory, preflight, qdrant, recall
from fieldkit.dag import Dag


def test_dag_resume_and_version():
    d, calls = tempfile.mkdtemp(), []

    def build(version):
        dag = Dag(cache_dir=d)

        @dag.node("a")
        def _(_i):
            calls.append("a"); return 2

        @dag.node("b", deps=["a", "x"], version=version)
        def _(i):
            calls.append("b"); return i["a"] * i["x"]
        return dag
    quiet = lambda *_: None
    assert build("1").run({"x": 5}, log=quiet)["b"] == 10 and calls == ["a", "b"]
    assert build("1").run({"x": 5}, log=quiet)["b"] == 10 and calls == ["a", "b"]          # both cached
    assert build("2").run({"x": 5}, log=quiet)["b"] == 10 and calls == ["a", "b", "b"]     # version busts one node
    assert build("2").run({"x": 6}, log=quiet)["b"] == 12                                  # input change re-runs

    cyc = Dag(cache_dir=d)
    cyc.node("p", deps=["q"])(lambda i: 1); cyc.node("q", deps=["p"])(lambda i: 1)
    try:
        cyc.run(log=quiet); assert False
    except ValueError:
        pass


def test_gate():
    ev = {"TX-acme-58": "We rotate keys every ninety days across all of our clusters.", "CM-9f3a11c2d4": "other"}
    ok, bad = gate.crossref_gate([
        {"text": "exact", "source_id": "TX-acme-58"},
        {"text": "wrong prefix, long hash", "source_id": "TX-9f3a11c2d4"},
        {"text": "wrong prefix, quote proves it", "source_id": "IN-acme-58", "quote": "we rotate keys every ninety days"},
        {"text": "wrong prefix, no proof", "source_id": "IN-acme-58"},
        {"text": "invented", "source_id": "TX-nope-1"}], ev, hard=False)
    assert [c["source_id"] for c in ok] == ["TX-acme-58", "CM-9f3a11c2d4", "TX-acme-58"] and len(bad) == 2
    try:
        gate.crossref_gate([{"text": "invented", "source_id": "zz"}], ev); assert False
    except gate.CrossrefError:
        pass


def test_qdrant_bodies():
    sent = []
    qdrant._req = lambda m, p, b=None: sent.append((m, p, b)) or {"result": {"points": []}}
    qdrant.hybrid_query("c", "q", limit=5, flt={"must": []}, boosts=[("corpus", "docs", 0.01)])
    body = sent[-1][2]
    inner = body["prefetch"][0]
    assert body["query"]["formula"]["sum"][1] == {"mult": [0.01, {"key": "corpus", "match": {"value": "docs"}}]}
    assert inner["query"] == {"fusion": "rrf"} and [p["using"] for p in inner["prefetch"]] == ["text", "sparse"]
    assert all(p["filter"] == {"must": []} and p["limit"] == 20 for p in inner["prefetch"])
    assert qdrant.point_id("a", 1) == qdrant.point_id("a", 1) != qdrant.point_id("a", 2)


def test_memory_prompt_block():
    rows = {"fail": [{"slot": "claim", "text": "bad", "reason": "unsourced"}], "pass": [{"slot": "claim", "text": "good"}],
            "lesson": [{"scope": "docs", "text": "cite the page"}]}
    memory._nearest = lambda q, k, key, value: rows[value]
    b = memory.prompt_block("q")
    assert "LESSON (learned on docs): cite the page" in b and 'AVOID (claim): "bad" (unsourced)' in b and 'REPEAT (claim): "good"' in b
    memory._nearest = lambda *a: []
    assert memory.prompt_block("q") == ""


def test_recall_ceiling():
    r = recall.reach(target=["Alpha fact.", "Beta fact", "Gamma fact"], queries=["q1", "q2"],
                     fetch=lambda q: ["alpha FACT"] if q == "q1" else ["noise"], corpus=["Alpha fact", "beta fact", "noise"])
    assert (r["target"], r["reachable"], r["reached"]) == (3, 2, 1)
    assert r["not_in_index"] == ["gamma fact"] and r["missed"] == ["beta fact"]
    gold = [{"q": "q1", "target": ["Alpha fact"]}, {"q": "q2", "target": ["Beta fact"]}]
    h = recall.hit_rate(gold, lambda q: ["alpha fact", "beta fact"] if q == "q1" else ["noise"], corpus=["alpha fact"])
    assert (h["hits"], h["missed"], h["not_in_index"]) == (1, ["q2"], ["beta fact"])     # q2 cannot borrow q1's hit


def test_approvals_and_bridge():
    report = [{"row": 1, "candidates": [{"id": "a"}, {"id": "b"}]}]
    assert approvals.select_approved(report, {"b"})[0]["candidates"] == [{"id": "b"}]
    bridge._self_check()


def test_preflight_blocking_vs_advisory():
    def boom():
        raise OSError("unreachable")
    quiet = lambda *_: None
    assert [r[1] for r in preflight.gate([("a", lambda: (True, "fine"), True), ("b", boom, False)], log=quiet)] == [True, False]
    try:
        preflight.gate([("b", boom, True)], log=quiet); assert False
    except SystemExit:
        pass


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
