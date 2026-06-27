import os, re, shutil, subprocess, threading, time, tempfile, secrets, ipaddress, sqlite3, uuid
from typing import Any, Dict, List, Optional
from pathlib import Path
import qrcode
from fastapi import Query, Body, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from common import (
    IS_CONTAINER, APP_DIR, WEB_DIR, CLIENTS_FILE, BACKUP_DIR, TOKEN_FILE, TOKEN_NEW_FILE,
    TOKENS_FILE, SETTINGS_FILE, ACTIVITY_LOG, HEALTH_FILE, SPEED_LIMITS_FILE, PARENTAL_RULES_FILE,
    MANUAL_OVERRIDES_FILE, TOP_USER_EVENT_FILE, AVATARS_PATH, PUBLIC_PATHS, LEGACY_PROTECTED_NAMES,
    atomic_json_write, run_cmd, try_run_cmd, bytes_to_human, backup_size_human, handshake_to_text,
    seconds_to_human, today_key, week_key, month_key, _get_hostname, get_default_interface,
    get_loadavg, get_cpu_usage, get_memory, get_disk_root, get_uptime,
    read_settings, write_settings, apply_timezone,
    log_activity, read_activity,
    _read_tokens_raw, _write_tokens_raw, _migrate_old_tokens, _reconcile_recovery_token,
    get_all_tokens, require_auth,
    read_clients_data, get_client, allocate_next_client_ip,
    validate_rate, read_speed_limits, write_speed_limits,
    read_parental_rules, write_parental_rules, read_manual_overrides, write_manual_overrides,
    _create_backup, _acquire_backup_lock, config_changed, backup_file_info,
    _do_restore, _read_health, _write_health, _days_since,
    _migrate_protected_peers, _migrate_peer_roles,
    check_schedule_block, check_parental_limits,
    load_top_user_event, save_top_user_event,
)

if IS_CONTAINER:
    WG_CONTAINER = ""
else:
    APP_DIR = "/root/wg-admin-api"
    CLIENTS_FILE = os.environ.get("CLIENTS_FILE", os.path.join(APP_DIR, "clients.json"))
    WG_CONTAINER = os.environ.get("WG_CONTAINER", "wg-vpn")

WG_INTERFACE = os.environ.get("WG_INTERFACE", "wg0")
WG_HOST = os.environ.get("WG_HOST")
if not WG_HOST: raise RuntimeError("WG_HOST environment variable is required")
WG_PORT = os.environ.get("WG_PORT", "51820")
WG_DNS = os.environ.get("WG_DNS", "1.1.1.1")
WG_ALLOWED_IPS = os.environ.get("WG_ALLOWED_IPS", "0.0.0.0/0, ::/0")
WG_VARIANT = os.environ.get("WG_VARIANT", "wg")
ONLINE_THRESHOLD_SECONDS = int(os.environ.get("ONLINE_THRESHOLD_SECONDS", "1800"))
TRAFFIC_DB_FILE = os.path.join(APP_DIR, "traffic_stats.sqlite")

LIVE_TRAFFIC_PREVIOUS: Dict[str, List[Dict[str, Any]]] = {}
LIVE_TRAFFIC_LOCK = threading.Lock()
WG_DUMP_CACHE: Dict[str, Any] = {"value": None, "ts": 0}
WG_DUMP_CACHE_LOCK = threading.Lock()
PARENTAL_LOOP_INTERVAL = 60
_parental_loop_stop = threading.Event()

def _ensure_wg_iptables():
    try:
        iface = os.environ.get("WG_EXTERNAL_IFACE", "eth0")
        def _ac(cmd): return subprocess.run(cmd, capture_output=True).returncode == 0
        for args in [
            (["iptables", "-C", "FORWARD", "-i", "wg0", "-j", "ACCEPT"],
             ["iptables", "-A", "FORWARD", "-i", "wg0", "-j", "ACCEPT"]),
            (["iptables", "-C", "FORWARD", "-o", "wg0", "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"],
             ["iptables", "-A", "FORWARD", "-o", "wg0", "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT"]),
            (["iptables", "-t", "nat", "-C", "POSTROUTING", "-o", iface, "-j", "MASQUERADE"],
             ["iptables", "-t", "nat", "-A", "POSTROUTING", "-o", iface, "-j", "MASQUERADE"]),
        ]:
            if not _ac(args[0]): _ac(args[1])
    except Exception as e: print(f"[iptables] ensure error: {e}")

def _sync_wg_peers():
    data = read_clients_data(); clients = data.get("clients", {}); server = data.get("server", {})
    wg_conf_path = os.path.join(APP_DIR, "wg0.conf")
    wg_lines = ["[Interface]", f"PrivateKey = {server.get('privateKey', '')}",
                 f"Address = {server.get('address', '10.8.0.1')}/24", f"ListenPort = {WG_PORT}"]
    if WG_VARIANT == "awg": wg_lines.extend(["Jc = 4", "Jmin = 10", "Jmax = 50", "S1 = 97", "S2 = 99"])
    external_iface = get_default_interface()
    wg_lines.append(f"PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o {external_iface} -j MASQUERADE")
    wg_lines.append(f"PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o {external_iface} -j MASQUERADE")
    wg_lines.append("")
    for cid, c in clients.items():
        if not c.get("enabled"): continue
        pub = c.get("publicKey", ""); psk = c.get("preSharedKey", ""); addr = c.get("address", "")
        if not pub or not psk or not addr: continue
        wg_lines.extend(["[Peer]", f"PublicKey = {pub}", f"PresharedKey = {psk}", f"AllowedIPs = {addr}/32", ""])
        fd, psk_tmp = tempfile.mkstemp(prefix="wg-sync-psk-"); os.close(fd)
        with open(psk_tmp, "w") as f: f.write(psk)
        os.chmod(psk_tmp, 0o600)
        try: run_cmd(["wg", "set", WG_INTERFACE, "peer", pub, "preshared-key", psk_tmp, "allowed-ips", f"{addr}/32"], timeout=8)
        except Exception as e: print(f"[_sync_wg_peers] add {cid} ({c.get('name', '?')}): {e}")
        finally: try_run_cmd(["rm", "-f", psk_tmp], timeout=5)
    old_pubkeys = set()
    try:
        dump = try_run_cmd(["wg", "show", WG_INTERFACE, "dump"])
        if dump:
            for line in dump.strip().split("\n")[1:]:
                parts = line.split("\t")
                if len(parts) >= 1 and parts[0]: old_pubkeys.add(parts[0])
    except Exception: pass
    active_pubkeys = {c.get("publicKey", "") for c in clients.values() if isinstance(c, dict) and c.get("enabled") and c.get("publicKey")}
    for pub in (old_pubkeys - active_pubkeys):
        try: run_cmd(["wg", "set", WG_INTERFACE, "peer", pub, "remove"], timeout=8)
        except Exception as e: print(f"[_sync_wg_peers] remove stale: {e}")
    try:
        with open(wg_conf_path, "w") as f: f.write("\n".join(wg_lines))
    except Exception as e: print(f"[_sync_wg_peers] save wg0.conf: {e}")

def _parental_loop():
    while not _parental_loop_stop.wait(PARENTAL_LOOP_INTERVAL):
        try: enforce_parental_limits()
        except Exception: pass

def apply_speed_limits() -> None:
    limits = read_speed_limits()
    if IS_CONTAINER: try_run_cmd(["tc", "qdisc", "del", "dev", WG_INTERFACE, "root"], timeout=5)
    else: try_run_cmd(["docker", "exec", WG_CONTAINER, "tc", "qdisc", "del", "dev", WG_INTERFACE, "root"], timeout=5)
    active = {ip: item for ip, item in limits.items() if isinstance(item, dict) and item.get("enabled") and item.get("rate")}
    if not active: return
    _root_rate = "1000mbit"
    script = [f'DEV="{WG_INTERFACE}"',
              'tc qdisc add dev "$DEV" root handle 1: htb default 999',
              f'tc class add dev "$DEV" parent 1: classid 1:1 htb rate {_root_rate} ceil {_root_rate}',
              f'tc class add dev "$DEV" parent 1:1 classid 1:999 htb rate {_root_rate} ceil {_root_rate}']
    idx = 100
    for ip, item in active.items():
        idx += 1; rate = validate_rate(item.get("rate", "256kbit")); class_id = f"1:{idx}"
        script.append(f'tc class add dev "$DEV" parent 1:1 classid {class_id} htb rate {rate} ceil {rate}')
        script.append(f'tc filter add dev "$DEV" protocol ip parent 1:0 prio {idx} u32 match ip dst {ip}/32 flowid {class_id}')
        script.append(f'tc filter add dev "$DEV" protocol ip parent 1:0 prio {idx} u32 match ip src {ip}/32 flowid {class_id}')
    if IS_CONTAINER: run_cmd(["sh", "-c", "\n".join(script)], timeout=10)
    else: run_cmd(["docker", "exec", WG_CONTAINER, "sh", "-c", "\n".join(script)], timeout=10)

