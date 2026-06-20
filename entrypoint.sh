#!/bin/bash
set -e

# ── Prerequisites ────────────────────────────────────────────
for cmd in wg iptables curl python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "FATAL: $cmd is required but not installed." >&2
        exit 1
    fi
done

DATA_DIR="/data"
CLIENTS_FILE="$DATA_DIR/clients.json"
WG_CONF="$DATA_DIR/wg0.conf"

resolve_external_iface() {
    local iface
    iface=$(ip route get 8.8.8.8 2>/dev/null | head -1 | awk '{for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit }}')
    if [ -z "$iface" ] || ! ip link show "$iface" >/dev/null 2>&1; then
        iface="eth0"
    fi
    echo "$iface"
}

# Create data dir if missing
mkdir -p "$DATA_DIR/backups"

# ── Migration from wg-easy ──────────────────────────────────
if [ -f /root/.wg-easy/wg0.json ] && [ ! -f "$CLIENTS_FILE" ]; then
    echo "Migrating clients from wg-easy..."
    cp /root/.wg-easy/wg0.json "$CLIENTS_FILE"
    mv /root/.wg-easy/wg0.json /root/.wg-easy/wg0.json.migrated
fi

# Ensure api_token exists
if [ ! -f "$DATA_DIR/api_token" ]; then
    echo "" > "$DATA_DIR/api_token"
fi

# ── Generate wg0.conf if missing ────────────────────────────
if [ ! -f "$WG_CONF" ]; then
    echo "Generating wg0.conf..."
    python3 /app/init_config.py
fi

if [ -f "$WG_CONF" ]; then
    chmod 600 "$WG_CONF"
fi

# ── Start WireGuard ─────────────────────────────────────────
wg-quick up "$WG_CONF"

# ── Ensure iptables rules for wg0 forwarding ───────────────
# Docker bridge networking can clear PostUp rules, so re-add them
EXTERNAL_IFACE=$(resolve_external_iface)
iptables -C FORWARD -i wg0 -j ACCEPT 2>/dev/null || iptables -A FORWARD -i wg0 -j ACCEPT
iptables -C FORWARD -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -C POSTROUTING -o "${EXTERNAL_IFACE}" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "${EXTERNAL_IFACE}" -j MASQUERADE

# ── Start API ───────────────────────────────────────────────
exec uvicorn app:app --host "${ADMIN_BIND_HOST:-0.0.0.0}" --port 8000