# FamilyNet VPN

Self-hosted VPN management panel with 3 protection tiers: WireGuard, AmneziaWG, and Xray (REALITY + XHTTP + WS).

## Quick Start

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/BattlemanV/familynet-vpn/main/install.sh)
```

Choose one of three variants during install:

| Tier | Port | Protocol | Best for |
|------|------|----------|----------|
| **1 — WireGuard** | 51820/udp | WG | Any device, max speed |
| **2 — AmneziaWG** | 31121/udp | AWG (obfuscated) | DPI-bypass, AmneziaVPN |
| **3 — Xray** | 443/tcp | REALITY | Windows/Android/macOS |
| | 8445/tcp | XHTTP | iOS (Hiddify) |
| | 8444/tcp | WS | iOS fallback |

After installation, open the panel via VPN:

```
 1. Connect to VPN (scan the QR shown after install)
 2. Open http://10.8.0.1:8000
```

**No password or token required** for admin VPN peers. A recovery token is stored on the server for emergency access.

## Features

- Client management (create/delete/rename/enable/disable)
- QR codes and config download (WG, AWG, and Xray VLESS links)
- Roles (admin / user)
- Traffic statistics (today, week, month, year)
- Activity log
- Backup / restore (tar.gz, auto on changes, upload max 100MB)
- Speed limits (symmetrical, upload + download)
- Parental control (daily limits, schedule, throttle)
- Online / offline indicator
- CPU, RAM, disk, uptime monitoring
- VPS reboot / WireGuard restart / panel restart
- Avatars (emoji + photo, localStorage)
- 7 languages: EN, RU, ZH, TR, FA, ES, HI

## Screenshots

| | |
|:-:|:-:|
| ![Dashboard](screenshots/photo_1_2026-06-19_15-05-44.jpg) | ![Devices](screenshots/photo_2_2026-06-19_15-05-44.jpg) |
| ![Traffic & charts](screenshots/photo_3_2026-06-19_15-05-44.jpg) | ![Settings & backups](screenshots/photo_4_2026-06-19_15-05-44.jpg) |
| ![Diagnostics & logs](screenshots/photo_5_2026-06-19_15-05-44.jpg) | |

## Stack

- Docker (python:3.11-slim)
- WireGuard / AmneziaWG / Xray-core
- FastAPI + Uvicorn
- PWA frontend (vanilla JS)
- SQLite (traffic history)

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — 3-tier architecture
- [CONNECT.ru.md](CONNECT.ru.md) — connection guide (Russian)
- [INSTALLATION.md](INSTALLATION.md) — installation details
- [API.md](API.md) — API endpoints
- [CHANGELOG.md](CHANGELOG.md) — changelog
- [DATA-FORMATS.md](DATA-FORMATS.md) — data formats

## Russian

Краткая инструкция на русском: [README.ru.md](README.ru.md)