def set_peer_speed_limit(client_id: str, enabled: bool, rate: str = "") -> Dict[str, Any]:
    if not rate: rate = "256kbit"
    peer = find_peer_by_client_id(client_id); ip = peer.get("ip"); name = peer.get("name", client_id)
    if not ip: raise HTTPException(status_code=400, detail="Peer IP not found")
    limits = read_speed_limits()
    if enabled: limits[ip] = {"enabled": True, "rate": rate, "client_id": client_id, "name": name, "updated_ts": int(time.time())}
    else: limits.pop(ip, None)
    write_speed_limits(limits); apply_speed_limits()
    return {"ok": True, "peer": name, "ip": ip, "client_id": client_id, "speed_limited": enabled, "rate": rate if enabled else None}

def get_wg_dump() -> str:
    now = time.time()
    with WG_DUMP_CACHE_LOCK:
        if WG_DUMP_CACHE["value"] and now - WG_DUMP_CACHE["ts"] < 5: return WG_DUMP_CACHE["value"]
    if IS_CONTAINER:
        dump = try_run_cmd(["wg", "show", WG_INTERFACE, "dump"])
        if dump:
            with WG_DUMP_CACHE_LOCK: WG_DUMP_CACHE["value"] = dump; WG_DUMP_CACHE["ts"] = now
            return dump
        raise HTTPException(status_code=500, detail=f"Cannot read WireGuard dump for {WG_INTERFACE}")
    host_dump = try_run_cmd(["wg", "show", WG_INTERFACE, "dump"])
    if host_dump:
        with WG_DUMP_CACHE_LOCK: WG_DUMP_CACHE["value"] = host_dump; WG_DUMP_CACHE["ts"] = now
        return host_dump
    if shutil.which("docker"):
        container_dump = try_run_cmd(["docker", "exec", WG_CONTAINER, "wg", "show", WG_INTERFACE, "dump"])
        if container_dump:
            with WG_DUMP_CACHE_LOCK: WG_DUMP_CACHE["value"] = container_dump; WG_DUMP_CACHE["ts"] = now
            return container_dump
    raise HTTPException(status_code=500, detail=f"Cannot read WireGuard dump for {WG_INTERFACE}")

def init_traffic_db() -> None:
    with sqlite3.connect(TRAFFIC_DB_FILE, timeout=10) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS peer_counters (public_key TEXT PRIMARY KEY, last_rx INTEGER NOT NULL DEFAULT 0, last_tx INTEGER NOT NULL DEFAULT 0, updated_ts INTEGER NOT NULL DEFAULT 0)")
        db.execute("CREATE TABLE IF NOT EXISTS traffic_totals (public_key TEXT NOT NULL, period_type TEXT NOT NULL, period_key TEXT NOT NULL, rx INTEGER NOT NULL DEFAULT 0, tx INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (public_key, period_type, period_key))")
        db.execute("CREATE TABLE IF NOT EXISTS online_totals (public_key TEXT NOT NULL, day_key TEXT NOT NULL, seconds INTEGER NOT NULL DEFAULT 0, last_seen_ts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (public_key, day_key))")
        db.execute("CREATE TABLE IF NOT EXISTS peer_last_seen (public_key TEXT PRIMARY KEY, last_seen INTEGER NOT NULL DEFAULT 0, updated_ts INTEGER NOT NULL DEFAULT 0)")

def save_peer_last_seen(public_key: str, latest_handshake: int) -> int:
    if not public_key or latest_handshake <= 0: return 0
    now = int(time.time())
    with sqlite3.connect(TRAFFIC_DB_FILE, timeout=10) as db:
        row = db.execute("SELECT last_seen FROM peer_last_seen WHERE public_key = ?", (public_key,)).fetchone()
        old_last_seen = int(row[0]) if row else 0
        new_last_seen = max(old_last_seen, int(latest_handshake))
        db.execute("INSERT INTO peer_last_seen (public_key, last_seen, updated_ts) VALUES (?, ?, ?) ON CONFLICT(public_key) DO UPDATE SET last_seen = CASE WHEN excluded.last_seen > peer_last_seen.last_seen THEN excluded.last_seen ELSE peer_last_seen.last_seen END, updated_ts = excluded.updated_ts", (public_key, new_last_seen, now))
        return new_last_seen

def get_peer_last_seen(public_key: str) -> int:
    if not public_key: return 0
    with sqlite3.connect(TRAFFIC_DB_FILE, timeout=10) as db:
        row = db.execute("SELECT last_seen FROM peer_last_seen WHERE public_key = ?", (public_key,)).fetchone()
    return int(row[0]) if row else 0

def add_traffic_total(db, public_key: str, period_type: str, period_key: str, rx_delta: int, tx_delta: int) -> None:
    db.execute("INSERT INTO traffic_totals (public_key, period_type, period_key, rx, tx) VALUES (?, ?, ?, ?, ?) ON CONFLICT(public_key, period_type, period_key) DO UPDATE SET rx = rx + excluded.rx, tx = tx + excluded.tx", (public_key, period_type, period_key, rx_delta, tx_delta))

def read_traffic_total(db, public_key: str, period_type: str, period_key: str) -> Dict[str, int]:
    row = db.execute("SELECT rx, tx FROM traffic_totals WHERE public_key = ? AND period_type = ? AND period_key = ?", (public_key, period_type, period_key)).fetchone()
    if not row: return {"rx": 0, "tx": 0}
    return {"rx": int(row[0] or 0), "tx": int(row[1] or 0)}

def update_online_total(db, public_key: str, day: str, online: bool, now_ts: int) -> int:
    row = db.execute("SELECT seconds, last_seen_ts FROM online_totals WHERE public_key = ? AND day_key = ?", (public_key, day)).fetchone()
    if not row:
        db.execute("INSERT INTO online_totals (public_key, day_key, seconds, last_seen_ts) VALUES (?, ?, 0, ?)", (public_key, day, now_ts if online else 0))
        return 0
    seconds = int(row[0] or 0); last_seen = int(row[1] or 0)
    if online:
        if last_seen > 0: seconds += min(max(0, now_ts - last_seen), 120)
        db.execute("UPDATE online_totals SET seconds = ?, last_seen_ts = ? WHERE public_key = ? AND day_key = ?", (seconds, now_ts, public_key, day))
    else:
        db.execute("UPDATE online_totals SET last_seen_ts = 0 WHERE public_key = ? AND day_key = ?", (public_key, day))
    return seconds

def read_rolling_traffic(db, public_key: str, days: int) -> Dict[str, int]:
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    row = db.execute("SELECT COALESCE(SUM(rx), 0), COALESCE(SUM(tx), 0) FROM traffic_totals WHERE public_key = ? AND period_type = 'day' AND period_key >= ?", (public_key, cutoff)).fetchone()
    return {"rx": int(row[0] or 0), "tx": int(row[1] or 0)}

def cleanup_traffic_db(db) -> None:
    cutoff_day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 180 * 86400))
    db.execute("DELETE FROM traffic_totals WHERE period_type = 'day' AND period_key < ?", (cutoff_day,))
    db.execute("DELETE FROM online_totals WHERE day_key < ?", (cutoff_day,))

