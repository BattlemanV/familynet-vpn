# Installation

## Quick Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/BattlemanV/familynet-vpn/main/install.sh)
```

The script will:
- Install Docker if missing
- Clone the repository
- Prompt for variant selection (WireGuard / AmneziaWG / Xray)
- Build and start the container
- Create the first admin user
- Display QR code and panel URL

## Variants

During install you choose one of three variants:

| # | Variant | Port | Docker Compose | Dockerfile |
|---|---------|------|----------------|------------|
| 1 | **WireGuard** | 51820/udp | `docker-compose.wg.yml` | `Dockerfile` |
| 2 | **AmneziaWG** | 31121/udp | `docker-compose.awg.yml` | `Dockerfile.awg` |
| 3 | **Xray** | 443/tcp, 8445/tcp, 8444/tcp | `docker-compose.xray.yml` | `Dockerfile.xray` |

All variants run as a **single container** with:
- WireGuard inside (wg0 interface, 10.8.0.1/24)
- FamilyNet management panel (FastAPI + PWA)
- Persistent `/data` volume (configs, traffic DB, backups)

## First-Time Setup

1. Run the one-command installer
2. Scan the QR code from the terminal output (WireGuard config)
3. Connect to the VPN
4. Open `http://10.8.0.1:8000/`

The panel opens immediately — **no password or token required**. Only admin VPN peers can access it.

> **Security note:** The API binds to `10.8.0.1:8000` (WireGuard interface) by default.
> The host port is mapped to loopback (`-p 127.0.0.1:8000:8000` or `127.0.0.1:8082:8000` for Xray).
> The panel cannot be reached without an active VPN connection.

## Recovery Token

Generated at `/root/familynet-vpn/api_token` during install.
Used for emergency recovery, SSH maintenance, or developer access when VPN is unavailable.
