#!/usr/bin/env bash
# Interview demo: build and start the stack, then walk the whole API with curl.
#
# Usage:
#   ./scripts/demo.sh             # build, start, run the API walkthrough
#   ./scripts/demo.sh --fresh     # first delete the database volume (all local data!)
#   ./scripts/demo.sh --tests     # also run the test suite in Docker at the end
#
# Every step prints the exact command it runs, so the audience sees real commands,
# not a black box. Names get a random suffix, so the demo never collides with data
# already in the database (names are unique, and a clash would return 409).

set -euo pipefail
cd "$(dirname "$0")/.."

API="${API:-http://127.0.0.1:8000}"
SUFFIX="$RANDOM"
FRESH=false
TESTS=false
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=true ;;
    --tests) TESTS=true ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# Colours only when writing to a terminal, so a redirected log stays clean.
if [ -t 1 ]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; CYAN=$'\e[36m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RED=$'\e[31m'; RESET=$'\e[0m'
else
  BOLD=""; DIM=""; CYAN=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

step() { printf "\n%s━━ %s ━━%s\n" "$BOLD$CYAN" "$1" "$RESET"; }
say()  { printf "%s%s%s\n" "$DIM" "$1" "$RESET"; }
show() { printf "%s\$ %s%s\n" "$YELLOW" "$*" "$RESET"; "$@"; }

# call METHOD PATH [JSON_BODY]
# Prints the curl command, runs it, prints the status code and the pretty-printed body.
# The body is kept in $BODY so the next step can read an id out of it.
call() {
  local method="$1" path="$2" data="${3:-}"
  local args=(-s -X "$method" "$API$path" -w $'\n%{http_code}')
  if [ -n "$data" ]; then
    args+=(-H 'Content-Type: application/json' -d "$data")
    printf "%s\$ curl -X %s %s -H 'Content-Type: application/json' -d '%s'%s\n" "$YELLOW" "$method" "$API$path" "$data" "$RESET"
  else
    printf "%s\$ curl -X %s %s%s\n" "$YELLOW" "$method" "$API$path" "$RESET"
  fi
  local output status
  output="$(curl "${args[@]}")"
  status="${output##*$'\n'}"
  BODY="${output%$'\n'*}"
  local colour="$GREEN"; [ "${status:0:1}" != "2" ] && colour="$RED"
  printf "%sHTTP %s%s\n" "$BOLD$colour" "$status" "$RESET"
  if [ -n "$BODY" ]; then printf '%s' "$BODY" | python3 -m json.tool; fi
}

json_field() { printf '%s' "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }

# ------------------------------------------------------------------------------------------
step "1. The tools"
show docker --version
show docker compose version

if $FRESH; then
  step "2a. Start from zero - delete containers AND the database volume"
  show docker compose down -v
fi

step "2. Build the image and start the stack"
say "One command: builds the API image (runtime stage), starts PostgreSQL, waits for it to be healthy, then starts the API."
show docker compose up -d --build

say "Waiting for the API health check..."
for _ in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' dlp_api 2>/dev/null)" = "healthy" ] && break
  sleep 2
done
show docker compose ps

step "3. Liveness"
call GET /health

step "4. Create two Data Types (inspection rules)"
say "PII fires at 2 occurrences, Finance at 1. 'credit card' shows that a keyword may contain a space."
call POST /api/datatypes "{\"name\":\"PII $SUFFIX\",\"type\":\"keywords\",\"content\":[\"ssn\",\"passport\",\"credit card\"],\"threshold\":2}"
PII_ID="$(json_field id)"
call POST /api/datatypes "{\"name\":\"Finance $SUFFIX\",\"type\":\"keywords\",\"content\":[\"iban\",\"swift\",\"salary\"],\"threshold\":1}"
FIN_ID="$(json_field id)"

step "5. Group them into a Data Set (a policy)"
call POST /api/datasets "{\"name\":\"HR Policy $SUFFIX\",\"data_type_ids\":[\"$PII_ID\",\"$FIN_ID\"]}"
DS_ID="$(json_field id)"
call GET "/api/datasets/$DS_ID"

step "6. Scan - a match"
say "PII: ssn + passport + credit card = 3 >= 2. Finance: salary = 1 >= 1. Both fire (OR)."
call POST /api/scan "{\"text\":\"my SSN, my passport, my credit card and my salary\",\"data_set_id\":\"$DS_ID\"}"

step "7. Scan - whole words only, so no match"
say "'passport' is not inside 'passports', 'ssn' is not inside 'ssnx'. The body is exactly {status: not matched}."
call POST /api/scan "{\"text\":\"passports and ssnx are not whole words\",\"data_set_id\":\"$DS_ID\"}"

step "8. Validation - rejected before any code runs (422)"
say "A threshold of 0 would flag every text."
call POST /api/datatypes "{\"name\":\"Bad $SUFFIX\",\"type\":\"keywords\",\"content\":[\"x\"],\"threshold\":0}"

step "9. Delete policy - a Data Type in use cannot be deleted (409)"
call DELETE "/api/datatypes/$PII_ID"

step "10. Clean up - the Data Set first, then its Data Types"
call DELETE "/api/datasets/$DS_ID"
call DELETE "/api/datatypes/$PII_ID"
call DELETE "/api/datatypes/$FIN_ID"

if $TESTS; then
  step "11. The test suite, inside Docker (the same command CI runs)"
  show docker compose run --rm --build test
fi

step "Done"
say "Swagger UI: $API/docs"
