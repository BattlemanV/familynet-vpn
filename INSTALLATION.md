# Installation

## First-Time Setup

The installer creates one administrator VPN profile — no separate login required.

1. Run the one-command installer
2. Scan the WireGuard QR code from the terminal output
3. Connect to the VPN
4. Open `http://10.8.0.1:8000/`

The panel opens immediately — **no password or token required**. Only admin VPN peers can access it. The recovery token is for SSH/developer access only, not for login.

> **Security note:** The installer sets `ADMIN_BIND_HOST=10.8.0.1`,
> which makes the API listen only on the WireGuard interface.
> The host port is mapped to loopback only (`-p 127.0.0.1:8000:8000`).
> The panel cannot be reached without an active VPN connection.

### One-Command Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/BattlemanV/familynet-vpn/main/install.sh)
```

The script will:
- Install Docker if missing
- Clone the repository
- Build and start the container
- Create the first admin user
- Display QR code and panel URL

### Recovery Token

Generated at `/root/wg-admin-api/api_token` during installation.
Not shown on screen — only the file path is displayed.
Used for:
- emergency recovery
- SSH maintenance
- developer access

Not needed for normal operation — only when VPN access is unavailable.
