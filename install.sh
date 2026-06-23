#!/bin/bash
set -e

# ── FamilyNet VPN — one-command installer ─────────────────────
# Usage: bash <(curl -fsSL https://raw.githubusercontent.com/BattlemanV/familynet-vpn/main/install.sh)
#
# What it does:
#   1. Installs Docker
#   2. Clones the repo
#   3. Prompts for admin password
#   4. Builds & starts the container
#   5. Creates the first admin user (protected)
#   6. Shows QR code for mobile config

REPO="https://github.com/BattlemanV/familynet-vpn.git"
INSTALL_DIR="/root/wg-admin-api"

# ── Variant selection ────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}Select VPN transport variant:${NC}"
echo ""
echo -e "  ${BOLD}[1]${NC} WireGuard      — ${GREEN}fastest${NC}, no obfuscation (port 51820/udp)"
echo -e "  ${BOLD}[2]${NC} AmneziaWG      — ${YELLOW}balanced${NC}, traffic obfuscation (port 31121/udp)"
echo -e "  ${BOLD}[3]${NC} Xray           — ${RED}max protection${NC}, TLS tunnel (port 8443/tcp)"
echo ""

read -p "$(echo -e "${CYAN}⌨${NC} Choose [1/2/3] (default: 1): ")" VARIANT
VARIANT="${VARIANT:-1}"

case "$VARIANT" in
  2)
    info "Selected: AmneziaWG"
    CONTAINER_NAME="wg-vpn-awg"
    IMAGE_NAME="wg-vpn-awg"
    DOCKERFILE="Dockerfile.awg"
    ENTRYPOINT="entrypoint-awg.sh"
    WG_PORT="${WG_PORT:-31121}"
    WG_EXTERNAL_PORT="$WG_PORT"
    CONTAINER_PORT="$WG_PORT"
    PROTO="udp"
    CLIENT_APP="AmneziaWG"
    ;;
  3)
    info "Selected: Xray"
    CONTAINER_NAME="wg-vpn-xray"
    IMAGE_NAME="wg-vpn-xray"
    DOCKERFILE="Dockerfile.xray"
    ENTRYPOINT="entrypoint-xray.sh"
    WG_PORT="${WG_PORT:-8443}"
    WG_EXTERNAL_PORT="8443"
    CONTAINER_PORT="8443"
    PROTO="tcp"
    CLIENT_APP="Xray / V2Ray"
    ;;
  *)
    info "Selected: WireGuard"
    CONTAINER_NAME="wg-vpn"
    IMAGE_NAME="wg-vpn"
    DOCKERFILE="Dockerfile"
    ENTRYPOINT="entrypoint.sh"
    WG_PORT="${WG_PORT:-51820}"
    WG_EXTERNAL_PORT="$WG_PORT"
    CONTAINER_PORT="51820"
    PROTO="udp"
    CLIENT_APP="WireGuard"
    ;;
esac

WG_HOST="${WG_HOST:-$(curl -fsSL ifconfig.me 2>/dev/null || curl -fsSL api.ipify.org 2>/dev/null || dig +short myip.opendns.com @resolver1.opendns.com 2>/dev/null)}"
if [ -z "$WG_HOST" ]; then
    warn "Could not detect public IP automatically."
    echo ""
    read -p "$(echo -e "${CYAN}⌨${NC} Enter your VPS public IP address: ")" WG_HOST
    echo ""
    if [ -z "$WG_HOST" ]; then
        echo -e "  ${YELLOW}No IP provided. Exiting.${NC}"
        echo -e "  ${YELLOW}Set it manually: export WG_HOST=<your-vps-ip> && bash <(curl -fsSL ...)${NC}"
        exit 1
    fi
fi
API_PORT="${API_PORT:-8000}"

# ── Colors ─────────────────────────────────────────────────────
BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${CYAN}◆${NC} $1"; }
ok()    { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }

# ── 0. Prerequisites ──────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root." >&2
    exit 1
fi

# Check OS
if ! command -v apt-get &>/dev/null; then
    warn "This installer requires apt-get (Debian/Ubuntu)."
    warn "See manual install instructions for other distributions."
    exit 1
fi

# Check resources
total_ram=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "$total_ram" -gt 0 ] && [ "$total_ram" -lt 524288 ]; then
    warn "Less than 512MB RAM detected. The panel may run slowly."
fi
root_free=$(df /root 2>/dev/null | awk 'NR==2{print $4}' || echo 0)
if [ "$root_free" -gt 0 ] && [ "$root_free" -lt 1048576 ]; then
    warn "Less than 1GB free disk space. Backups may fail."
fi

# ── 1. Install dependencies ──────────────────────────────────
info "Ensuring required packages..."
apt-get update -qq
apt-get install -y -qq curl git qrencode openssl 2>/dev/null || true

if ! command -v docker &>/dev/null; then
    info "Installing Docker..."
    apt-get install -y -qq docker.io 2>/dev/null || true
    if command -v systemctl &>/dev/null; then
        systemctl enable --now docker
    else
        service docker start 2>/dev/null || true
    fi
    ok "Docker installed"
else
    ok "Docker already installed"
fi

# ── 2. Clone / update repo ─────────────────────────────────────
if [ -d "$INSTALL_DIR" ]; then
    info "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only origin main
else
    info "Cloning repo..."
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
ok "Repository ready at $INSTALL_DIR"

