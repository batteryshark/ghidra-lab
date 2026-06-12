#!/usr/bin/env bash
set -euo pipefail

mkdir -p /data/uploads /data/projects /data/logs

# Resolve the agent service-account password from its mounted secret so both the
# headless REST server (programmatic /server auth) and the shared-project
# bootstrap can authenticate without the password living in the environment file.
if [[ -z "${GHIDRA_SERVER_PASSWORD:-}" \
      && -n "${GHIDRA_SERVER_PASSWORD_FILE:-}" \
      && -r "${GHIDRA_SERVER_PASSWORD_FILE}" ]]; then
    GHIDRA_SERVER_PASSWORD="$(<"${GHIDRA_SERVER_PASSWORD_FILE}")"
    export GHIDRA_SERVER_PASSWORD
fi

cleanup() {
    if [[ -n "${BRIDGE_PID:-}" ]]; then kill "${BRIDGE_PID}" 2>/dev/null || true; fi
    if [[ -n "${GHIDRA_PID:-}" ]]; then kill "${GHIDRA_PID}" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

build_classpath() {
    local classpath="/app/GhidraMCP.jar"
    local jar
    while IFS= read -r -d '' jar; do
        classpath="${classpath}:${jar}"
    done < <(find /opt/ghidra -name '*.jar' -print0)
    printf '%s' "${classpath}"
}

wait_for_http() {
    local url="$1" name="$2" tries="${3:-120}" i
    for ((i = 1; i <= tries; i++)); do
        if curl -fsS "${url}" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "Timed out waiting for ${name} at ${url}" >&2
    return 1
}

wait_for_tcp() {
    local host="$1" port="$2" name="$3" tries="${4:-120}" i
    for ((i = 1; i <= tries; i++)); do
        if timeout 1 bash -c "cat < /dev/null > /dev/tcp/${host}/${port}" 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    echo "Timed out waiting for ${name} at ${host}:${port}" >&2
    return 1
}

CLASSPATH="$(build_classpath)"

# Creation/reconciliation of the local shared project bound to the server repository.
# Soft-fail by design: if the Ghidra Server is unreachable or the agent account
# is not provisioned yet, the lab still comes up and the sample/upload tools
# work; the repository tools surface diagnostics until the project exists.
bootstrap_shared_project() {
    local repo="${GHIDRA_LAB_DEFAULT_REPOSITORY:-GhidraLab}"
    if [[ -z "${GHIDRA_SERVER_HOST:-}" || -z "${GHIDRA_SERVER_USER:-}" || -z "${GHIDRA_SERVER_PASSWORD:-}" ]]; then
        echo "Ghidra Server identity not fully configured; skipping shared-project bootstrap."
        return 0
    fi
    if ! wait_for_tcp "${GHIDRA_SERVER_HOST}" "${GHIDRA_SERVER_PORT:-13100}" "Ghidra Server" 60; then
        echo "Ghidra Server unreachable; skipping shared-project bootstrap." >&2
        return 0
    fi
    local attempt
    for attempt in 1 2 3 4 5; do
        if java -cp "${CLASSPATH}:/opt/ghidra-lab/bootstrap" BootstrapSharedProject \
                >> /data/logs/bootstrap.log 2>&1; then
            echo "Shared-project bootstrap succeeded (attempt ${attempt})."
            return 0
        fi
        echo "Shared-project bootstrap attempt ${attempt} failed; the agent account may still be provisioning, retrying..." >&2
        sleep 5
    done
    echo "Shared-project bootstrap did not succeed; see /data/logs/bootstrap.log." >&2
    return 0
}
bootstrap_shared_project

# Headless GhidraMCP REST server (loopback only; the facade fronts the network).
java ${JAVA_OPTS:-} \
    -Dghidra.home=/opt/ghidra \
    -Dapplication.name=GhidraMCP \
    -Duser.home=/data \
    -cp "${CLASSPATH}" \
    com.xebyte.headless.GhidraMCPHeadlessServer \
    --bind 127.0.0.1 \
    --port 8089 \
    > /data/logs/ghidra-headless.log 2>&1 &
GHIDRA_PID=$!

wait_for_http "http://127.0.0.1:8089/check_connection" "Ghidra headless REST"

# Upstream MCP bridge, eager (--no-lazy) so the facade's search index sees the
# full upstream tool catalog.
GHIDRA_MCP_URL="http://127.0.0.1:8089" \
    /opt/ghidra-lab/venv/bin/python /opt/ghidra-mcp/bridge_mcp_ghidra.py \
    --transport streamable-http \
    --mcp-host 127.0.0.1 \
    --mcp-port 8081 \
    --no-lazy \
    > /data/logs/ghidra-bridge.log 2>&1 &
BRIDGE_PID=$!

wait_for_tcp "127.0.0.1" "8081" "upstream Ghidra MCP bridge"

exec /opt/ghidra-lab/venv/bin/python -m ghidra_lab_mcp.main