def get_period_traffic(public_key: str, rx: int, tx: int, online: bool = False) -> Dict[str, Any]:
    today = today_key(); week = week_key(); month = month_key(); now_ts = int(time.time())
    with sqlite3.connect(TRAFFIC_DB_FILE, timeout=10) as db:
        row = db.execute("SELECT last_rx, last_tx FROM peer_counters WHERE public_key = ?", (public_key,)).fetchone()
        ignore_zero_offline = bool(row) and rx == 0 and tx == 0 and not online
        if row is None:
            rx_delta = 0; tx_delta = 0
            db.execute("INSERT INTO peer_counters (public_key, last_rx, last_tx, updated_ts) VALUES (?, ?, ?, ?)", (public_key, rx, tx, now_ts))
        elif ignore_zero_offline: rx_delta = 0; tx_delta = 0
        else:
            last_rx = int(row[0] or 0); last_tx = int(row[1] or 0)
            rx_delta = rx - last_rx if rx >= last_rx else rx; tx_delta = tx - last_tx if tx >= last_tx else tx
            rx_delta = max(0, rx_delta); tx_delta = max(0, tx_delta)
            db.execute("UPDATE peer_counters SET last_rx = ?, last_tx = ?, updated_ts = ? WHERE public_key = ?", (rx, tx, now_ts, public_key))
        if rx_delta or tx_delta:
            add_traffic_total(db, public_key, "day", today, rx_delta, tx_delta)
            add_traffic_total(db, public_key, "hour", time.strftime("%Y-%m-%d-%H", time.gmtime()), rx_delta, tx_delta)
            add_traffic_total(db, public_key, "week", week, rx_delta, tx_delta)
            add_traffic_total(db, public_key, "month", month, rx_delta, tx_delta)
            add_traffic_total(db, public_key, "total", "all", rx_delta, tx_delta)
        online_today_seconds = update_online_total(db, public_key, today, online, now_ts)
        day_t = read_traffic_total(db, public_key, "day", today)
        week_total = read_rolling_traffic(db, public_key, 7)
        month_total = read_traffic_total(db, public_key, "month", month)
        year_total = read_rolling_traffic(db, public_key, 365)
        total = read_traffic_total(db, public_key, "total", "all")
        cleanup_traffic_db(db)
    return {
        "today_rx_bytes": day_t["rx"], "today_tx_bytes": day_t["tx"], "today_total_bytes": day_t["rx"] + day_t["tx"],
        "today_rx_human": bytes_to_human(day_t["rx"]), "today_tx_human": bytes_to_human(day_t["tx"]), "today_total_human": bytes_to_human(day_t["rx"] + day_t["tx"]),
        "week_rx_bytes": week_total["rx"], "week_tx_bytes": week_total["tx"], "week_total_bytes": week_total["rx"] + week_total["tx"],
        "week_rx_human": bytes_to_human(week_total["rx"]), "week_tx_human": bytes_to_human(week_total["tx"]), "week_total_human": bytes_to_human(week_total["rx"] + week_total["tx"]),
        "month_rx_bytes": month_total["rx"], "month_tx_bytes": month_total["tx"], "month_total_bytes": month_total["rx"] + month_total["tx"],
        "month_rx_human": bytes_to_human(month_total["rx"]), "month_tx_human": bytes_to_human(month_total["tx"]), "month_total_human": bytes_to_human(month_total["rx"] + month_total["tx"]),
        "year_rx_bytes": year_total["rx"], "year_tx_bytes": year_total["tx"], "year_total_bytes": year_total["rx"] + year_total["tx"],
        "year_rx_human": bytes_to_human(year_total["rx"]), "year_tx_human": bytes_to_human(year_total["tx"]), "year_total_human": bytes_to_human(year_total["rx"] + year_total["tx"]),
        "saved_total_rx_bytes": total["rx"], "saved_total_tx_bytes": total["tx"], "saved_total_bytes": total["rx"] + total["tx"],
        "saved_total_human": bytes_to_human(total["rx"] + total["tx"]), "saved_total_rx_human": bytes_to_human(total["rx"]), "saved_total_tx_human": bytes_to_human(total["tx"]),
        "online_today_seconds": online_today_seconds, "online_today_human": seconds_to_human(online_today_seconds),
    }

def parse_wg_dump(dump: str) -> Dict[str, Any]:
    lines = [line for line in dump.splitlines() if line.strip()]
    data = read_clients_data(); clients = data.get("clients", {})
    live_by_public_key: Dict[str, Dict[str, Any]] = {}
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 8: continue
        public_key = parts[0]; preshared_key = parts[1]; endpoint = parts[2]; allowed_ips = parts[3]
        latest_handshake = int(parts[4] or "0"); transfer_rx = int(parts[5] or "0"); transfer_tx = int(parts[6] or "0"); persistent_keepalive = parts[7]
        online = latest_handshake > 0 and int(time.time()) - latest_handshake < ONLINE_THRESHOLD_SECONDS
        period = get_period_traffic(public_key, transfer_rx, transfer_tx, online)
        saved_last_seen = save_peer_last_seen(public_key, latest_handshake)
        live_by_public_key[public_key] = {
            "endpoint": endpoint if endpoint != "(none)" else None, "allowed_ips": allowed_ips,
            "latest_handshake": latest_handshake, "latest_handshake_text": handshake_to_text(saved_last_seen or latest_handshake),
            "saved_last_seen": saved_last_seen, "saved_last_seen_text": handshake_to_text(saved_last_seen),
            "online": online, "is_active_now": False,
            "transfer_rx_bytes": transfer_rx, "transfer_tx_bytes": transfer_tx, "transfer_total_bytes": transfer_rx + transfer_tx,
            "transfer_rx_human": bytes_to_human(transfer_rx), "transfer_tx_human": bytes_to_human(transfer_tx), "transfer_total_human": bytes_to_human(transfer_rx + transfer_tx),
            **period, "persistent_keepalive": persistent_keepalive, "has_preshared_key": preshared_key not in ("", "(none)"),
        }
    speed_limits = read_speed_limits(); peers = []
    if isinstance(clients, dict):
        for client_id, client in clients.items():
            if not isinstance(client, dict): continue
            name = str(client.get("name", "")).strip(); address = str(client.get("address", "")).strip()
            public_key = str(client.get("publicKey", "")).strip(); enabled = bool(client.get("enabled", True))
            live = live_by_public_key.get(public_key)
            if live:
                endpoint = live["endpoint"]; allowed_ips = live["allowed_ips"]; latest_handshake = live["latest_handshake"]
                latest_handshake_text = live["latest_handshake_text"]; online = live["online"]
                transfer_rx = live["transfer_rx_bytes"]; transfer_tx = live["transfer_tx_bytes"]; transfer_total = live["transfer_total_bytes"]
                transfer_rx_human = live["transfer_rx_human"]; transfer_tx_human = live["transfer_tx_human"]; transfer_total_human = live["transfer_total_human"]
                persistent_keepalive = live["persistent_keepalive"]; has_preshared_key = live["has_preshared_key"]
            else:
                endpoint = None; allowed_ips = f"{address}/32" if address else ""
                latest_handshake = 0; saved_last_seen = get_peer_last_seen(public_key)
                latest_handshake_text = handshake_to_text(saved_last_seen) if saved_last_seen else "never"
                online = False; transfer_rx = 0; transfer_tx = 0; transfer_total = 0
                transfer_rx_human = bytes_to_human(0); transfer_tx_human = bytes_to_human(0); transfer_total_human = bytes_to_human(0)
                persistent_keepalive = "off"; has_preshared_key = bool(client.get("preSharedKey"))
                period = get_period_traffic(public_key, 0, 0) if public_key else {}
            peer_name = name or address or public_key[:10]
            speed_item = speed_limits.get(address, {}) if address else {}
            peers.append({
                "id": public_key[:12], "name": peer_name, "ip": address, "client_id": str(client_id),
                "enabled": enabled, "protected": bool(client.get("protected", False)), "role": client.get("role", "user"),
                "public_key": public_key, "public_key_short": public_key[:10] + "..." if public_key else "",
                "endpoint": endpoint, "allowed_ips": allowed_ips, "latest_handshake": latest_handshake,
                "latest_handshake_text": latest_handshake_text, "online": online,
                "transfer_rx_bytes": transfer_rx, "transfer_tx_bytes": transfer_tx, "transfer_total_bytes": transfer_total,
                "transfer_rx_human": transfer_rx_human, "transfer_tx_human": transfer_tx_human, "transfer_total_human": transfer_total_human,
                **(live if live else period),
                "persistent_keepalive": persistent_keepalive, "has_preshared_key": has_preshared_key,
            })
    for peer in peers:
        ip = peer.get("ip"); item = speed_limits.get(ip, {}) if ip else {}
        peer["speed_limited"] = bool(item.get("enabled")); peer["speed_limit_rate"] = item.get("rate")
    peers.sort(key=lambda peer: peer.get("ip") or "")
    return {"interface": WG_INTERFACE, "peers": peers, "peer_count": len(peers),
            "online_peer_count": sum(1 for peer in peers if peer["online"]),
            "enabled_peer_count": sum(1 for peer in peers if peer["enabled"]),
            "disabled_peer_count": sum(1 for peer in peers if not peer["enabled"]),
            "online_threshold_seconds": ONLINE_THRESHOLD_SECONDS}

