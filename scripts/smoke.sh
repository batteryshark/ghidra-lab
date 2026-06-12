#!/usr/bin/env bash
# End-to-end smoke test for the Ghidra Lab stack.
#   scripts/smoke.sh            # build, start, verify
#   scripts/smoke.sh --no-build # verify an already-built stack
set -euo pipefail

cd "$(dirname "$0")/.."

BUILD=1
[[ "${1:-}" == "--no-build" ]] && BUILD=0

# Read the few values we need without executing .env (values may contain spaces).
get_env() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }
HOST_PORT="$(get_env GHIDRA_LAB_LOCAL_PORT)"; HOST_PORT="${HOST_PORT:-18080}"
AGENT="$(get_env GHIDRA_AGENT_SERVER_USER)"; AGENT="${AGENT:-agent}"
REPO="$(get_env GHIDRA_LAB_DEFAULT_REPOSITORY)"; REPO="${REPO:-GhidraLab}"
SRV_HOST="$(get_env GHIDRA_AGENT_SERVER_HOST)"; SRV_HOST="${SRV_HOST:-ghidra-server}"
SRV_PORT="$(get_env GHIDRA_AGENT_SERVER_PORT)"; SRV_PORT="${SRV_PORT:-13100}"

say()  { printf '\n=== %s ===\n' "$*"; }
fail() { printf '\nSMOKE FAILED: %s\n' "$*" >&2; exit 1; }

say "Docker daemon"
docker info >/dev/null 2>&1 || fail "docker daemon not reachable"

if [[ "${BUILD}" == "1" ]]; then
    say "Build"
    docker compose build
fi

say "Up"
docker compose up -d

say "Waiting for both services to report healthy (up to 10 min)"
deadline=$(( $(date +%s) + 600 ))
while :; do
    lab=$(docker inspect -f '{{.State.Health.Status}}' ghidra-lab-ghidra-lab-1 2>/dev/null || echo none)
    srv=$(docker inspect -f '{{.State.Health.Status}}' ghidra-lab-ghidra-server-1 2>/dev/null || echo none)
    printf '  ghidra-server=%s  ghidra-lab=%s\n' "${srv}" "${lab}"
    [[ "${lab}" == "healthy" && "${srv}" == "healthy" ]] && break
    [[ $(date +%s) -gt ${deadline} ]] && fail "services did not become healthy in time"
    sleep 10
done

say "Agent account exists on the Ghidra Server"
docker compose exec -T ghidra-server cat /data/repositories/users 2>/dev/null \
    | grep -q "^${AGENT}:" || fail "agent account '${AGENT}' not found (see ghidra-server logs)"
echo "  ok: ${AGENT}"

say "Repository '${REPO}' present on the Ghidra Server"
docker compose exec -T ghidra-lab curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "{\"host\":\"${SRV_HOST}\",\"port\":${SRV_PORT}}" \
    "http://127.0.0.1:8089/server/connect" >/dev/null || fail "headless server could not connect to ${SRV_HOST}:${SRV_PORT}"
docker compose exec -T ghidra-lab curl -fsS "http://127.0.0.1:8089/server/repositories" \
    | grep -q "${REPO}" || fail "repository '${REPO}' not listed by the headless server"
echo "  ok: ${REPO}"

say "Host auth surface"
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${HOST_PORT}/healthz")
[[ "${code}" == "200" ]] || fail "/healthz returned ${code}"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:${HOST_PORT}/mcp")
[[ "${code}" == "401" ]] || fail "unauthenticated /mcp returned ${code}, expected 401"
echo "  ok: /healthz 200, unauthenticated /mcp 401"

say "In-container MCP end-to-end"
docker compose exec -T ghidra-lab python -m ghidra_lab_mcp.smoke || fail "MCP smoke failed"

say "SMOKE PASSED"
