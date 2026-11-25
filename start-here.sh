#!/bin/bash
# The whole path on one made-up company, in order. Needs python3, git, and a Qdrant Cloud cluster:
#   export QDRANT_URL=... QDRANT_API_KEY=...
#   ./start-here.sh
set -e
cd "$(dirname "$0")"
: "${QDRANT_URL:?set QDRANT_URL and QDRANT_API_KEY first}"
PY="${PYTHON:-python3}"
C="${FIELD_KIT_COLLECTION:-quillfeather}"
export GROUNDING_COLLECTION=$C KNOWLEDGE_COLLECTION=$C FIELD_COLLECTION=$C PYTHONPATH="$PWD:."
mkdir -p kits
for k in grounding-layer org-knowledge-mcp field-systems field-guides page-engine; do
  [ -d kits/$k ] || git clone -q https://github.com/meinsta/$k kits/$k
done
step() { echo; echo "== $1"; }

step "1. grounding-layer: sync docs, field notes and tickets into Qdrant, then gate on recall"
cd kits/grounding-layer
$PY -m grounding.refresh
KEY=$($PY -m fieldkit.bridge --pending | head -1 | cut -d' ' -f1)
if [ ${#KEY} -eq 16 ]; then                      # the queued ticket call: store the connector's result, sync again
  $PY -m fieldkit.bridge --fulfil "$KEY" --from-json fixtures/tickets-response.json > /dev/null
  $PY -m grounding.refresh | grep tickets
fi

step "2. org-knowledge-mcp: ask the sources, and check that claims are cited"
cd ../org-knowledge-mcp && $PY evaluate.py

step "3. field-systems: build field cards, then check every cited line is current"
cd ../field-systems && $PY -m fieldsys.card && $PY -m fieldsys.keep_current --detect

step "4. field-guides: outline a guide, then take an expert's comment"
cd ../field-guides && $PY -m fieldguide.outline && $PY -m fieldguide.review

step "5. page-engine: build a product page and its paid landing cut"
cd ../page-engine && $PY -m pageengine.build && $PY -m pageengine.build --track paid

echo; echo "Done. Read the outputs in kits/*/cards, kits/*/out and kits/field-systems/review."