def mark_active_peers(peer_data: Dict[str, Any]) -> Dict[str, Any]:
    top = get_top_user_now(peer_data.get("peers", []))
    peer_data["active_peer_count"] = int(top.get("active_peer_count", 0) or 0)
    peer_data["top_user_now"] = top
    return peer_data

def find_peer_by_client_id(client_id: str) -> Dict[str, Any]:
    data = parse_wg_dump(get_wg_dump())
    for peer in data["peers"]:
        if peer.get("client_id") == client_id: return peer
    raise HTTPException(status_code=404, detail=f"Peer not found: {client_id}")

def _get_client_field(client_id: str, field: str, default=None):
    item = get_client(client_id)
    return item["client"].get(field, default)

def can_disable_peer(client_id: str) -> Dict[str, Any]:
    peer = find_peer_by_client_id(client_id)
    if _get_client_field(client_id, "protected", False):
        return {"allowed": False, "peer": peer["name"], "ip": peer["ip"], "client_id": client_id, "reason": "Protected peer"}
    data = parse_wg_dump(get_wg_dump()); online_count = data["online_peer_count"]
    if peer["online"] and online_count <= 1:
        return {"allowed": False, "peer": peer["name"], "ip": peer["ip"], "client_id": client_id, "reason": "Last online peer"}
    return {"allowed": True, "peer": peer["name"], "ip": peer["ip"], "client_id": client_id, "reason": None}

def disable_peer(client_id: str) -> Dict[str, Any]:
    check = can_disable_peer(client_id)
    if not check["allowed"]: raise HTTPException(status_code=403, detail=check)
    item = get_client(client_id); data = item["data"]; client = item["client"]
    public_key = client.get("publicKey"); name = client.get("name", client_id); address = client.get("address")
    if not public_key: raise HTTPException(status_code=500, detail="Client publicKey not found")
    backup_path = f"{CLIENTS_FILE}.bak-{int(time.time())}"; shutil.copy2(CLIENTS_FILE, backup_path)
    client["enabled"] = False; atomic_json_write(CLIENTS_FILE, data, backup=True)
    try:
        cmd = ["wg", "set", WG_INTERFACE, "peer", public_key, "remove"]
        if not IS_CONTAINER: cmd = ["docker", "exec", WG_CONTAINER] + cmd
        run_cmd(cmd, timeout=8)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"message": "Peer disabled in JSON but live wg remove failed", "error": str(e), "backup": backup_path})
    log_activity("disable", name, client_id, address or "", {"backup": backup_path, "public_key_short": public_key[:10] + "..."})
    return {"disabled": True, "peer": name, "ip": address, "client_id": client_id, "public_key_short": public_key[:10] + "...", "backup": backup_path}

def enable_peer(client_id: str) -> Dict[str, Any]:
    item = get_client(client_id); data = item["data"]; client = item["client"]
    name = client.get("name", client_id); address = client.get("address"); public_key = client.get("publicKey"); preshared_key = client.get("preSharedKey")
    if not address: raise HTTPException(status_code=500, detail="Client address not found")
    if not public_key: raise HTTPException(status_code=500, detail="Client publicKey not found")
    if not preshared_key: raise HTTPException(status_code=500, detail="Client preSharedKey not found")
    backup_path = f"{CLIENTS_FILE}.bak-{int(time.time())}"; shutil.copy2(CLIENTS_FILE, backup_path)
    client["enabled"] = True; atomic_json_write(CLIENTS_FILE, data, backup=True)
    try:
        fd, psk_tmp = tempfile.mkstemp(prefix="wg-psk-"); os.close(fd)
        with open(psk_tmp, "w") as f: f.write(preshared_key)
        os.chmod(psk_tmp, 0o600)
        if not IS_CONTAINER:
            run_cmd(["docker", "cp", psk_tmp, f"{WG_CONTAINER}:{psk_tmp}"], timeout=8)
        cmd = ["wg", "set", WG_INTERFACE, "peer", public_key, "preshared-key", psk_tmp, "allowed-ips", f"{address}/32"]
        if not IS_CONTAINER: cmd = ["docker", "exec", WG_CONTAINER] + cmd
        run_cmd(cmd, timeout=8)
    finally:
        try_run_cmd(["rm", "-f", psk_tmp], timeout=5)
        if not IS_CONTAINER: try_run_cmd(["docker", "exec", WG_CONTAINER, "rm", "-f", psk_tmp], timeout=5)
    log_activity("enable", name, client_id, address or "", {"backup": backup_path, "public_key_short": public_key[:10] + "...", "method": "live wg set peer"})
    return {"enabled": True, "peer": name, "ip": address, "client_id": client_id, "public_key_short": public_key[:10] + "...", "backup": backup_path}

def create_peer(name: str) -> Dict[str, Any]:
    name = name.strip()
    if not name: raise HTTPException(status_code=400, detail="Client name is required")
    if not __import__('re').compile(r"^[\w\s\-\.а-яА-ЯёЁ]+$").match(name):
        raise HTTPException(status_code=400, detail="Client name contains invalid characters")
    data = read_clients_data(); clients = data.setdefault("clients", {})
    for client in clients.values():
        if isinstance(client, dict) and client.get("name") == name:
            raise HTTPException(status_code=409, detail=f"Client already exists: {name}")
    client_id = run_cmd(["cat", "/proc/sys/kernel/random/uuid"])
    address = allocate_next_client_ip(data)
    if IS_CONTAINER:
        private_key = run_cmd(["wg", "genkey"]); public_key = run_cmd(["wg", "pubkey"], input_text=private_key + "\n"); preshared_key = run_cmd(["wg", "genpsk"])
    else:
        private_key = run_cmd(["docker", "exec", WG_CONTAINER, "wg", "genkey"])
        public_key = run_cmd(["docker", "exec", "-i", WG_CONTAINER, "wg", "pubkey"], input_text=private_key + "\n")
        preshared_key = run_cmd(["docker", "exec", WG_CONTAINER, "wg", "genpsk"])
    backup_path = f"{CLIENTS_FILE}.bak-{int(time.time())}"; shutil.copy2(CLIENTS_FILE, backup_path)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    clients[client_id] = {"name": name, "address": address, "privateKey": private_key, "publicKey": public_key,
                          "preSharedKey": preshared_key, "xray_uuid": str(uuid.uuid4()),
                          "createdAt": now, "updatedAt": now, "enabled": True, "protected": False, "role": "user"}
    atomic_json_write(CLIENTS_FILE, data, backup=True)
    try:
        fd, psk_tmp = tempfile.mkstemp(prefix="wg-psk-"); os.close(fd)
        with open(psk_tmp, "w") as f: f.write(preshared_key)
        os.chmod(psk_tmp, 0o600)
        cmd = ["wg", "set", WG_INTERFACE, "peer", public_key, "preshared-key", psk_tmp, "allowed-ips", f"{address}/32"]
        if not IS_CONTAINER: cmd = ["docker", "exec", WG_CONTAINER] + cmd
        run_cmd(cmd, timeout=8)
    finally:
        try_run_cmd(["rm", "-f", psk_tmp], timeout=5)
        if not IS_CONTAINER: try_run_cmd(["docker", "exec", WG_CONTAINER, "rm", "-f", psk_tmp], timeout=5)
    log_activity("create", name, client_id, address, {"backup": backup_path, "public_key_short": public_key[:10] + "..."})
    config_changed(f"peer-created:{name}")
    return {"created": True, "peer": name, "ip": address, "client_id": client_id, "public_key_short": public_key[:10] + "...", "backup": backup_path}

