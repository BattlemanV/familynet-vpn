# Changelog

## v2.1 — Mobile PWA Stability & Xray Race Fixes

- **Service Worker**: fixed `?v=Date.now()` causing hundreds of orphan SW registrations; SW now registered at fixed `/sw.js` with cleanup of old registrations
- **API timeout**: replaced `AbortController` (broken through SW on iOS Safari) with `Promise.race` + 20s timeout guaranteed to fire
- **SW timeout**: added 30s `AbortController` inside SW fetch handler to prevent hanging connections
- **Cache busting**: `cache:'no-cache'` on all API fetches to prevent stale browser cache
- **`refreshAll()`**: replaced recursive queue with `while(_refreshPromise){await}` loop — no more race with 5s poll
- **Optimistic create**: peer pushed to `state.peers` + `showMain()` called immediately after POST, `refreshAll()` runs in background
- **Double-tap guard**: `_creating` flag + button disabled with "Загрузка..." text
- **`try/catch` in `createClient()`**: non-fatal errors now show toast instead of silently hanging UI
- **`sync_xray_config()` debounce + lock**: `_XRAY_SYNC_PENDING` prevents spawning threads; `_XRAY_SYNC_LOCK` prevents two threads from `pkill`+`Popen` simultaneously
- **`CLIENTS_LOCK`**: `threading.Lock()` around all `clients.json` read-modify-write to prevent race conditions
- **`get_xray_traffic()` cache fix**: `_TRAFFIC_CACHE_TS` set before xray query, blocking repeated 5s timeouts across N peers × 2 endpoints
- **Startup delay**: `time.sleep(0.3)` before `Popen xray` to let OS free ports
- **Routes**: `/sw.js` and `/manifest.json` now served by backend (were 404)
- **Backup guard**: `os.path.exists()` check before `shutil.copy2` on fresh install

## v2.0 — Code Separation & Xray Stabilisation

- **Code separation**: monolithic `app.py` (~2750 lines) split into:
  - `common.py` (~560 lines) — pure shared logic: auth, backup, tokens, clients CRUD, system info, settings, activity log, i18n, avatars, health
  - `app.py` (~1800 lines) — WG/AWG-specific logic: wg-quick, wg dump/parse, iptables, traffic DB (SQLite), speed limits (tc), parental control
  - `app_xray.py` (~940 lines) — Xray-specific logic: `sync_xray_config()`, 3 inbounds (REALITY/WS/XHTTP), per-peer UUID CRUD, VLESS link builder, config download/QR per protocol, dashboard, diagnostics
- **Zero conditional checks**: no `if WG_VARIANT == "xray"` anywhere — variants are entirely separate files
- **Auth middleware**: `x-api-token` header (not `Authorization: Bearer`) with multi-token support via `api_tokens.json`
- **Diagnostics**: `pidof` instead of `pgrep` for xray process detection (pgrep not available in slim image)
- **REALITY short ID persistence**: `reality_short_id` file extracted from existing xray.json if missing on startup
- **Multi-token auth**: legacy `api_token` auto-migrated to `api_tokens.json` with recovery token reconciliation

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
