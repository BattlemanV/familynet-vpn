# Changelog

## v1.1 — Three-Tier Architecture

- **AWG variant** (`Dockerfile.awg`, `entrypoint-awg.sh`): AmneziaWG userspace daemon with obfuscation (Jc=4, Jmin=10, Jmax=50, S1=97, S2=99), port 31121/udp
- **Xray variant** (`Dockerfile.xray`, `entrypoint-xray.sh`, `docker-compose.xray.yml`): Three parallel inbounds — REALITY (443), XHTTP (8445), WS (8444)
- Auto-generation of REALITY keys (`xray x25519`) and shortId on first start
- REALITY with `www.microsoft.com` as target, Chrome fingerprint, xtls-rprx-vision flow
- XHTTP (`network: xhttp`, cleartext HTTP/2) — iOS fallback that works with Hiddify
- WS (`network: ws`, path `/vless`) — universal fallback for AmneziaVPN / Hiddify on iOS
- Web panel: variant-specific UI — shows REALITY/XHTTP/WS QR codes and VLESS links in Xray mode
- `/xray/links` API endpoint returning all three VLESS links as JSON
- `variant` field in `/dashboard` response for frontend detection
- `CONNECT.ru.md` — per-platform connection guide
- Documentation updated: README, ARCHITECTURE, INSTALLATION reflect all three variants

## v1.0 — Initial Release

- PWA frontend + FastAPI backend
- Client management (create/delete/rename/enable/disable)
- Peer roles (admin/user) with Access tab
- Traffic statistics (SQLite, rolling periods)
- Activity log, backup/restore, speed limits
- Parental control with schedule
- CPU/RAM/disk/uptime monitoring
- 7 languages (EN, RU, ZH, TR, FA, ES, HI)
- VPN-first auth model (no passwords)