def delete_peer(client_id: str) -> Dict[str, Any]:
    item = get_client(client_id); data = item["data"]; client = item["client"]
    name = client.get("name", client_id); address = client.get("address", ""); public_key = client.get("publicKey", "")
    if client.get("protected", False):
        log_activity("delete_blocked", name, client_id, address or "", {"reason": "Protected peer"})
        raise HTTPException(status_code=403, detail="Protected peer")
    backup_path = f"{CLIENTS_FILE}.bak-{int(time.time())}"; shutil.copy2(CLIENTS_FILE, backup_path)
    clients = data.get("clients", {}); clients.pop(client_id, None); atomic_json_write(CLIENTS_FILE, data, backup=True)
    if public_key:
        cmd = ["wg", "set", WG_INTERFACE, "peer", public_key, "remove"]
        if not IS_CONTAINER: cmd = ["docker", "exec", WG_CONTAINER] + cmd
        try_run_cmd(cmd, timeout=8)
    log_activity("delete", name, client_id, address or "", {"backup": backup_path, "public_key_short": public_key[:10] + "..." if public_key else ""})
    config_changed(f"peer-deleted:{name}")
    return {"deleted": True, "peer": name, "ip": address, "client_id": client_id, "backup": backup_path}

def build_client_config(client_id: str) -> str:
    item = get_client(client_id); data = item["data"]; client = item["client"]; server = data.get("server", {})
    client_private_key = client.get("privateKey"); client_address = client.get("address"); client_preshared_key = client.get("preSharedKey"); server_public_key = server.get("publicKey")
    if not client_private_key: raise HTTPException(status_code=500, detail="Client privateKey not found")
    if not client_address: raise HTTPException(status_code=500, detail="Client address not found")
    if not server_public_key: raise HTTPException(status_code=500, detail="Server publicKey not found")
    lines = ["[Interface]", f"PrivateKey = {client_private_key}", f"Address = {client_address}/32", f"DNS = {WG_DNS}"]
    if WG_VARIANT == "awg": lines.extend(["Jc = 4", "Jmin = 10", "Jmax = 50", "S1 = 97", "S2 = 99"])
    lines.append(""); lines.append("[Peer]"); lines.append(f"PublicKey = {server_public_key}")
    if client_preshared_key: lines.append(f"PresharedKey = {client_preshared_key}")
    lines.extend([f"AllowedIPs = {WG_ALLOWED_IPS}", f"Endpoint = {WG_HOST}:{WG_PORT}", "PersistentKeepalive = 25", ""])
    return "\n".join(lines)

def get_top_user_now(peers: List[Dict[str, Any]]) -> Dict[str, Any]:
    now_ts = time.time(); best = None; active_peer_count = 0; active_peer_keys = []; active_peer_threshold_mbps = 0.01; current_keys = set()
    TOP_WINDOW = 600
    for peer in peers:
        for peer in peers: peer["is_active_now"] = False; break
        break
    for peer in peers:
        public_key = peer.get("public_key")
        if not public_key: continue
        current_keys.add(public_key)
        rx = int(peer.get("transfer_rx_bytes") or 0); tx = int(peer.get("transfer_tx_bytes") or 0)
        with LIVE_TRAFFIC_LOCK:
            samples = LIVE_TRAFFIC_PREVIOUS.get(public_key)
            if samples is None: samples = []; LIVE_TRAFFIC_PREVIOUS[public_key] = samples
            samples.append({"ts": now_ts, "rx": rx, "tx": tx})
            cutoff = now_ts - TOP_WINDOW
            LIVE_TRAFFIC_PREVIOUS[public_key] = [s for s in samples if s["ts"] >= cutoff]
        if len(LIVE_TRAFFIC_PREVIOUS[public_key]) < 2:
            peer["is_active_now"] = peer.get("online") and int(peer.get("latest_handshake", 0)) > 0 and (now_ts - int(peer.get("latest_handshake", 0))) < 120
            continue
        oldest = LIVE_TRAFFIC_PREVIOUS[public_key][0]; dt = max(5.0, now_ts - oldest["ts"])
        rx_delta = rx - oldest["rx"]; tx_delta = tx - oldest["tx"]
        if rx_delta < 0 or tx_delta < 0: continue
        total_mbps = ((rx_delta + tx_delta) * 8) / dt / 1_000_000
        if peer.get("online") and total_mbps >= active_peer_threshold_mbps:
            active_peer_keys.append(public_key); peer["is_active_now"] = True
        if not peer.get("is_active_now"):
            peer["is_active_now"] = peer.get("online") and int(peer.get("latest_handshake", 0)) > 0 and (now_ts - int(peer.get("latest_handshake", 0))) < 120
    active_peer_count = sum(1 for p in peers if p.get("is_active_now"))
    with LIVE_TRAFFIC_LOCK:
        for key in list(LIVE_TRAFFIC_PREVIOUS.keys()):
            if key not in current_keys: LIVE_TRAFFIC_PREVIOUS.pop(key, None)
    for peer in peers:
        if not peer.get("online"): continue
        bytes_today = int(peer.get("today_total_bytes") or 0)
        if not best or bytes_today > best["today_total_bytes"]:
            best = {"name": peer.get("name"), "ip": peer.get("ip"), "rx_mbps": 0, "tx_mbps": 0, "total_mbps": 0,
                    "today_total_bytes": bytes_today, "today_total_human": peer.get("today_total_human", "0.00 B")}
    if not best: return {"active": False, "threshold_mbps": 1.0, "active_peer_count": active_peer_count,
                          "active_peer_keys": active_peer_keys, "active_peer_threshold_mbps": active_peer_threshold_mbps, "last_event": load_top_user_event()}
    best["active"] = best["today_total_bytes"] > 0; best["threshold_bytes"] = 1024
    best["active_peer_count"] = active_peer_count; best["active_peer_keys"] = active_peer_keys
    best["active_peer_threshold_mbps"] = active_peer_threshold_mbps
    if best["active"]: best["ts"] = int(time.time()); save_top_user_event(best)
    best["last_event"] = load_top_user_event()
    return best

def enforce_parental_limits() -> None:
    rules = read_parental_rules()
    if not rules: return
    overrides = read_manual_overrides()
    try: peer_data = mark_active_peers(parse_wg_dump(get_wg_dump()))
    except Exception: return
    for peer in peer_data.get("peers", []):
        cid = peer.get("client_id")
        if cid in overrides: continue
        rule = rules.get(cid)
        if not rule or not rule.get("enabled"): continue
        result = check_parental_limits(peer, rule); action = result["action"]
        if action == "disable":
            if peer.get("enabled"):
                try: disable_peer(cid)
                except Exception: pass
                log_activity("parental_block", peer.get("name", cid), cid, peer.get("ip", ""), {"reason": result["reason"]})
        elif action == "speed_limit":
            if not peer.get("speed_limited"):
                try: set_peer_speed_limit(cid, True, result.get("rate", ""))
                except Exception: pass
                log_activity("parental_slow", peer.get("name", cid), cid, peer.get("ip", ""), {"reason": "threshold", "rate": result.get("rate")})
        elif action == "ok":
            if not peer.get("enabled") and rule.get("auto_enable"):
                try: enable_peer(cid)
                except Exception: pass
                try: set_peer_speed_limit(cid, False)
                except Exception: pass
                log_activity("parental_unblock", peer.get("name", cid), cid, peer.get("ip", ""), {"reason": "limit_reset"})

def post_restart_wg():
    wg_conf_path = os.path.join(APP_DIR, "wg0.conf")
    if IS_CONTAINER and os.path.exists(wg_conf_path):
        try_run_cmd(["wg-quick", "down", wg_conf_path], timeout=10)
        run_cmd(["wg-quick", "up", wg_conf_path], timeout=10)
    elif not IS_CONTAINER:
        try_run_cmd(["docker", "stop", WG_CONTAINER], timeout=15)
        run_cmd(["docker", "start", WG_CONTAINER], timeout=15)

