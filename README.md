# field-kit

Small, single-file building blocks for systems that put Qdrant behind an agent. Each atom does one
job, depends on the standard library plus a Qdrant cluster, and passes plain dicts. Recipes compose
atoms into something a customer can run.

Lifted from a production content pipeline, then stripped of everything specific to it.

```bash
pip install git+https://github.com/meinsta/field-kit
python3 test_fieldkit.py        # offline, no cluster, no keys
```

Kits built on it: [grounding-layer](https://github.com/meinsta/grounding-layer).

## The contract

Atoms agree on two shapes and nothing else.

| Shape | Form |
|---|---|
| Evidence | `{id: text}` |
| Claim | `{"text", "source_id", "quote"?, "confidence"?}` |

A model writes claims. Code decides whether they ship.

## Atoms

| Atom | Job | Needs |
|---|---|---|
| [`qdrant`](fieldkit/qdrant.py) | Hybrid search over REST: dense + BM25 fused with RRF, score boosts, grouping, recommendation, scroll. Cloud Inference embeds server-side. | Cluster |
| [`dag`](fieldkit/dag.py) | Run plain functions as a DAG. Content-hash resume, parallel siblings, per-kind timing. | Nothing |
| [`gate`](fieldkit/gate.py) | Reject any claim whose `source_id` is missing from the evidence. Repairs a mistyped id only when the evidence proves it. | Nothing |
| [`memory`](fieldkit/memory.py) | Store judged outcomes in Qdrant and return the nearest failures, successes and lessons as a prompt block. | Cluster |
| [`recall`](fieldkit/recall.py) | Reach and per-question hit rate against a frozen golden set, with the ceiling first: how many targets are in the index at all. | Nothing |
| [`preflight`](fieldkit/preflight.py) | Probe every dependency before a run. Blocking failures halt; advisory ones degrade honestly. | Nothing |
| [`approvals`](fieldkit/approvals.py) | Propose, approve, bind: a human approves ids in a text file and code binds only those. | Nothing |
| [`bridge`](fieldkit/bridge.py) | Queue tool calls that only an interactive connector can make. A headless session fulfils them and the raw result is cached by call. | Nothing |

## Recipes

| Recipe | Atoms | What you supply |
|---|---|---|
| [`grounded_answer`](recipes/grounded_answer.py) | qdrant, dag, gate, memory | One function: your LLM call that drafts claims |

A recipe is the unit a customer sees. Each one should ship with fixture data, a frozen golden set
and a number from `recall.reach` in its README.

## Next atoms

| Atom | Job |
|---|---|
| `llm` | One structured call with a schema, cached on model + prompt + schema. `verify(claim, source)` builds on it. |
| `disclosure` | Name a customer only when a public source names them. Otherwise return a descriptor. |
| `classify` | Label a sentence by its nearest labelled exemplars in Qdrant. A gate without a regex. |
| `drift` | Detect, propose, approve: compare a shipped asset against its source of truth. |
