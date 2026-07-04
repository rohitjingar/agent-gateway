#!/usr/bin/env bash
# End-to-end walkthrough against a running gateway (default: docker compose stack).
#   docker compose up --build -d && ./scripts/demo.sh
set -euo pipefail

GW="${GATEWAY_URL:-http://localhost:8000}"
say() { printf "\n\033[1;36m== %s ==\033[0m\n" "$1"; }
pyget() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)"; }
tok() {
  curl -s -X POST "$GW/auth/token" -H 'content-type: application/json' \
    -d "{\"subject\":\"$1\",\"role\":\"$2\"}" | pyget "d['access_token']"
}
code() { # method url token [json]
  curl -s -o /tmp/agw_body.json -w "%{http_code}" -X "$1" "$GW$2" \
    -H "Authorization: Bearer $3" -H 'content-type: application/json' ${4:+-d "$4"}
}

say "health"; curl -s "$GW/health"; echo

DEV=$(tok dev developer); RO=$(tok ro readonly); ADMIN=$(tok boss admin)
echo "minted tokens for: developer, readonly, admin"

say "RBAC: readonly sees only read-only tools"
curl -s "$GW/tools" -H "Authorization: Bearer $RO" | pyget "[t['name'] for t in d]"

say "RBAC: readonly is denied a write/delete (expect 403)"
echo "delete_branch as readonly -> HTTP $(code POST /tools/call "$RO" '{"name":"github.delete_branch","arguments":{"name":"develop"}}')"

say "developer writes then reads a file (normal, low-risk call)"
code POST /tools/call "$DEV" '{"name":"files.write_file","arguments":{"path":"hello.txt","content":"hi from the demo"}}' >/dev/null
code POST /tools/call "$DEV" '{"name":"files.read_file","arguments":{"path":"hello.txt"}}' >/dev/null
cat /tmp/agw_body.json | pyget "d['content'][0]['text']"

say "HIGH-RISK: developer asks to delete a branch -> queued (202), NOT executed"
echo "HTTP $(code POST /tools/call "$DEV" '{"name":"github.delete_branch","arguments":{"name":"develop"}}')"
AID=$(pyget "d['approval_id']" < /tmp/agw_body.json)
echo "approval id: $AID"

say "admin sees the pending queue"
curl -s "$GW/approvals?status=pending" -H "Authorization: Bearer $ADMIN" | pyget "[(a['id'][:8],a['tool'],a['status']) for a in d]"

say "admin APPROVES -> tool executes exactly once"
code POST "/approvals/$AID/approve" "$ADMIN" >/dev/null
cat /tmp/agw_body.json | pyget "('status='+d['status'], 'outcome='+str(d['outcome']), d['result']['structured'])"

say "branches now (develop gone)"
code POST /tools/call "$DEV" '{"name":"github.list_branches","arguments":{}}' >/dev/null
cat /tmp/agw_body.json | pyget "d['structured']['result']"

say "audit trail (admin)"
curl -s "$GW/audit/recent?limit=6" -H "Authorization: Bearer $ADMIN" \
  | pyget "[(r['role'],r['tool'],r['outcome']) for r in d]"

printf "\nTraces: open http://localhost:16686 (service: agent-gateway)\n"