# ── 3. Create admin password ─────────────────────────────────────
TOKEN_FILE="$INSTALL_DIR/api_token"
if [ ! -f "$TOKEN_FILE" ] || [ ! -s "$TOKEN_FILE" ]; then
    echo ""
    echo -e "  ${CYAN}Create an admin password for the web panel.${NC}"
    echo -e "  ${YELLOW}It will be used to log into the admin interface.${NC}"
    echo ""
    while :; do
        read -s -p "$(echo -e "${CYAN}⌨${NC} Enter admin password: ")" ADMIN_PASS
        echo ""
        if [ -z "$ADMIN_PASS" ]; then
            echo -e "  ${YELLOW}Password cannot be empty.${NC}"
            continue
        fi
        read -s -p "$(echo -e "${CYAN}⌨${NC} Confirm password: ")" ADMIN_PASS2
        echo ""
        if [ "$ADMIN_PASS" != "$ADMIN_PASS2" ]; then
            echo -e "  ${YELLOW}Passwords do not match. Try again.${NC}"
            continue
        fi
        break
    done
    echo ""
    echo "$ADMIN_PASS" > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    ok "Admin password saved"
else
    ok "Admin password already exists"
fi

API_TOKEN=$(cat "$TOKEN_FILE")

# ── 4. Copy variant entrypoint ──────────────────────────────────
cp "$INSTALL_DIR/$ENTRYPOINT" "$INSTALL_DIR/entrypoint.sh"
ok "Entrypoint ready: $ENTRYPOINT"

# ── 5. Build Docker image ──────────────────────────────────────
info "Building Docker image (first build may take a minute)..."
docker build -t "$IMAGE_NAME" -f "$INSTALL_DIR/$DOCKERFILE" "$INSTALL_DIR"
ok "Docker image built"

# ── 6. Stop & remove old container ─────────────────────────────
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

# ── 7. Run container ───────────────────────────────────────────
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
    -e WG_INSIDE_CONTAINER=1 \
    -e ADMIN_BIND_HOST=10.8.0.1 \
    --restart unless-stopped \
    "$IMAGE_NAME"

ok "Container started"

# ── 8. Wait for API ────────────────────────────────────────────
info "Waiting for API to become ready..."
for i in $(seq 1 30); do
    if docker exec "$CONTAINER_NAME" curl -sf -H "X-API-Token: $API_TOKEN" "http://10.8.0.1:8000/health" >/dev/null 2>&1; then
        ok "API is ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo ""
        warn "API didn't respond in time. Check 'docker logs $CONTAINER_NAME'"
        echo ""
        echo "Admin password saved at: $TOKEN_FILE"
        exit 1
    fi
    printf "."
    sleep 2
done
echo ""

# ── 9. Create first admin user ─────────────────────────────────
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
    ok "Peer '$PEER_NAME' already exists (client_id: $EXISTING)"
    CLIENT_ID="$EXISTING"
else
    info "Creating first admin user '$PEER_NAME'..."
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
        ok "Peer '$PEER_NAME' created (client_id: $CLIENT_ID)"
        # Set admin role
        docker exec "$CONTAINER_NAME" curl -sf -X POST -H "X-API-Token: $API_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"role":"admin"}' \
            "http://10.8.0.1:8000/peer/$CLIENT_ID/role" >/dev/null 2>&1 || true
    else
        warn "Failed to create peer. You can do it manually via the panel."
        echo "Admin password saved at: $TOKEN_FILE"
        echo "Panel (via VPN): http://10.8.0.1:8000"
        exit 0
    fi
fi

# ── 10. Protect the admin user ─────────────────────────────────
# Disable deletion protection for admin (already protected by default in protected_peers)
# Make sure the peer is enabled
docker exec "$CONTAINER_NAME" curl -sf -X POST -H "X-API-Token: $API_TOKEN" \
    "http://10.8.0.1:8000/peer/$CLIENT_ID/enable" >/dev/null 2>&1 || true

# ── 11. Show QR code ──────────────────────────────────────────

echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║        ✅ FamilyNet VPN is ready!                ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Variant:${NC}   $CONTAINER_NAME ($CLIENT_APP)"
echo -e "  ${BOLD}Server:${NC}    $WG_HOST:$WG_PORT/$PROTO"
echo -e "  ${BOLD}Panel:${NC}     http://$WG_HOST:$API_PORT  (via VPN)"
echo -e "  ${BOLD}Admin user:${NC} $PEER_NAME"
echo ""

if command -v qrencode &>/dev/null; then
    echo -e "  ${BOLD}Scan QR in $CLIENT_APP app:${NC}"
    echo ""
    QR_DATA=$(docker exec "$CONTAINER_NAME" curl -sf -H "X-API-Token: $API_TOKEN" \
        "http://10.8.0.1:8000/peer/$CLIENT_ID/config" 2>/dev/null || echo "")
    if [ -n "$QR_DATA" ]; then
        echo "$QR_DATA" | qrencode -t ANSI256UTF8 2>/dev/null || \
        echo "$QR_DATA" | qrencode -t UTF8 2>/dev/null || true
        echo ""
    fi
fi

echo -e "${BOLD}${CYAN}── Next steps ──────────────────────────────────────${NC}"
echo ""
echo -e "  ${BOLD}1.${NC} Scan the QR code above in your $CLIENT_APP app"
echo -e "  ${BOLD}2.${NC} Connect to the VPN"
echo -e "  ${BOLD}3.${NC} Open the admin panel:"
echo -e "     ${CYAN}http://$WG_HOST:$API_PORT${NC}"
echo ""
echo -e "  ${BOLD}Use your admin password${NC} to log into the panel."
echo -e "  You will be prompted the first time you open it."
echo ""
echo -e "${BOLD}${YELLOW}── Admin password ─────────────────────────────────${NC}"
echo ""
echo -e "  ${YELLOW}File:${NC} $TOKEN_FILE"
echo -e "  ${YELLOW}Use:${NC}  X-API-Token header or ?token= query param"
echo ""
echo -e "  ${BOLD}Need help?${NC} https://github.com/BattlemanV/familynet-vpn"
echo ""