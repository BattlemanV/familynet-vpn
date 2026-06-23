# Architecture

## Project

FamilyNet VPN — self-hosted management panel for a family VPN server supporting three transport variants.

## Runtime Environment

- Ubuntu 24.04 LTS (host)
- Docker (one or more containers depending on variant)
- FastAPI + Uvicorn
- PWA frontend (vanilla JS)

---

## Installation Variants

A user chooses one of three variants during install. Each shares the same FamilyNet management panel but uses a different VPN transport.

| # | Variant | Speed | Obfuscation | Docker stack | Ports |
|---|---------|-------|-------------|--------------|-------|
| 1 | **wireguard** | high | none | `wg-vpn` (single) | 51820/udp |
| 2 | **amnezia-wg** | medium | strong (AWG) | `wg-vpn` + `amnezia-awg` | 31121/udp |
| 3 | **amnezia-xray** | lower | max (VLESS+XTLS) | `wg-vpn` + `amnezia-xray` | 8443/tcp |

### Variant 1 — WireGuard (current)

Straightforward WireGuard. No traffic obfuscation. Maximum throughput, minimal CPU overhead. Best for unrestricted regions or when raw speed is the priority.

### Variant 2 — AmneziaWG

Standard WireGuard tunnel wrapped inside AmneziaWG. The inner WG packet is encapsulated with an obfuscation layer that makes it indistinguishable from random UDP traffic. Good balance of speed and DPI bypass. Recommended for moderate censorship environments.

### Variant 3 — Xray

WireGuard traffic is routed through an Xray proxy (VLESS + XTLS Vision + TCP). The outer layer looks like a standard TLS connection. Highest bypass capability at the cost of reduced throughput and higher latency. Recommended for heavy censorship / China.

### Install flow

```
curl -fsSL https://raw.githubusercontent.com/BattlemanV/familynet-vpn/main/install.sh | bash
    │
    ├── [1] WireGuard      → docker-compose.wg.yml   + entrypoint-wg.sh
    ├── [2] AmneziaWG      → docker-compose.awg.yml  + entrypoint-awg.sh
    └── [3] Xray           → docker-compose.xray.yml + entrypoint-xray.sh
```

All variants share:
- `app.py` / `web/` — identical management panel
- `/data` volume — same JSON/SQLite format
- `api_token` — same auth mechanism

### Network topology

**Variant 1 (WireGuard):**
```
Client ── WG ──► wg0 (10.8.0.1) ──► Internet
```

**Variant 2 (AmneziaWG):**
```
Client ── AWG ──► amnezia-awg ──┬──► wg0 (10.8.0.1) ──► Internet
                                └──► FastAPI :8000
```

**Variant 3 (Xray):**
```
Client ── Xray ──► amnezia-xray(:8443) ──┬──► wg0 (10.8.0.1) ──► Internet
                                          └──► FastAPI :8000
```

In variants 2 and 3, the admin panel is still reached via WireGuard IP (10.8.0.1:8000). The external-facing service (AWG or Xray) proxies only client traffic; admin access remains through the internal WireGuard interface.

---

## High-Level Architecture

```
Internet
    │
    ▼
Ubuntu VPS
    │
    └── Docker (wg-vpn container)
        │
        ├── WireGuard (wg0, 10.8.0.1/24)
        ├── FastAPI (10.8.0.1:8000)
        ├── /data (persistent volume)
        │   ├── clients.json
        │   ├── settings.json
        │   ├── speed_limits.json
        │   ├── traffic_stats.sqlite
        │   ├── activity.log
        │   ├── api_token (read-only bind mount)
        │   └── backups/
        ├── /app/app.py (bind mount)
        └── /app/web/ (bind mount)
```

---

## Main Components

### Frontend

Files:
- `web/index.html` — PWA entry point
- `web/app.js` — all screens, i18n (7 languages inline), API client, UI
- `web/app.css` — all styles (single file)

Responsibilities: Dashboard, client management, traffic stats, activity log, settings, backups, QR codes, avatars, parental control, maintenance.

### Backend

File:
- `app.py` — FastAPI app, ~2750 lines, single file

Responsibilities: REST API (40+ endpoints), auth middleware (VPN-first), WireGuard integration (wg-quick, wg set, iptables), client lifecycle, speed limits (tc), traffic stats (SQLite, background collector), backup/restore (tar.gz, atomic writes), parental control, avatars, global exception handler.

Support files:
- `entrypoint.sh` — container entrypoint: WireGuard up → uvicorn
- `Dockerfile` — python:3.11-slim, wireguard-tools + curl + iptables, HEALTHCHECK
- `init_config.py` — initial config generator
- `requirements.txt` — Python deps

---

## Key Design Decisions

### Atomic JSON writes
`atomic_json_write(path, data)` — writes to `.tmp`, then `os.replace()`. Used for all JSON files. No `.bak` copies (frequent writes would flood disk).

### Utilities
- `get_default_interface()` — `ip route get 8.8.8.8` → dev name, fallback `WG_EXTERNAL_IFACE`
- `_acquire_backup_lock()` — file lock with stale protection (600s)
- `validate_rate()` — strict regex `^\d+(kbit|mbit|gbit|kbps|mbps|gbps)$`
- `_get_hostname()` — `SERVER_HOSTNAME` env → `socket.gethostname()`
- `NAME_RE` — client name validation: `^[\w\s\-\.а-яА-ЯёЁ]+$`

### HEALTHCHECK
`curl -f http://10.8.0.1:8000/health`, interval 30s, start-period 15s. Container shows `(healthy)` after successful startup.

### Speed limits (tc)
Symmetrical (same rate for upload/download). Parental control uses separate class `1:30`. Manual override (slow/disable) takes priority over parental rules.

---

## Security

### VPN-First Admin Model
- Uvicorn binds to `ADMIN_BIND_HOST` env (set to `10.8.0.1` by install.sh)
- Middleware: admin peer IP (10.8.0.x) → access without token
- `127.0.0.1`, `::1`, `10.8.0.1` always allowed
- Recovery token (`X-API-Token` / `?token=`) — emergency/developer only
- Host port: loopback only (`-p 127.0.0.1:8000:8000`)

### Sensitive files (never commit)
`api_token`, `*.json` runtime files, `*.sqlite`, `*.log`, `*.wgadmin`, `*.tar.gz`

---

## Data Flows

### Create Client
Frontend → `POST /peer/create` → generate keys → update clients.json → apply peer → refresh UI

### Delete Client
Frontend → `DELETE /peer/{id}` → remove from wg → update clients.json → redirect to devices

### Speed Limit
Frontend → `POST /peer/{id}/speed-limit` → validate_rate → tc class replace → save speed_limits.json

### Backup
- Manual: `POST /backup/create` → tar.gz → `/data/backups/`
- Auto: on every clients.json change (lock-protected)
- Upload: `POST /backup/upload` → max 100MB → extract

### Restore
`POST /backup/restore/{kind}` → safety snapshot → extract → validate → restart WireGuard
