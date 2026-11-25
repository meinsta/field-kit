"""dag.py: a DAG of plain functions with content-hash resume.

A node's fn receives {dep_name: result} and returns its result. A JSON-able result is cached under
<cache_dir>/<node>.<hash>.json, keyed on the node's version, your code fingerprint and its inputs, so
a node re-runs only when one of those changes. Ready siblings run concurrently.

`kind` is a label ("code" | "agent" | "human"). Keep agent nodes few and explicit; the timing line
shows what they cost.
"""
import hashlib, json, os, threading, time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def fingerprint(*paths):
    """Hash of your own source and prompt files. Pass it to Dag() so a code edit busts the cache."""
    h = hashlib.sha1()
    for p in sorted(paths):
        with open(p, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:12]


class Dag:
    def __init__(self, cache_dir=".cache/dag", fingerprint=""):
        self.nodes, self.cache_dir, self.fp = {}, cache_dir, fingerprint

    def node(self, name, deps=(), kind="code", cache=True, version="1"):
        def wrap(fn):
            self.nodes[name] = (fn, list(deps), kind, cache, version)
            return fn
        return wrap

    def _order(self):
        order, seen = [], set()

        def visit(n, stack):
            if n in seen:
                return
            if n in stack:
                raise ValueError(f"cycle through {n}")
            for d in self.nodes[n][1]:
                if d in self.nodes:                 # anything else is supplied via context
                    visit(d, stack | {n})
            seen.add(n); order.append(n)
        for n in self.nodes:
            visit(n, set())
        return order

    def run(self, context=None, resume=True, log=print, workers=None):
        workers = workers or int(os.environ.get("DAG_WORKERS", "8"))
        results, timings, lock = dict(context or {}), [], threading.Lock()
        os.makedirs(self.cache_dir, exist_ok=True)

        def execute(name, inp, cf):
            fn, _, kind, cache, _ = self.nodes[name]
            t0 = time.perf_counter()
            out = fn(inp)
            with lock:
                timings.append((name, kind, time.perf_counter() - t0))
            if cache:
                try:
                    blob = json.dumps(out)          # serialize first: a TypeError mid-dump leaves a torn file
                except TypeError:
                    return out                      # non-JSON result: keep in memory, skip the cache
                with open(cf + ".tmp", "w") as fh:
                    fh.write(blob)
                os.replace(cf + ".tmp", cf)         # atomic: readers see old or new, never partial
            return out

        pending, running = [n for n in self._order() if n not in results], {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while pending or running:
                hit = False
                for name in [n for n in pending if all(d in results for d in self.nodes[n][1] if d in self.nodes)]:
                    pending.remove(name)
                    _, deps, kind, cache, version = self.nodes[name]
                    inp = {d: results.get(d) for d in deps}
                    key = hashlib.sha1(f"{version}|{self.fp}|{json.dumps(inp, sort_keys=True, default=str)}".encode()).hexdigest()[:12]
                    cf = os.path.join(self.cache_dir, f"{name}.{key}.json")
                    if resume and cache and os.path.exists(cf):
                        try:
                            results[name] = json.load(open(cf)); hit = True
                            log(f"· {name}: cached")
                            continue
                        except ValueError:          # a corrupt cache file is a miss, not a crash
                            pass
                    log(f"· {name}: run ({kind})")
                    running[pool.submit(execute, name, inp, cf)] = name
                if not running:
                    if hit:
                        continue                    # a cache hit may have unblocked dependants
                    if pending:
                        raise ValueError(f"unsatisfiable deps for {sorted(pending)}")
                    break
                for fut in wait(list(running), return_when=FIRST_COMPLETED)[0]:
                    name = running.pop(fut)
                    try:
                        results[name] = fut.result()
                    except BaseException:
                        for f in running:           # fail fast
                            f.cancel()
                        raise
        if timings:
            by_kind = {}
            for _, k, d in timings:
                by_kind[k] = by_kind.get(k, 0.0) + d
            log("· timing: " + ", ".join(f"{k} {v:.1f}s" for k, v in sorted(by_kind.items()))
                + "; slowest: " + ", ".join(f"{n} {d:.1f}s" for n, _, d in sorted(timings, key=lambda t: -t[2])[:5]))
        return results
