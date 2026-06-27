#!/bin/bash
set -e

for cmd in wg iptables curl python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "FATAL: $cmd is required but not installed." >&2
        exit 1
    fi
done

DATA_DIR="/data"
CLIENTS_FILE="$DATA_DIR/clients.json"
WG_CONF="$DATA_DIR/wg0.conf"

AWG_PORT="${AWG_PORT:-31121}"

resolve_external_iface() {
    local iface
    iface=$(ip route get 8.8.8.8 2>/dev/null | head -1 | awk '{for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit }}')
    if [ -z "$iface" ] || ! ip link show "$iface" >/dev/null 2>&1; then
        iface="eth0"
    fi
    echo "$iface"
}

mkdir -p "$DATA_DIR/backups"

if [ ! -f "$DATA_DIR/api_token" ]; then
    echo "" > "$DATA_DIR/api_token"
fi

if [ ! -f "$WG_CONF" ]; then
    echo "Generating wg0.conf..."
    python3 /opt/wg-admin/init_config.py
fi

if [ -f "$WG_CONF" ]; then
    chmod 600 "$WG_CONF"
    sed -i "s/ListenPort = 51820/ListenPort = $AWG_PORT/" "$WG_CONF" 2>/dev/null || true
fi

wg-quick up "$WG_CONF"

awg set wg0 jc 4 jmin 10 jmax 50 s1 97 s2 99

EXTERNAL_IFACE=$(resolve_external_iface)
iptables -C FORWARD -i wg0 -j ACCEPT 2>/dev/null || iptables -A FORWARD -i wg0 -j ACCEPT
iptables -C FORWARD -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -C POSTROUTING -o "${EXTERNAL_IFACE}" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "${EXTERNAL_IFACE}" -j MASQUERADE

exec uvicorn app:app --host "${ADMIN_BIND_HOST:-0.0.0.0}" --port 8000
