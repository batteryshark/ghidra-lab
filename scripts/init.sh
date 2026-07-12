#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

fail() { printf 'INIT FAILED: %s\n' "$*" >&2; exit 1; }

[[ ! -e .env ]] || fail ".env already exists; refusing to overwrite it"
[[ ! -e .ghidra-agent-password ]] \
    || fail ".ghidra-agent-password already exists; refusing to overwrite it"
command -v openssl >/dev/null 2>&1 || fail "openssl is required"

umask 077
bearer="$(openssl rand -hex 32)"
awk -v bearer="${bearer}" '
    /^GHIDRA_LAB_TOKEN=/ { print "GHIDRA_LAB_TOKEN=" bearer; next }
    { print }
' .env.example > .env
openssl rand -hex 24 > .ghidra-agent-password

printf 'Created .env and .ghidra-agent-password.\n'