@asynccontextmanager
async def lifespan(app):
    apply_timezone(); _migrate_protected_peers(); _migrate_peer_roles(); _migrate_old_tokens(); _reconcile_recovery_token()
    init_traffic_db(); _ensure_wg_iptables(); _sync_wg_peers()
    t = threading.Thread(target=_parental_loop, daemon=True); t.start()
    yield
    _parental_loop_stop.set()

app = FastAPI(title="FamilyNet API", version="0.4.0", lifespan=lifespan)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log_activity("error", "system", "", "", {"error": str(exc), "path": request.url.path})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS: return await call_next(request)
    client_ip = request.client.host
    if client_ip == "10.8.0.1": return await call_next(request)
    try:
        data = read_clients_data()
        for c, client in data.get("clients", {}).items():
            if client.get("role") == "admin" and client.get("address") == client_ip: return await call_next(request)
    except Exception: pass
    x_api_token = request.headers.get("x-api-token")
    if x_api_token:
        try: require_auth(x_api_token); return await call_next(request)
        except HTTPException: pass
    token = request.query_params.get("token")
    if token:
        try: require_auth(token); return await call_next(request)
        except HTTPException: pass
    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

@app.get("/app.css")
def web_css():
    with open(os.path.join(WEB_DIR, "app.css"), "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="text/css; charset=utf-8", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/app.js")
