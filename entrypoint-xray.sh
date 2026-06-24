#!/bin/bash
set -e

for cmd in wg iptables curl python3 xray; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "FATAL: $cmd is required but not installed." >&2
        exit 1
    fi
done

DATA_DIR="/data"
CLIENTS_FILE="$DATA_DIR/clients.json"
WG_CONF="$DATA_DIR/wg0.conf"
XRAY_CONF="$DATA_DIR/xray.json"

XRAY_PORT="${XRAY_PORT:-8443}"

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
    python3 init_config.py
fi

if [ -f "$WG_CONF" ]; then
    chmod 600 "$WG_CONF"
fi

wg-quick up "$WG_CONF"

EXTERNAL_IFACE=$(resolve_external_iface)
iptables -C FORWARD -i wg0 -j ACCEPT 2>/dev/null || iptables -A FORWARD -i wg0 -j ACCEPT
iptables -C FORWARD -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -o wg0 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -t nat -C POSTROUTING -o "${EXTERNAL_IFACE}" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "${EXTERNAL_IFACE}" -j MASQUERADE

if [ ! -f "$XRAY_CONF" ]; then
    echo "Generating Xray config..."
    UUID=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c "import uuid; print(uuid.uuid4())")

    CERT_DIR="$DATA_DIR/xray-certs"
    mkdir -p "$CERT_DIR"
    if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
        openssl req -x509 -nodes -days 3650 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
            -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/fullchain.pem" \
            -subj "/CN=familynet-vpn" 2>/dev/null
    fi

    cat > "$XRAY_CONF" <<XRAYEOF
{
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "port": ${XRAY_PORT},
    "protocol": "vless",
    "settings": {
      "clients": [{"id": "${UUID}", "flow": "xtls-rprx-vision"}],
      "decryption": "none"
    },
    "streamSettings": {
      "network": "tcp",
      "security": "tls",
      "tlsSettings": {
        "certificates": [{
          "certificateFile": "${CERT_DIR}/fullchain.pem",
          "keyFile": "${CERT_DIR}/key.pem"
        }]
      }
    },
    "sniffing": {
      "enabled": true,
      "destOverride": ["http", "tls"]
    }
  }],
  "outbounds": [{
    "protocol": "freedom",
    "tag": "direct"
  }]
}
XRAYEOF
    chmod 600 "$XRAY_CONF"
    echo "Xray UUID: $UUID"
fi

nohup xray run -c "$XRAY_CONF" > /tmp/xray.log 2>&1 &
sleep 1

exec uvicorn app:app --host "${ADMIN_BIND_HOST:-0.0.0.0}" --port 8000
