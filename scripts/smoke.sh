#!/usr/bin/env bash
# Walks one garment from sketch to quotes against a running app.
set -euo pipefail
U=${1:-http://localhost:8000}; K="X-API-Key: ${2:-demo-northwind}"
here=$(cd "$(dirname "$0")/.." && pwd)
j() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)"; }
code="SMOKE-$(date +%s)"
SID=$(curl -sf -H "$K" -F code=$code -F name="Boxy pocket tee" -F garment=tee -F season=SS27 \
      -F features=chest_pocket -F "sketch=@$here/static/samples/tee.svg;type=image/svg+xml" $U/api/styles | j 'd["id"]')
echo "style $SID"
for _ in $(seq 20); do v=$(curl -sf -H "$K" $U/api/styles/$SID | j 'len(d["versions"])'); [ "$v" -ge 1 ] && break; sleep 0.3; done
V1=$(curl -sf -H "$K" $U/api/styles/$SID | j 'd["versions"][0]["id"]')
echo "approve v1: $(curl -sf -X POST -H "$K" $U/api/versions/$V1/approve)"
echo "send v1:    $(curl -sf -X POST -H "$K" $U/api/versions/$V1/send)"
echo "send again: $(curl -sf -X POST -H "$K" $U/api/versions/$V1/send)"
echo "images v1:  $(curl -sf -X POST -H "$K" $U/api/versions/$V1/images | j '[(i["shot"],i["result"]) for i in d]')"
sleep 8
V2=$(curl -sf -X POST -H "$K" -H 'content-type: application/json' -d '{"designer_note":"Bar-tack pocket corners"}' $U/api/styles/$SID/versions | j 'd["id"]')
echo "approve v2: $(curl -sf -X POST -H "$K" $U/api/versions/$V2/approve)"
echo "images v2:  $(curl -sf -X POST -H "$K" $U/api/versions/$V2/images | j '[(i["shot"],i["result"]) for i in d]')"
V3=$(curl -sf -X POST -H "$K" -H 'content-type: application/json' -d '{"fabric":"linen"}' $U/api/styles/$SID/versions | j 'd["id"]')
echo "approve v3: $(curl -sf -X POST -H "$K" $U/api/versions/$V3/approve)"
curl -sf -H "$K" $U/api/styles/$SID | python3 -c '
import sys,json;d=json.load(sys.stdin)
for r in d["requests"]: print(" ", r["supplier_code"], "v%s"%r["version"], r["status"], "tries=%s"%r["attempts"], "stale" if r["stale"] else "fresh", r["stale_reason"] or "", (r["quote"] or {}).get("unit_price_cents"))
print("  budget", d["budget"])'
curl -sf -H "$K" $U/api/styles/$SID/timeline | python3 -c '
import sys,json;d=json.load(sys.stdin);print("  sketch -> first quote: %ss" % d["sketch_to_first_quote_seconds"])'
echo "factory inbox: $(curl -sf $U/sim/suppliers/inbox)"
echo "other brand sees style: HTTP $(curl -s -o /dev/null -w '%{http_code}' -H 'X-API-Key: demo-kestrel' $U/api/styles/$SID)"