def web_js():
    with open(os.path.join(WEB_DIR, "app.js"), "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="application/javascript; charset=utf-8", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.get("/")
def root() -> Response:
    with open(os.path.join(WEB_DIR, "index.html"), "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

# ── Shared endpoints ────────────────────────────────────────────

@app.get("/health")
def health(): return {"status": "ok"}

@app.post("/settings")
def update_settings(payload: Dict[str, Any] = Body(...), x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    update = {}
    if "traffic_warn_gb" in payload:
        v = int(payload["traffic_warn_gb"])
        if v > 0: update["traffic_warn_gb"] = v
    if "timezone" in payload:
        tz = payload["timezone"].strip()
        if tz: update["timezone"] = tz
    if not update: return read_settings()
    result = write_settings(update); apply_timezone(); config_changed("settings-updated")
    return result

@app.get("/tokens")
def list_tokens(x_api_token: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    return [{"id": t.get("id"), "label": t.get("label", ""),
             "prefix": (t["token"][:8] + "..." + t["token"][-4:]) if len(t.get("token", "")) > 12 else t.get("token", ""),
             "created_at": t.get("created_at")} for t in get_all_tokens()]

@app.post("/tokens")
def create_token(payload: Dict[str, Any] = Body(default={}), x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    label = str(payload.get("label", "")).strip() or "Unnamed"; new_id = str(uuid.uuid4())[:8]
    new_token = str(payload.get("password", "")).strip()
    if not new_token: new_token = secrets.token_hex(32)
    tokens = _read_tokens_raw()
    tokens.append({"id": new_id, "label": label, "token": new_token, "created_at": int(time.time())})
    _write_tokens_raw(tokens); log_activity("token_created", "", "", "", {"label": label, "id": new_id}); config_changed("token-created")
    return {"id": new_id, "label": label, "token": new_token}

@app.delete("/tokens/{token_id}")
def revoke_token(token_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    tokens = _read_tokens_raw()
    for t in tokens:
        if t.get("token") == x_api_token and t.get("id") == token_id:
            raise HTTPException(status_code=400, detail="Cannot revoke the current session token")
    new_tokens = [t for t in tokens if t.get("id") != token_id]
    if len(new_tokens) == len(tokens): raise HTTPException(status_code=404, detail="Token not found")
    _write_tokens_raw(new_tokens); log_activity("token_revoked", "", "", "", {"id": token_id}); config_changed("token-revoked")
    return {"ok": True, "revoked": token_id}

@app.get("/backup/status")
def backup_status(x_api_token: Optional[str] = Header(default=None)) -> Response:
    return Response(content=json.dumps({"latest": backup_file_info("latest.wgadmin"), "previous": backup_file_info("previous.wgadmin")}),
                    media_type="application/json", headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

@app.post("/backup/create")
def backup_create(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if not _acquire_backup_lock(): return {"created": False, "message": "Backup already in progress"}
    ok = _create_backup(); try_run_cmd(["rm", "-f", BACKUP_LOCK], timeout=5)
    return {"created": ok, "latest": backup_file_info("latest.wgadmin"), "previous": backup_file_info("previous.wgadmin")}

@app.get("/backup/download/{kind}")
def backup_download(kind: str, x_api_token: Optional[str] = Header(default=None), token: Optional[str] = Query(default=None)):
    if kind not in ("latest", "previous"): raise HTTPException(status_code=400, detail="Invalid backup kind")
    path = BACKUP_DIR / f"{kind}.wgadmin"
    if not path.exists(): raise HTTPException(status_code=404, detail="Backup not found")
    ts = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d_%H-%M")
    return Response(content=path.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="FamilyNet-VPN-{ts}.wgadmin"'})

@app.post("/backup/restore/{kind}")
def backup_restore(kind: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if kind not in ("latest", "previous"): raise HTTPException(status_code=400, detail="Invalid backup kind")
    path = BACKUP_DIR / f"{kind}.wgadmin"
    if not path.exists(): raise HTTPException(status_code=404, detail="Backup not found")
    log_activity("maintenance", "system", "", "", {"action": "restore-backup-started", "kind": kind, "file": str(path)})
    try: return _do_restore(path, f"{kind}.wgadmin", post_restart=post_restart_wg)
    except Exception as e:
        log_activity("maintenance", "system", "", "", {"action": "restore-backup-failed", "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")

MAX_BACKUP_SIZE = 100 * 1024 * 1024

@app.post("/backup/upload")
async def backup_upload(file: UploadFile = File(...), x_api_token: Optional[str] = Header(default=None)):
    if not file.filename or not file.filename.endswith(".wgadmin"): raise HTTPException(status_code=400, detail="File must be a .wgadmin backup")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True); path = BACKUP_DIR / "uploaded.wgadmin"
    try:
        content = await file.read()
        if len(content) > MAX_BACKUP_SIZE: raise HTTPException(status_code=413, detail="Backup file too large (max 100 MB)")
        path.write_bytes(content)
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=500, detail=f"Failed to save upload: {e}")
    log_activity("maintenance", "system", "", "", {"action": "restore-from-upload", "file": file.filename})
    try: return _do_restore(path, f"upload ({file.filename})", post_restart=post_restart_wg)
    except Exception as e:
        log_activity("maintenance", "system", "", "", {"action": "restore-upload-failed", "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")

@app.get("/activity")
def activity(x_api_token: Optional[str] = Header(default=None), limit: int = 30) -> Dict[str, Any]:
    limit = max(1, min(limit, 100))
    return {"events": read_activity(limit), "limit": limit}

@app.get("/avatars")
def get_avatars(x_api_token: Optional[str] = Header(default=None)) -> Response:
    if os.path.exists(AVATARS_PATH):
        with open(AVATARS_PATH, "r") as f: return Response(content=f.read(), media_type="application/json")
    return Response(content="{}", media_type="application/json")

@app.post("/avatars")
def save_avatars(payload: Dict[str, Any] = Body(default={}), x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    with open(AVATARS_PATH, "w") as f: json.dump(payload, f)
    return {"ok": True}

@app.post("/maintenance/restart-admin")
def maintenance_restart_admin(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    run_cmd(["nohup", "bash", "-c", "sleep 2 && systemctl restart wg-admin-api >/dev/null 2>&1 &"], timeout=5)
    log_activity("maintenance", "system", "", "", {"action": "restart-admin"})
    return {"ok": True, "action": "restart-admin", "message": "Restart scheduled"}

@app.post("/maintenance/reboot-server")
def maintenance_reboot_server(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    run_cmd(["nohup", "bash", "-c", "sleep 5 && reboot >/dev/null 2>&1 &"], timeout=5)
    return {"ok": True, "action": "reboot-server", "message": "Reboot scheduled"}

# ── WG / AWG specific endpoints ────────────────────────────────

@app.get("/diagnostics")
def diagnostics(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    wg_dump = try_run_cmd(["wg", "show", WG_INTERFACE, "dump"]); wg_ok = wg_dump is not None and len(wg_dump.strip()) > 0
    awg_info: Dict[str, Any] = {}
    if WG_VARIANT == "awg":
        awg_out = try_run_cmd(["awg", "show", WG_INTERFACE])
        if awg_out:
            for line in awg_out.splitlines():
                line = line.strip()
                for key in ("jc", "jmin", "jmax", "s1", "s2"):
                    if line.startswith(key + ":"):
                        val = line.split(":", 1)[1].strip()
                        if val.isdigit(): awg_info[key] = int(val); break
        awg_transfer = try_run_cmd(["awg", "show", WG_INTERFACE, "transfer"])
        if awg_transfer:
            rx_bytes = 0; tx_bytes = 0
            for line in awg_transfer.splitlines():
                line = line.strip()
                if "received" in line:
                    m = re.search(r'\((\d+)\s*B\)', line)
                    if m: rx_bytes = int(m.group(1))
                elif "sent" in line:
                    m = re.search(r'\((\d+)\s*B\)', line)
                    if m: tx_bytes = int(m.group(1))
            if rx_bytes or tx_bytes: awg_info["rx_bytes"] = rx_bytes; awg_info["tx_bytes"] = tx_bytes
    internet_ok = try_run_cmd(["ip", "route", "get", "8.8.8.8"]) is not None
    info = backup_file_info("latest.wgadmin"); backup_ok = info.get("exists") and (time.time() - info.get("mtime", 0)) < 3 * 86400
    peers_ok = False
    if wg_ok:
        parsed = parse_wg_dump(wg_dump)
        for p in parsed.get("peers", []):
            if int(p.get("latest_handshake", 0)) > 0 and p.get("online") and (time.time() - int(p.get("latest_handshake", 0))) < 1800: peers_ok = True; break
    health = _read_health(); now = int(time.time()); has_issue = not (wg_ok and internet_ok and backup_ok and peers_ok)
    if has_issue: health["last_issue_ts"] = now
    if has_issue and health.get("healthy_since") is not None: health["healthy_since"] = None
    elif not has_issue and health.get("healthy_since") is None: health["healthy_since"] = now
    _write_health(health); days_ok = _days_since(health.get("healthy_since")) if health.get("healthy_since") else 0
    cpu_pct = 0.0
    try:
        with open("/proc/stat") as f:
            parts = f.readline().strip().split()
        if parts and parts[0] == 'cpu' and len(parts) >= 5:
            user, nice, system, idle = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
            iowait = int(parts[5]) if len(parts) > 5 else 0; irq = int(parts[6]) if len(parts) > 6 else 0
            softirq = int(parts[7]) if len(parts) > 7 else 0; steal = int(parts[8]) if len(parts) > 8 else 0
            total = user + nice + system + idle + iowait + irq + softirq + steal
            if total > idle: cpu_pct = round((total - idle) / total * 100, 1)
    except Exception: pass
    mem_pct = 0.0; mem_total = 0; mem_avail = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"): mem_total = int(line.split()[1])
                elif line.startswith("MemAvailable:"): mem_avail = int(line.split()[1])
        if mem_total: mem_pct = round((1 - mem_avail / mem_total) * 100, 1)
    except Exception: pass
    disk_pct = 0.0; disk_total = 0; disk_used = 0
    try:
        out = subprocess.run(["df", "-B1", "/"], capture_output=True, text=True, timeout=5).stdout
        lines = out.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 3: disk_total = int(parts[1]); disk_used = int(parts[2])
            if disk_total: disk_pct = round(disk_used / disk_total * 100, 1)
    except Exception: pass
    return {"wg": wg_ok, "internet": internet_ok, "backup": backup_ok, "peers": peers_ok,
            "all_ok": wg_ok and internet_ok and backup_ok and peers_ok, "days_ok": days_ok,
            "checks": {"wg": "ok" if wg_ok else "fail", "internet": "ok" if internet_ok else "fail",
                        "backup": "ok" if backup_ok else "fail", "peers": "ok" if peers_ok else "fail"},
            "cpu_pct": cpu_pct, "mem_pct": mem_pct, "mem_total": mem_total, "mem_avail": mem_avail,
            "disk_pct": disk_pct, "disk_total": disk_total, "disk_used": disk_used, "awg": awg_info if awg_info else None}

@app.get("/dashboard")
def dashboard(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    peer_data = parse_wg_dump(get_wg_dump())
    total_rx = sum(p["transfer_rx_bytes"] for p in peer_data["peers"])
    total_tx = sum(p["transfer_tx_bytes"] for p in peer_data["peers"])
    vpn_today = sum(p.get("today_total_bytes", 0) for p in peer_data["peers"])
    vpn_week = sum(p.get("week_total_bytes", 0) for p in peer_data["peers"])
    vpn_month = sum(p.get("month_total_bytes", 0) for p in peer_data["peers"])
    vpn_year = sum(p.get("year_total_bytes", 0) for p in peer_data["peers"])
    vpn_saved_total = sum(p.get("saved_total_bytes", p.get("transfer_total_bytes", 0)) for p in peer_data["peers"])
    top_user_now = get_top_user_now(peer_data["peers"])
    online_peers = [{"name": p["name"], "ip": p["ip"], "latest_handshake_text": p["latest_handshake_text"],
                      "transfer_total_human": p["transfer_total_human"], "protected": p["protected"]}
                     for p in peer_data["peers"] if p["online"]]
    return {"variant": WG_VARIANT, "hostname": _get_hostname(), "uptime": get_uptime(), "cpu": get_cpu_usage(),
            "loadavg": get_loadavg(), "memory": get_memory(), "disk_root": get_disk_root(),
            "wireguard": {"interface": peer_data["interface"], "peer_count": peer_data["peer_count"],
                          "online_peer_count": peer_data["online_peer_count"],
                          "online_threshold_seconds": peer_data["online_threshold_seconds"],
                          "total_rx_bytes": total_rx, "total_tx_bytes": total_tx, "total_traffic_bytes": total_rx + total_tx,
                          "total_rx_human": bytes_to_human(total_rx), "total_tx_human": bytes_to_human(total_tx),
                          "total_traffic_human": bytes_to_human(total_rx + total_tx),
                          "vpn_today_bytes": vpn_today, "vpn_week_bytes": vpn_week, "vpn_month_bytes": vpn_month,
                          "vpn_year_bytes": vpn_year, "vpn_saved_total_bytes": vpn_saved_total,
                          "traffic_warn_bytes": read_settings().get("traffic_warn_gb", 30) * 1024 * 1024 * 1024,
                          "timezone": read_settings().get("timezone", "auto"),
                          "vpn_today_human": bytes_to_human(vpn_today), "vpn_week_human": bytes_to_human(vpn_week),
                          "vpn_month_human": bytes_to_human(vpn_month), "vpn_year_human": bytes_to_human(vpn_year),
                          "vpn_saved_total_human": bytes_to_human(vpn_saved_total),
                          "top_user_now": top_user_now, "online_peers": online_peers}}

@app.get("/peers")
def peers(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    return mark_active_peers(parse_wg_dump(get_wg_dump()))

@app.post("/peer/create")
def peer_create(payload: Dict[str, Any] = Body(...), x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    return create_peer(str(payload.get("name", "")))

@app.delete("/peer/{client_id}")
def peer_delete(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    return delete_peer(client_id)

@app.post("/peer/{client_id}/disable")
def peer_disable(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    result = disable_peer(client_id)
    overrides = read_manual_overrides(); overrides[client_id] = {"ts": int(time.time())}; write_manual_overrides(overrides)
    return result

@app.post("/peer/{client_id}/enable")
def peer_enable(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    overrides = read_manual_overrides(); overrides.pop(client_id, None); write_manual_overrides(overrides)
    return enable_peer(client_id)

@app.post("/peer/{client_id}/name")
def peer_rename(client_id: str, payload: Dict[str, Any] = Body(default={}), x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    new_name = str(payload.get("name", "")).strip()
    if not new_name: raise HTTPException(status_code=400, detail="Name is required")
    if not NAME_RE.match(new_name): raise HTTPException(status_code=400, detail="Name contains invalid characters")
    item = get_client(client_id); data = item["data"]; client = item["client"]
    old_name = client.get("name", client_id); client["name"] = new_name; atomic_json_write(CLIENTS_FILE, data, backup=True)
    log_activity("rename", old_name, client_id, client.get("address", ""), {"old_name": old_name, "new_name": new_name})
    return {"ok": True, "client_id": client_id, "name": new_name}

@app.post("/peer/{client_id}/role")
def peer_set_role(client_id: str, payload: Dict[str, Any] = Body(default={}), x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    role = str(payload.get("role", "")).strip()
    if role not in ("admin", "user"): raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")
    item = get_client(client_id); data = item["data"]; client = item["client"]
    client["role"] = role; atomic_json_write(CLIENTS_FILE, data, backup=True)
    log_activity("role_change", client.get("name", client_id), client_id, client.get("address", ""), {"role": role}); config_changed(f"peer-role:{client_id}:{role}")
    return {"ok": True, "client_id": client_id, "role": role}

@app.post("/peer/{client_id}/protect")
def peer_protect(client_id: str, payload: Dict[str, Any] = Body(default={}), x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    protected = bool(payload.get("protected", True))
    item = get_client(client_id); data = item["data"]; client = item["client"]
    name = client.get("name", client_id); client["protected"] = protected; atomic_json_write(CLIENTS_FILE, data, backup=True)
    log_activity("protect" if protected else "unprotect", name, client_id, client.get("address", ""), {"protected": protected})
    return {"ok": True, "client_id": client_id, "protected": protected}

@app.get("/peer/{client_id}/config")
def peer_config(client_id: str, proto: Optional[str] = Query(default=None), x_api_token: Optional[str] = Header(default=None), token: Optional[str] = Query(default=None)):
    config = build_client_config(client_id)
    return Response(content=config, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{client_id}.conf"'})

@app.get("/peer/{client_id}/qr")
def peer_qr(client_id: str, proto: Optional[str] = Query(default=None), x_api_token: Optional[str] = Header(default=None), token: Optional[str] = Query(default=None)):
    config = build_client_config(client_id)
    if not config: raise HTTPException(400, "No config for this protocol")
    img = qrcode.make(config); buffer = io.BytesIO(); img.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")

@app.get("/peer/{client_id}/traffic/days")
def peer_traffic_days(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    peer = find_peer_by_client_id(client_id)
    if not peer: raise HTTPException(404, "Peer not found")
    public_key = peer.get("public_key") or peer.get("pubkey") or ""
    if not public_key: return {"days": []}
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - 60 * 86400))
    with sqlite3.connect(TRAFFIC_DB_FILE, timeout=10) as db:
        rows = db.execute("SELECT period_key, rx, tx FROM traffic_totals WHERE public_key = ? AND period_type = 'day' AND period_key >= ? ORDER BY period_key ASC", (public_key, cutoff)).fetchall()
    return {"days": [{"date": r[0], "rx": int(r[1]), "tx": int(r[2])} for r in rows]}

@app.get("/peer/{client_id}/traffic/hours")
def peer_traffic_hours(client_id: str, date: Optional[str] = Query(default=None), x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    peer = find_peer_by_client_id(client_id)
    if not peer: raise HTTPException(404, "Peer not found")
    public_key = peer.get("public_key") or peer.get("pubkey") or ""
    if not public_key: return {"hours": []}
    if not date: date = today_key()
    with sqlite3.connect(TRAFFIC_DB_FILE, timeout=10) as db:
        rows = db.execute("SELECT period_key, rx, tx FROM traffic_totals WHERE public_key = ? AND period_type = 'hour' AND period_key LIKE ? ORDER BY period_key ASC", (public_key, date + "%")).fetchall()
        if rows: return {"hours": [{"h": r[0].split("-")[-1], "rx": int(r[1]), "tx": int(r[2])} for r in rows]}
        if date == today_key():
            day = read_traffic_total(db, public_key, "day", date); total_rx, total_tx = day["rx"], day["tx"]
            current_hour = int(time.strftime("%H", time.gmtime()))
            if total_rx or total_tx:
                cnt = current_hour + 1
                return {"hours": [{"h": f"{h:02d}", "rx": total_rx // cnt, "tx": total_tx // cnt} for h in range(cnt)]}
        return {"hours": []}

@app.get("/traffic/global/hours")
def global_traffic_hours(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    today = today_key()
    with sqlite3.connect(TRAFFIC_DB_FILE, timeout=10) as db:
        rows = db.execute("SELECT period_key, SUM(rx), SUM(tx) FROM traffic_totals WHERE period_type = 'hour' AND period_key LIKE ? GROUP BY period_key ORDER BY period_key ASC", (today + "%",)).fetchall()
        if rows: return {"hours": [{"h": r[0].split("-")[-1], "rx": int(r[1]), "tx": int(r[2])} for r in rows]}
    return {"hours": []}

@app.get("/traffic/global/days")
def global_traffic_days(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - 365 * 86400))
    with sqlite3.connect(TRAFFIC_DB_FILE, timeout=10) as db:
        rows = db.execute("SELECT period_key, SUM(rx), SUM(tx) FROM traffic_totals WHERE period_type = 'day' AND period_key >= ? GROUP BY period_key ORDER BY period_key ASC", (cutoff,)).fetchall()
    return {"days": [{"date": r[0], "rx": int(r[1]), "tx": int(r[2])} for r in rows]}

@app.post("/peer/{client_id}/speed-limit")
def peer_speed_limit(client_id: str, payload: Dict[str, Any] = Body(default={}), x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    rate = validate_rate(payload.get("rate"))
    overrides = read_manual_overrides(); overrides[client_id] = {"ts": int(time.time())}; write_manual_overrides(overrides)
    peer = find_peer_by_client_id(client_id)
    log_activity("parental_slow", peer.get("name", client_id), client_id, peer.get("ip", ""), {"reason": "manual"})
    return set_peer_speed_limit(client_id, True, rate)

@app.post("/peer/{client_id}/speed-normal")
def peer_speed_normal(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    overrides = read_manual_overrides(); overrides.pop(client_id, None); write_manual_overrides(overrides)
    peer = find_peer_by_client_id(client_id)
    log_activity("speed_normal", peer.get("name", client_id), client_id, peer.get("ip", ""))
    return set_peer_speed_limit(client_id, False)

@app.get("/parental/rules")
def parental_get_rules(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    return {"rules": read_parental_rules()}

@app.put("/parental/rules/{client_id}")
def parental_set_rule(client_id: str, payload: Dict[str, Any] = Body(default={}), x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    rules = read_parental_rules(); enabled = bool(payload.get("enabled", False))
    if enabled:
        schedule = payload.get("schedule")
        if schedule and isinstance(schedule, dict): schedule.setdefault("enabled", False)
        rules[client_id] = {"enabled": enabled, "daily_bytes": int(payload.get("daily_bytes", 0)),
                            "speed_limit_threshold": int(payload.get("speed_limit_threshold", 0)),
                            "speed_limit_rate": str(payload.get("speed_limit_rate")) if "speed_limit_rate" in payload else "256kbit",
                            "auto_enable": bool(payload.get("auto_enable", True)), "schedule": schedule, "updated_ts": int(time.time())}
    else: rules.pop(client_id, None)
    write_parental_rules(rules); overrides = read_manual_overrides(); overrides.pop(client_id, None); write_manual_overrides(overrides)
    enforce_parental_limits()
    return {"ok": True, "client_id": client_id, "enabled": enabled}

@app.post("/maintenance/restart-vpn")
def maintenance_restart_vpn(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    wg_conf_path = os.path.join(APP_DIR, "wg0.conf")
    if IS_CONTAINER and os.path.exists(wg_conf_path):
        try_run_cmd(["wg-quick", "down", wg_conf_path], timeout=10)
        run_cmd(["wg-quick", "up", wg_conf_path], timeout=10); out = "wg-quick restarted"
    elif not IS_CONTAINER: out = run_cmd(["docker", "restart", WG_CONTAINER], timeout=30)
    else: out = "vpn restart skipped (config not found)"
    log_activity("maintenance", "system", "", "", {"action": "restart-vpn"})
    return {"ok": True, "action": "restart-vpn", "output": out}
