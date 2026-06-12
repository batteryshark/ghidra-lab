#!/usr/bin/env bash
set -euo pipefail

mkdir -p /data/repositories /data/server /data/logs

CONF="/opt/ghidra/server/server.conf"
TEMPLATE="/data/server/server.conf.template"

if [[ ! -f "${TEMPLATE}" ]]; then
    cp "${CONF}" "${TEMPLATE}"
fi
cp "${TEMPLATE}" "${CONF}"

sed -i "s|^ghidra.repositories.dir=.*|ghidra.repositories.dir=/data/repositories|" "${CONF}"
sed -i "s|^wrapper.java.maxmemory=.*|wrapper.java.maxmemory=${GHIDRA_SERVER_MAX_MEMORY_MB:-2048}|" "${CONF}"
sed -i "/^wrapper.app.parameter\\./d" "${CONF}"

params=()
if [[ -n "${GHIDRA_SERVER_PUBLIC_HOST:-}" ]]; then
    params+=("-ip" "${GHIDRA_SERVER_PUBLIC_HOST}")
    params+=("-ipAlt" "${GHIDRA_SERVER_ALT_NAMES:-${GHIDRA_SERVER_PUBLIC_HOST}}")
fi
params+=("-p${GHIDRA_SERVER_BASE_PORT:-13100}")
params+=("-a0")
params+=("-e0")
params+=("-u")
params+=('${ghidra.repositories.dir}')

index=1
for param in "${params[@]}"; do
    printf 'wrapper.app.parameter.%d=%s\n' "${index}" "${param}" >> "${CONF}"
    index=$((index + 1))
done

# Provision the agent service account once, in the background, after the server
# is accepting connections (mutating svrAdmin commands are queued and applied by
# the running server). Idempotent and non-fatal: a failure here just means the
# agent account must be created manually with the printed command.
provision_agent_account() {
    set +e
    local user="${GHIDRA_AGENT_SERVER_USER:-agent}"
    local pwfile="${GHIDRA_SERVER_PASSWORD_FILE:-/run/secrets/ghidra_agent_password}"
    local port="${GHIDRA_SERVER_BASE_PORT:-13100}"
    [[ -n "${user}" ]] || return 0
    if [[ ! -r "${pwfile}" ]]; then
        echo "agent: no password secret at ${pwfile}; skipping account creation." >&2
        return 0
    fi
    local pw; pw="$(<"${pwfile}")"
    local usersfile="/data/repositories/users"

    local i
    for ((i = 1; i <= 120; i++)); do
        if timeout 1 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/${port}" 2>/dev/null; then
            break
        fi
        sleep 1
    done

    if [[ -f "${usersfile}" ]] && grep -q "^${user}:" "${usersfile}"; then
        echo "agent: account '${user}' already exists."
        return 0
    fi

    echo "agent: creating Ghidra Server account '${user}'..."
    if printf '%s\n%s\n' "${pw}" "${pw}" | /opt/ghidra/server/svrAdmin -add "${user}" --p; then
        sleep 3
        if [[ -f "${usersfile}" ]] && grep -q "^${user}:" "${usersfile}"; then
            echo "agent: account '${user}' created."
        else
            echo "agent: add for '${user}' queued; it will appear once the server processes the command."
        fi
    else
        echo "agent: automatic creation failed. Create it once manually with:" >&2
        echo "  docker compose exec ghidra-server /opt/ghidra/server/svrAdmin -add ${user} --p" >&2
    fi
}
provision_agent_account &

cd /opt/ghidra/server
exec ./ghidraSvr console > /data/logs/ghidra-server.log 2>&1
