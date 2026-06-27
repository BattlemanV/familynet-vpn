# FamilyNet VPN API

## Authentication

Protected endpoints allow access via (checked in order):
- Public paths: `/health`, `/`, `/app.css`, `/app.js`, `/sw.js`, `/manifest.json`
- Request from container-internal IP `10.8.0.1`
- Request from admin peer VPN IP (`10.8.0.x` with `role: admin`)
- `X-API-Token: <token>` header — recovery/developer access
- `?token=<token>` query parameter — fallback

**Note:** `Authorization: Bearer` is not used. Only `X-API-Token` header.

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

## Dashboard

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /dashboard | System status, peer stats, variant info |
| GET | /diagnostics | CPU, RAM, Disk %, VPN/Internet/backup checks |

## Client Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /peers | List all peers |
| POST | /peer/create | Create new peer |
| DELETE | /peer/{client_id} | Delete peer |
| POST | /peer/{client_id}/name | Rename peer |
| POST | /peer/{client_id}/role | Change role (admin/user) |
| POST | /peer/{client_id}/protect | Toggle protected status |
| POST | /peer/{client_id}/disable | Disable peer |
| POST | /peer/{client_id}/enable | Enable peer |

### Speed Limits

| Method | Endpoint | Variant |
|--------|----------|---------|
| POST | /peer/{client_id}/speed-limit | WG/AWG only |
| POST | /peer/{client_id}/speed-normal | WG/AWG only |

### Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /peer/{client_id}/config | Download config file (WG/AWG) or VLESS link (Xray) |
| GET | /peer/{client_id}/config?proto=reality | Xray REALITY config |
| GET | /peer/{client_id}/config?proto=ws | Xray WS config |
| GET | /peer/{client_id}/config?proto=xhttp | Xray XHTTP config |
| GET | /peer/{client_id}/qr | QR code for config (same proto params) |
| GET | /xray/links | All three VLESS links as JSON (Xray only) |

## Parental Control

| Method | Endpoint | Variant |
|--------|----------|---------|
| GET | /parental/rules | Get all rules |
| PUT | /parental/rules/{client_id} | Set/modify rule (WG/AWG only) |

## Traffic (WG/AWG only)

| Method | Endpoint |
|--------|----------|
| GET | /peer/{client_id}/traffic/days |
| GET | /peer/{client_id}/traffic/hours |
| GET | /traffic/global/hours |
| GET | /traffic/global/days |

## Activity

| Method | Endpoint |
|--------|----------|
| GET | /activity |

## Tokens

Recovery/developer tokens. Managed via api_tokens.json; legacy api_token is reconciled as "Recovery" token on startup.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /tokens | List recovery tokens |
| POST | /tokens | Create token (with password label) |
| DELETE | /tokens/{id} | Revoke token |

## Avatars

| Method | Endpoint |
|--------|----------|
| GET | /avatars |
| POST | /avatars |

## Backup System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /backup/status | Backup info (size, date) |
| POST | /backup/create | Create manual backup (lock-protected) |
| GET | /backup/download/{kind} | Download backup (latest/previous) |
| POST | /backup/restore/{kind} | Restore from backup (safety snapshot + validate) |
| POST | /backup/upload | Upload backup archive (max 100MB) |

## Maintenance

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /maintenance/restart-vpn | Restart WireGuard (wg-quick down/up) or Xray (pkill + restart) |
| POST | /maintenance/restart-admin | Restart uvicorn (docker restart) |
| POST | /maintenance/reboot-server | Reboot VPS |

## Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /settings | Update panel settings (traffic_warn_gb, timezone) |
