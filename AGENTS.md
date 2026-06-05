# AGENTS.md

## Project

Personal VPN Admin — панель управления семейным VPN-сервером на собственном VPS.

## Important files

- app.py — FastAPI backend
- web/app.js — frontend logic
- web/app.css — frontend styles
- web/index.html — PWA HTML

## Rules

- Do not commit secrets.
- Do not commit api_token, json state files, sqlite databases or backups.
- Always check frontend usage before removing API endpoints.
- Be careful with iOS Safari: inputs must have font-size 16px or higher.
- Backup/restore logic is critical and should not be changed casually.

## Current priorities

1. Clean frontend CSS.
2. Stabilize modal windows.
3. Improve documentation.
4. Add parental control.
5. Add Telegram notifications.
