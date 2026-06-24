# FamilyNet VPN API

## Authentication

Protected endpoints allow access via:
- Connection from an admin peer (WireGuard IP `10.8.0.x`) — no token required
- `X-API-Token: <token>` header — recovery/developer access
- `?token=<token>` query parameter — fallback

Always allowed: `127.0.0.1`, `::1`, `10.8.0.1` (middleware).

---

## Frontend

| Method | Endpoint |
|--------|---------|
| GET | / |
| GET | /app.css |
| GET | /app.js |
| GET | /manifest.json |

## System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /health | Health check (public, no auth) |
| GET | /status | WireGuard status JSON |

## Dashboard

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /dashboard | System status, WG stats, top user |
| GET | /diagnostics | CPU, RAM, Disk %, WG/Internet/backup checks |

## Client Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /peers | List all peers |
| GET | /peer/{client_id} | Single peer details |
| POST | /peer/create | Create new peer |
| DELETE | /peer/{client_id} | Delete peer |
| POST | /peer/{client_id}/name | Rename peer |
| POST | /peer/{client_id}/role | Change role (admin/user) |
| POST | /peer/{client_id}/disable | Disable peer |
| POST | /peer/{client_id}/enable | Enable peer |

### Speed Limits

| Method | Endpoint |
|--------|----------|
| POST | /peer/{client_id}/speed-limit |
| POST | /peer/{client_id}/speed-normal |

### Configuration

| Method | Endpoint |
|--------|----------|
| GET | /peer/{client_id}/config |
| GET | /peer/{client_id}/qr |
| GET | /xray/links | Returns VLESS links for REALITY, XHTTP, WS (Xray variant only) |

## Parental Control

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /parental/rules | Get all rules |
| POST | /parental/rules | Set/modify rule (daily/time/speed limits) |

## Traffic

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /traffic/global/days | Daily traffic history (rolling 7 days) |
| GET | /traffic/global/hours | Hourly traffic history (24h) |
| GET | /traffic/peer/{client_id}/days | Per-peer daily traffic |
| GET | /traffic/peer/{client_id}/hours | Per-peer hourly traffic |

## Activity

| Method | Endpoint |
|--------|----------|
| GET | /activity |

## Tokens

Recovery/developer tokens.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /tokens | List recovery tokens |
| POST | /tokens | Create token |
| DELETE | /tokens/{id} | Revoke token |

## Avatars

| Method | Endpoint |
|--------|----------|
| GET | /avatars |
| POST | /avatars |

## Backup System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /backup/status | Backup info (size, date, last auto-backup) |
| POST | /backup/create | Create manual backup (lock-protected) |
| GET | /backup/download/{kind} | Download backup (latest, previous, timestamped) |
| POST | /backup/restore/{kind} | Restore from backup (safety snapshot + validate) |
| POST | /backup/upload | Upload backup archive (max 100MB) |

## Maintenance

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /maintenance/restart-vpn | Restart WireGuard (wg-quick down/up) |
| POST | /maintenance/restart-admin | Restart uvicorn (docker restart) |
| POST | /maintenance/reboot-server | Reboot VPS |

## Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /settings | Get panel settings |
| POST | /settings | Update panel settings |
