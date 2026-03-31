#!/usr/bin/env bash
# docker-entrypoint.sh — Authenticate with OpenBao and inject Valkey credentials
#
# Simplified entrypoint for the poppy-retrieval sidecar.
# Authenticates with OpenBao via AppRole, reads the Valkey password,
# builds VALKEY_URL, and exec's the application.
#
# If vault is unavailable, the sidecar starts WITHOUT Valkey caching
# (graceful degradation — the app handles missing VALKEY_URL).
#
# Required environment variables (for vault mode):
#   VAULT_ADDR       — OpenBao address (e.g., http://poppy-openbao:8200)
#   VAULT_ROLE_ID    — AppRole role_id
#   VAULT_SECRET_ID  — AppRole secret_id
#
# Note: Make this file executable: chmod +x services/retrieval/docker-entrypoint.sh

set -euo pipefail

LOG_PREFIX="[retrieval-entrypoint]"

log() {
    echo "$LOG_PREFIX $*"
}

err() {
    echo "$LOG_PREFIX ERROR: $*" >&2
}

warn() {
    echo "$LOG_PREFIX WARN: $*" >&2
}

# --- Check if vault credentials are provided ---

if [ -z "${VAULT_ADDR:-}" ] || [ -z "${VAULT_ROLE_ID:-}" ] || [ -z "${VAULT_SECRET_ID:-}" ]; then
    warn "Vault credentials not fully configured (VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID)."
    warn "Starting without vault — Valkey caching will use VALKEY_URL from environment (if set)."
    exec "$@"
fi

VAULT_WAIT_TIMEOUT="${VAULT_WAIT_TIMEOUT:-15}"

# --- Wait for vault to become reachable ---

log "Waiting for OpenBao at $VAULT_ADDR ..."
elapsed=0
while [ "$elapsed" -lt "$VAULT_WAIT_TIMEOUT" ]; do
    if curl -sf "$VAULT_ADDR/v1/sys/health" -o /dev/null 2>/dev/null || \
       curl -sf "$VAULT_ADDR/v1/sys/seal-status" -o /dev/null 2>/dev/null; then
        break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done

if [ "$elapsed" -ge "$VAULT_WAIT_TIMEOUT" ]; then
    warn "OpenBao not reachable at $VAULT_ADDR after ${VAULT_WAIT_TIMEOUT}s."
    warn "Starting without vault — Valkey caching will be unavailable."
    exec "$@"
fi

log "OpenBao is reachable."

# --- Check vault is unsealed ---

SEAL_STATUS=$(curl -sf "$VAULT_ADDR/v1/sys/seal-status" 2>/dev/null) || {
    warn "Failed to query vault seal status. Starting without vault."
    exec "$@"
}

IS_SEALED=$(echo "$SEAL_STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sealed', 'ERROR'))" 2>/dev/null) || IS_SEALED="ERROR"
if [ "$IS_SEALED" = "True" ] || [ "$IS_SEALED" = "ERROR" ]; then
    warn "Vault is sealed or seal status unparseable (sealed=$IS_SEALED). Starting without vault."
    exec "$@"
fi

# --- Authenticate with AppRole ---

log "Authenticating with AppRole ..."

AUTH_RESPONSE=$(curl -s -w "\n%{http_code}" "$VAULT_ADDR/v1/auth/approle/login" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$(python3 -c "
import json, os
print(json.dumps({
    'role_id': os.environ['VAULT_ROLE_ID'],
    'secret_id': os.environ['VAULT_SECRET_ID']
}))
")") || {
    warn "Could not reach vault for AppRole login. Starting without vault."
    exec "$@"
}

HTTP_CODE=$(echo "$AUTH_RESPONSE" | tail -1)
AUTH_BODY=$(echo "$AUTH_RESPONSE" | sed '$d')

if [ "$HTTP_CODE" != "200" ]; then
    warn "AppRole authentication failed (HTTP $HTTP_CODE). Starting without vault."
    exec "$@"
fi

VAULT_TOKEN=$(echo "$AUTH_BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
token = data.get('auth', {}).get('client_token', '')
print(token, end='')
") || {
    warn "Failed to extract client token from auth response. Starting without vault."
    exec "$@"
}

if [ -z "$VAULT_TOKEN" ]; then
    warn "AppRole authentication returned empty token. Starting without vault."
    exec "$@"
fi

log "AppRole authentication successful."

# --- Read Valkey credentials ---

log "Reading Valkey credentials from vault ..."

VALKEY_RESPONSE=$(curl -sf "$VAULT_ADDR/v1/secret/data/infra/valkey" \
    -H "X-Vault-Token: $VAULT_TOKEN" 2>/dev/null) || {
    warn "Failed to read secret/infra/valkey. Starting without Valkey cache."
    exec "$@"
}

VALKEY_CREDS=$(echo "$VALKEY_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin).get('data', {}).get('data', {})
print(json.dumps({
    'password': data.get('password', ''),
    'host': data.get('host', 'poppy-valkey'),
    'port': data.get('port', '6379'),
}))
" 2>/dev/null) || {
    warn "Failed to parse Valkey credentials. Starting without Valkey cache."
    exec "$@"
}

VALKEY_PASSWORD=$(echo "$VALKEY_CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")
VALKEY_HOST=$(echo "$VALKEY_CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['host'])")
VALKEY_PORT=$(echo "$VALKEY_CREDS" | python3 -c "import sys,json; print(json.load(sys.stdin)['port'])")

# Build Valkey URL (DB 4 for retrieval sidecar)
if [ -n "$VALKEY_PASSWORD" ]; then
    export VALKEY_URL="redis://:${VALKEY_PASSWORD}@${VALKEY_HOST}:${VALKEY_PORT}/4"
else
    export VALKEY_URL="redis://${VALKEY_HOST}:${VALKEY_PORT}/4"
fi

log "Valkey credentials injected (host=$VALKEY_HOST, port=$VALKEY_PORT, db=4)."

# --- Execute the application command ---

log "Starting application: $*"
exec "$@"
