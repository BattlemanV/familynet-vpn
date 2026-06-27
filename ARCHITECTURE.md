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
| 2 | **amnezia-wg** | medium | strong (AWG) | `familynet-vpn-awg` (single) | 31121/udp |
| 3 | **xray** | lower | max (VLESS+REALITY) | `familynet-vpn-xray` (single) | 443/tcp (REALITY), 8445/tcp (XHTTP), 8444/tcp (WS) |

### Variant 1 — WireGuard (current)

Straightforward WireGuard. No traffic obfuscation. Maximum throughput, minimal CPU overhead. Best for unrestricted regions or when raw speed is the priority.

### Variant 2 — AmneziaWG

Standard WireGuard tunnel wrapped inside AmneziaWG. The inner WG packet is encapsulated with an obfuscation layer that makes it indistinguishable from random UDP traffic. Good balance of speed and DPI bypass. Recommended for moderate censorship environments.

### Variant 3 — Xray

Xray-core with three parallel inbounds:
- **REALITY** (port 443) — VLESS + XTLS Vision + REALITY. The outer layer looks like a standard HTTPS connection to `www.microsoft.com`. Maximum bypass capability. Works on Windows, Android, macOS.
- **XHTTP** (port 8445) — VLESS + XHTTP (HTTP/2 cleartext). Fallback for iOS clients (Hiddify) where REALITY is broken.
- **WS** (port 8444) — VLESS + WebSocket. Universal fallback, works with AmneziaVPN and Hiddify on iOS.

### Install flow

```
curl -fsSL https://raw.githubusercontent.com/BattlemanV/familynet-vpn/main/install.sh | bash
    │
    ├── [1] WireGuard      → docker-compose.wg.yml   + entrypoint-wg.sh
    ├── [2] AmneziaWG      → docker-compose.awg.yml  + entrypoint-awg.sh
    └── [3] Xray           → docker-compose.xray.yml + entrypoint-xray.sh
```

All variants share:
- `common.py` — shared backend (auth, backup, tokens, i18n, clients CRUD, system info, settings, activity log, avatars)
- Variant-specific `app.py` (WG/AWG) or `app_xray.py` (Xray) — wireguard/xray logic
- `web/` — identical PWA frontend
- `/data` volume — same JSON/SQLite format
- `api_token` — same auth mechanism

### Network topology

**Variant 1 (WireGuard):**
```
Client ── WG ──► wg0 (10.8.0.1) ──► Internet
```

**Variant 2 (AmneziaWG):**
```
Client ── AWG ──► familynet-vpn-awg ──┬──► wg0 (10.8.0.1) ──► Internet
                                      └──► FastAPI :8000
```

**Variant 3 (Xray):**
```
Client ── REALITY(:443) ──┐
Client ── XHTTP(:8445) ───┤──► familynet-vpn-xray ──┬──► wg0 (10.8.0.1) ──► Internet
Client ── WS(:8444) ──────┘                          └──► FastAPI :8000
```

All three are **separate containers** that can run simultaneously on the same host. Each variant has its own port and can be used independently.

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

### Backend (Shared — common.py)

File:
- `common.py` — pure shared code, ~560 lines, no variant-specific logic

Responsibilities: Auth middleware (VPN-first with X-API-Token), backup/restore (tar.gz, atomic writes, safety snapshots), multi-token management (api_tokens.json), clients JSON CRUD, system info (CPU/RAM/disk/uptime), settings, activity log, avatars, speed limits file I/O, parental rules file I/O, global exception handler.

### Backend (Variants)

| File | Variant | Lines | Responsibilities |
|------|---------|-------|------------------|
| `app.py` | WG / AWG | ~1800 | wg-quick, wg dump/parse, wg set, iptables, traffic DB (SQLite, background collector), speed limits (tc), parental control (schedule + daily bytes), WireGuard config download/QR |
| `app_xray.py` | Xray | ~940 | `sync_xray_config()` (generates xray.json + restarts xray), 3 inbounds (REALITY/WS/XHTTP), per-peer UUID CRUD, VLESS link builder, config download/QR for each protocol, dashboard, diagnostics, stub for traffic history & parental limits |

Support files:
- `Dockerfile` — python:3.11-slim, wireguard-tools + curl + iptables, HEALTHCHECK (WG variant)
- `Dockerfile.awg` — same as Dockerfile + amneziawg-tools (AWG variant)
- `Dockerfile.xray` — same as Dockerfile + xray-core (Xray variant)
- `entrypoint.sh` — WG container entrypoint: wg-quick up → uvicorn
- `entrypoint-awg.sh` — AWG container entrypoint: awg-quick up → uvicorn
- `entrypoint-xray.sh` — Xray container entrypoint: wg-quick up → xray run → uvicorn
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
- Auth middleware checks in order:
  1. Public paths (`/health`, `/`, `/app.js`, etc.) — always allowed
  2. Container-internal IP (`10.8.0.1`) — always allowed
  3. Admin peer VPN IP (`10.8.0.x` with `role: admin`) — allowed without token
  4. `X-API-Token` header or `?token=` query param — recovery/developer access
- `Authorization: Bearer` is **not** used; only `X-API-Token` header
- Uvicorn binds to `ADMIN_BIND_HOST` env
- Host port: varies by variant:
  - WG/AWG: `-p 127.0.0.1:8000:8000` (loopback)
  - Xray: `-p 8082:8000` (public, tunneled through Xray WS)

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
