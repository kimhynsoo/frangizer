#!/usr/bin/env bash
# Quick integration check against the running stack (docs/api/API_명세서.md).
# usage: ./scripts/smoke.sh [base_url] [username] [password]
BASE="${1:-http://localhost}"
USER_NAME="${2:-demo-owner-a001}"
PASSWORD="${3:-demo-password}"
API="$BASE/api/v1"
fail=0

check() {  # name expected actual
  if [ "$2" = "$3" ]; then echo "PASS  $1 ($3)"; else echo "FAIL  $1 (expected $2, got $3)"; fail=1; fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

check "web health" 200 "$(code "$BASE/healthz")"
check "api health" 200 "$(code "$API/health")"
check "me without token" 401 "$(code "$API/me")"

login="$(curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER_NAME\",\"password\":\"$PASSWORD\"}" "$API/login")"
token="$(printf '%s' "$login" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("accessToken",""))' 2>/dev/null)"
check "login returns accessToken" yes "$([ -n "$token" ] && echo yes || echo no)"

if [ -n "$token" ]; then
  check "me with token" 200 "$(code -H "Authorization: Bearer $token" "$API/me")"
  check "rag-query" 200 "$(code -X POST -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
    -H "Idempotency-Key: smoke-$(date +%s)" -d '{"question":"데모 바닐라라테의 제조 순서를 알려주세요."}' "$API/rag-query")"
fi

exit $fail
