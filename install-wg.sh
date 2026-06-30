#!/bin/bash
set -e

# ── FamilyNet VPN — WireGuard (no-menu variant) ─────────────

REPO="https://github.com/BattlemanV/familynet-vpn.git"
INSTALL_DIR="/root/wg-admin-api"
CONTAINER_NAME="wg-vpn"
IMAGE_NAME="wg-vpn"
DOCKERFILE="Dockerfile"
ENTRYPOINT="entrypoint.sh"
WG_PORT="${WG_PORT:-51820}"
WG_EXTERNAL_PORT="$WG_PORT"
CONTAINER_PORT="51820"
PROTO="udp"
CLIENT_APP="WireGuard"
VARIANT_LABEL="wg"

WG_HOST="${WG_HOST:-$(curl -fsSL ifconfig.me 2>/dev/null || curl -fsSL api.ipify.org 2>/dev/null || dig +short myip.opendns.com @resolver1.opendns.com 2>/dev/null)}"
if [ -z "$WG_HOST" ]; then
    echo "Could not detect public IP automatically."
    exit 1
fi
API_PORT="${API_PORT:-8000}"

BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${CYAN}◆${NC} $1"; }
ok()    { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root." >&2
    exit 1
fi

if ! command -v apt-get &>/dev/null; then
    warn "This installer requires apt-get (Debian/Ubuntu)."
    exit 1
fi

total_ram=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "$total_ram" -gt 0 ] && [ "$total_ram" -lt 524288 ]; then
    warn "Less than 512MB RAM detected."
fi

info "Installing dependencies..."
apt-get update -qq
apt-get install -y -qq curl git qrencode openssl 2>/dev/null || true

if ! command -v docker &>/dev/null; then
    info "Installing Docker..."
    apt-get install -y -qq docker.io 2>/dev/null || true
    systemctl enable --now docker 2>/dev/null || service docker start 2>/dev/null || true
fi

if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR"
    git pull --ff-only origin main
else
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
ok "Repository ready"

TOKEN_FILE="$INSTALL_DIR/api_token"
if [ ! -f "$TOKEN_FILE" ] || [ ! -s "$TOKEN_FILE" ]; then
    echo ""
    echo "  Create admin password."
    echo ""
    while :; do
        read -s -p "Enter admin password: " ADMIN_PASS
        echo ""
        if [ -z "$ADMIN_PASS" ]; then
            echo "Password cannot be empty."
            continue
        fi
        read -s -p "Confirm password: " ADMIN_PASS2
        echo ""
        if [ "$ADMIN_PASS" != "$ADMIN_PASS2" ]; then
            echo "Passwords do not match."
            continue
        fi
        break
    done
    echo "$ADMIN_PASS" > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
fi

API_TOKEN=$(cat "$TOKEN_FILE")
cp "$INSTALL_DIR/$ENTRYPOINT" "$INSTALL_DIR/entrypoint.sh"

info "Building Docker image..."
docker build -t "$IMAGE_NAME" "$INSTALL_DIR"
ok "Image built"

docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

info "Starting container..."
docker run -d \
    --name "$CONTAINER_NAME" \
    --hostname "$(hostname)" \
    --cap-add NET_ADMIN \
    --cap-add SYS_MODULE \
    -p "$WG_EXTERNAL_PORT":"$CONTAINER_PORT"/"$PROTO" \
    -p 127.0.0.1:"$API_PORT":8000 \
    -v "$INSTALL_DIR/app.py:/app/app.py" \
    -v "$INSTALL_DIR/web:/app/web" \
    -v "$TOKEN_FILE:/data/api_token:ro" \
    -v wg-vpn-data:/data \
    -e WG_HOST="$WG_HOST" \
    -e WG_PORT="$WG_PORT" \
    -e SERVER_HOSTNAME="$(hostname)" \
    -e WG_VARIANT="$VARIANT_LABEL" \
    -e WG_INSIDE_CONTAINER=1 \
    -e ADMIN_BIND_HOST=10.8.0.1 \
    --restart unless-stopped \
    "$IMAGE_NAME"
ok "Container started"

info "Waiting for API..."
for i in $(seq 1 30); do
    if docker exec "$CONTAINER_NAME" curl -sf -H "X-API-Token: $API_TOKEN" "http://10.8.0.1:8000/health" >/dev/null 2>&1; then
        ok "API is ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        warn "API not ready. Check 'docker logs $CONTAINER_NAME'"
        exit 1
    fi
    printf "."
    sleep 2
done

PEER_NAME="${PEER_NAME:-Admin}"
EXISTING=$(docker exec "$CONTAINER_NAME" curl -sf -H "X-API-Token: $API_TOKEN" "http://10.8.0.1:8000/peers" | PEER_NAME="$PEER_NAME" python3 -c "
import os, sys, json
target = os.environ['PEER_NAME']
d = json.load(sys.stdin)
for p in d.get('peers', []):
    if p.get('name') == target:
        print(p.get('client_id', ''))
        break
" 2>/dev/null || echo "")

if [ -n "$EXISTING" ]; then
    ok "Peer '$PEER_NAME' already exists"
    CLIENT_ID="$EXISTING"
else
    info "Creating admin user..."
    CREATE=$(docker exec -e PEER_NAME="$PEER_NAME" "$CONTAINER_NAME" sh -c "
        curl -sf -X POST -H 'X-API-Token: $API_TOKEN' \
            -H 'Content-Type: application/json' \
            -d \"\$(python3 -c 'import os,json; print(json.dumps({\"name\":os.environ[\"PEER_NAME\"]}))')\" \
            'http://10.8.0.1:8000/peer/create'" 2>/dev/null || echo "")
    CLIENT_ID=$(echo "$CREATE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('client_id', ''))
except: print('')
" 2>/dev/null || echo "")
    if [ -n "$CLIENT_ID" ]; then
        ok "Peer created"
        docker exec "$CONTAINER_NAME" curl -sf -X POST -H "X-API-Token: $API_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"role":"admin"}' \
            "http://10.8.0.1:8000/peer/$CLIENT_ID/role" >/dev/null 2>&1 || true
    fi
fi

docker exec "$CONTAINER_NAME" curl -sf -X POST -H "X-API-Token: $API_TOKEN" \
    "http://10.8.0.1:8000/peer/$CLIENT_ID/enable" >/dev/null 2>&1 || true

echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║        FamilyNet VPN is ready!                  ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Server:  $WG_HOST:$WG_PORT/$PROTO"
echo -e "  Panel:   http://$WG_HOST:$API_PORT  (via VPN)"
echo ""

if command -v qrencode &>/dev/null; then
    QR_DATA=$(docker exec "$CONTAINER_NAME" curl -sf -H "X-API-Token: $API_TOKEN" \
        "http://10.8.0.1:8000/peer/$CLIENT_ID/config" 2>/dev/null || echo "")
    if [ -n "$QR_DATA" ]; then
        echo "$QR_DATA" | qrencode -t ANSI256UTF8 2>/dev/null || \
        echo "$QR_DATA" | qrencode -t UTF8 2>/dev/null || true
    fi
fi

echo ""
echo "Admin password saved at: $TOKEN_FILE"
echo "Need help? https://github.com/BattlemanV/familynet-vpn"