import io
import ipaddress
import json
import os
import shutil
import sqlite3
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

import qrcode
from fastapi import Query, Body, FastAPI, Header, HTTPException, Response
from pathlib import Path
import datetime

def config_changed(reason: str = ""):
    """
    Called when persistent VPN/Admin configuration changes.
    Creates an async backup without blocking API response.
    """
    try:
        print(f"[config_changed] {reason}")
        subprocess.Popen(
            ["/usr/local/bin/wg-admin-backup"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[backup] failed: {e}")




APP_DIR = "/root/wg-admin-api"
TOKEN_FILE = os.path.join(APP_DIR, "api_token")

WG_INTERFACE = os.environ.get("WG_INTERFACE", "wg0")
BACKUP_DIR = Path("/var/lib/wg-admin/backups")
WG_EASY_CONTAINER = os.environ.get("WG_EASY_CONTAINER", "wg-easy")
WG_EASY_JSON = os.environ.get("WG_EASY_JSON", "/root/.wg-easy/wg0.json")

WG_HOST = os.environ.get("WG_HOST", "147.45.169.35")
WG_PORT = os.environ.get("WG_PORT", "51820")
WG_DNS = os.environ.get("WG_DNS", "1.1.1.1")
WG_ALLOWED_IPS = os.environ.get("WG_ALLOWED_IPS", "0.0.0.0/0, ::/0")
ONLINE_THRESHOLD_SECONDS = int(os.environ.get("ONLINE_THRESHOLD_SECONDS", "1800"))
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
TRAFFIC_STATS_FILE = os.path.join(APP_DIR, "traffic_stats.json")
TRAFFIC_DB_FILE = os.path.join(APP_DIR, "traffic_stats.sqlite")
TOP_USER_EVENT_FILE = os.path.join(APP_DIR, "top_user_event.json")

PROTECTED_PEERS = {"VadimSmart", "VadimWork", "Router"}
ACTIVITY_LOG = os.path.join(APP_DIR, "activity.log")
SPEED_LIMITS_FILE = os.path.join(APP_DIR, "speed_limits.json")

LIVE_TRAFFIC_PREVIOUS: Dict[str, Dict[str, Any]] = {}

app = FastAPI(title="WG Admin API", version="0.4.0")

init_traffic_db()


def read_settings() -> Dict[str, Any]:
    defaults = {
        "display_name": "Family VPN",
    }

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                defaults.update(data)
    except FileNotFoundError:
        pass
    except Exception:
        pass

    return defaults


def write_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    current = read_settings()
    current.update(data)

    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)

    return current


def log_activity(action: str, peer: str, client_id: str, ip: str = "", details: Optional[Dict[str, Any]] = None) -> None:
    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,
        "peer": peer,
        "client_id": client_id,
        "ip": ip,
        "details": details or {},
    }

    with open(ACTIVITY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_speed_limits() -> Dict[str, Any]:
    try:
        with open(SPEED_LIMITS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_speed_limits(data: Dict[str, Any]) -> None:
    tmp = f"{SPEED_LIMITS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SPEED_LIMITS_FILE)


def apply_speed_limits() -> None:
    limits = read_speed_limits()

    try_run_cmd(["docker", "exec", WG_EASY_CONTAINER, "tc", "qdisc", "del", "dev", WG_INTERFACE, "root"], timeout=5)

    active = {
        ip: item for ip, item in limits.items()
        if isinstance(item, dict) and item.get("enabled") and item.get("rate")
    }

    if not active:
        return

    script = [
        f'DEV="{WG_INTERFACE}"',
        'tc qdisc add dev "$DEV" root handle 1: htb default 999',
        'tc class add dev "$DEV" parent 1: classid 1:1 htb rate 200mbit ceil 200mbit',
        'tc class add dev "$DEV" parent 1:1 classid 1:999 htb rate 200mbit ceil 200mbit',
    ]

    idx = 100
    for ip, item in active.items():
        idx += 1
        rate = str(item.get("rate", "256kbit"))
        class_id = f"1:{idx}"
        script.append(f'tc class add dev "$DEV" parent 1:1 classid {class_id} htb rate {rate} ceil {rate}')
        script.append(f'tc filter add dev "$DEV" protocol ip parent 1:0 prio {idx} u32 match ip dst {ip}/32 flowid {class_id}')

    run_cmd(["docker", "exec", WG_EASY_CONTAINER, "sh", "-c", "\n".join(script)], timeout=10)


def set_peer_speed_limit(client_id: str, enabled: bool, rate: str = "256kbit") -> Dict[str, Any]:
    peer = find_peer_by_client_id(client_id)
    ip = peer.get("ip")
    name = peer.get("name", client_id)

    if not ip:
        raise HTTPException(status_code=400, detail="Peer IP not found")

    limits = read_speed_limits()

    if enabled:
        limits[ip] = {
            "enabled": True,
            "rate": rate,
            "client_id": client_id,
            "name": name,
            "updated_ts": int(time.time()),
        }
    else:
        limits.pop(ip, None)

    write_speed_limits(limits)
    apply_speed_limits()

    log_activity(
        action="speed-limit" if enabled else "speed-normal",
        peer=name,
        client_id=client_id,
        ip=ip,
        details={"rate": rate if enabled else None},
    )

    return {
        "ok": True,
        "peer": name,
        "ip": ip,
        "client_id": client_id,
        "speed_limited": enabled,
        "rate": rate if enabled else None,
    }


def read_activity(limit: int = 30) -> List[Dict[str, Any]]:
    try:
        with open(ACTIVITY_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    events = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue

    events.reverse()
    return events


def read_token() -> str:
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def require_auth(x_api_token: Optional[str]) -> None:
    expected = read_token()
    if not expected:
        raise HTTPException(status_code=500, detail="API token is not configured")
    if not x_api_token or x_api_token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def run_cmd(cmd: List[str], timeout: int = 8, input_text: Optional[str] = None) -> str:
    try:
        result = subprocess.run(cmd, text=True, input=input_text, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail=f"Command timeout: {' '.join(cmd)}")

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())

    return result.stdout.strip()


def try_run_cmd(cmd: List[str], timeout: int = 8) -> Optional[str]:
    try:
        return run_cmd(cmd, timeout=timeout)
    except Exception:
        return None


def get_wg_dump() -> str:
    host_dump = try_run_cmd(["wg", "show", WG_INTERFACE, "dump"])
    if host_dump:
        return host_dump

    if shutil.which("docker"):
        container_dump = try_run_cmd(
            ["docker", "exec", WG_EASY_CONTAINER, "wg", "show", WG_INTERFACE, "dump"]
        )
        if container_dump:
            return container_dump

    raise HTTPException(status_code=500, detail=f"Cannot read WireGuard dump for {WG_INTERFACE}")


def bytes_to_human(num: int) -> str:
    value = float(num)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{num} B"


def handshake_to_text(ts: int) -> str:
    if ts <= 0:
        return "never"

    diff = max(0, int(time.time()) - ts)

    if diff < 60:
        return f"{diff} seconds ago"
    if diff < 3600:
        return f"{diff // 60} minutes ago"
    if diff < 86400:
        return f"{diff // 3600} hours ago"

    return f"{diff // 86400} days ago"


def extract_peer_ip(allowed_ips: str) -> str:
    if not allowed_ips:
        return ""
    return allowed_ips.split(",")[0].strip().split("/")[0].strip()


def read_wg_easy_data() -> Dict[str, Any]:
    try:
        with open(WG_EASY_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read wg-easy JSON: {e}")


def read_wg_easy_clients() -> Dict[str, Dict[str, Dict[str, Any]]]:
    data = read_wg_easy_data()
    clients = data.get("clients", {})

    by_public_key: Dict[str, Dict[str, Any]] = {}
    by_ip: Dict[str, Dict[str, Any]] = {}

    if not isinstance(clients, dict):
        return {"by_public_key": by_public_key, "by_ip": by_ip}

    for client_id, client in clients.items():
        if not isinstance(client, dict):
            continue

        name = str(client.get("name", "")).strip()
        address = str(client.get("address", "")).strip()
        public_key = str(client.get("publicKey", "")).strip()
        enabled = bool(client.get("enabled", True))

        item = {
            "client_id": str(client_id),
            "name": name or address or public_key[:10],
            "address": address,
            "public_key": public_key,
            "enabled": enabled,
        }

        if public_key:
            by_public_key[public_key] = item
        if address:
            by_ip[address] = item

    return {"by_public_key": by_public_key, "by_ip": by_ip}


def seconds_to_human(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def today_key() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def week_key() -> str:
    return time.strftime("%G-W%V", time.localtime())


def month_key() -> str:
    return time.strftime("%Y-%m", time.localtime())


def read_traffic_stats() -> Dict[str, Any]:
    try:
        with open(TRAFFIC_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except FileNotFoundError:
        pass
    except Exception:
        pass

    return {"daily": {}, "weekly": {}, "monthly": {}}


def write_traffic_stats(data: Dict[str, Any]) -> None:
    tmp = f"{TRAFFIC_STATS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TRAFFIC_STATS_FILE)


def traffic_delta(current: int, baseline: int) -> int:
    if current < baseline:
        return current
    return current - baseline



def init_traffic_db() -> None:
    with sqlite3.connect(TRAFFIC_DB_FILE) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS peer_counters (
                public_key TEXT PRIMARY KEY,
                last_rx INTEGER NOT NULL DEFAULT 0,
                last_tx INTEGER NOT NULL DEFAULT 0,
                updated_ts INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS traffic_totals (
                public_key TEXT NOT NULL,
                period_type TEXT NOT NULL,
                period_key TEXT NOT NULL,
                rx INTEGER NOT NULL DEFAULT 0,
                tx INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (public_key, period_type, period_key)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS online_totals (
                public_key TEXT NOT NULL,
                day_key TEXT NOT NULL,
                seconds INTEGER NOT NULL DEFAULT 0,
                last_seen_ts INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (public_key, day_key)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS peer_last_seen (
                public_key TEXT PRIMARY KEY,
                last_seen INTEGER NOT NULL DEFAULT 0,
                updated_ts INTEGER NOT NULL DEFAULT 0
            )
        """)


def save_peer_last_seen(public_key: str, latest_handshake: int) -> int:
    if not public_key or latest_handshake <= 0:
        return 0
    now = int(time.time())
    with sqlite3.connect(TRAFFIC_DB_FILE) as db:
        row = db.execute("SELECT last_seen FROM peer_last_seen WHERE public_key = ?", (public_key,)).fetchone()
        old_last_seen = int(row[0]) if row else 0
        new_last_seen = max(old_last_seen, int(latest_handshake))
        db.execute("""
            INSERT INTO peer_last_seen (public_key, last_seen, updated_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(public_key)
            DO UPDATE SET
                last_seen = CASE
                    WHEN excluded.last_seen > peer_last_seen.last_seen
                    THEN excluded.last_seen
                    ELSE peer_last_seen.last_seen
                END,
                updated_ts = excluded.updated_ts
        """, (public_key, new_last_seen, now))
        return new_last_seen


def get_peer_last_seen(public_key: str) -> int:
    if not public_key:
        return 0
    with sqlite3.connect(TRAFFIC_DB_FILE) as db:
        row = db.execute("SELECT last_seen FROM peer_last_seen WHERE public_key = ?", (public_key,)).fetchone()
    return int(row[0]) if row else 0


def add_traffic_total(db, public_key: str, period_type: str, period_key: str, rx_delta: int, tx_delta: int) -> None:
    db.execute("""
        INSERT INTO traffic_totals (public_key, period_type, period_key, rx, tx)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(public_key, period_type, period_key)
        DO UPDATE SET rx = rx + excluded.rx, tx = tx + excluded.tx
    """, (public_key, period_type, period_key, rx_delta, tx_delta))


def read_traffic_total(db, public_key: str, period_type: str, period_key: str) -> Dict[str, int]:
    row = db.execute("""
        SELECT rx, tx FROM traffic_totals
        WHERE public_key = ? AND period_type = ? AND period_key = ?
    """, (public_key, period_type, period_key)).fetchone()
    if not row:
        return {"rx": 0, "tx": 0}
    return {"rx": int(row[0] or 0), "tx": int(row[1] or 0)}


def update_online_total(db, public_key: str, day: str, online: bool, now_ts: int) -> int:
    row = db.execute("""
        SELECT seconds, last_seen_ts FROM online_totals
        WHERE public_key = ? AND day_key = ?
    """, (public_key, day)).fetchone()

    if not row:
        db.execute("""
            INSERT INTO online_totals (public_key, day_key, seconds, last_seen_ts)
            VALUES (?, ?, 0, ?)
        """, (public_key, day, now_ts if online else 0))
        return 0

    seconds = int(row[0] or 0)
    last_seen = int(row[1] or 0)

    if online:
        if last_seen > 0:
            seconds += min(max(0, now_ts - last_seen), 120)
        db.execute("""
            UPDATE online_totals
            SET seconds = ?, last_seen_ts = ?
            WHERE public_key = ? AND day_key = ?
        """, (seconds, now_ts, public_key, day))
    else:
        db.execute("""
            UPDATE online_totals
            SET last_seen_ts = 0
            WHERE public_key = ? AND day_key = ?
        """, (public_key, day))

    return seconds


def cleanup_traffic_db(db) -> None:
    cutoff_day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 180 * 86400))
    db.execute("DELETE FROM traffic_totals WHERE period_type = 'day' AND period_key < ?", (cutoff_day,))
    db.execute("DELETE FROM online_totals WHERE day_key < ?", (cutoff_day,))



def get_period_traffic(public_key: str, rx: int, tx: int, online: bool = False) -> Dict[str, Any]:

    today = today_key()
    week = week_key()
    month = month_key()
    now_ts = int(time.time())

    with sqlite3.connect(TRAFFIC_DB_FILE) as db:
        row = db.execute(
            "SELECT last_rx, last_tx FROM peer_counters WHERE public_key = ?",
            (public_key,),
        ).fetchone()

        # Если peer отключён и переданы нули, не сбрасываем последнюю точку.
        ignore_zero_offline = bool(row) and rx == 0 and tx == 0 and not online

        if row is None:
            rx_delta = 0
            tx_delta = 0
            db.execute(
                "INSERT INTO peer_counters (public_key, last_rx, last_tx, updated_ts) VALUES (?, ?, ?, ?)",
                (public_key, rx, tx, now_ts),
            )
        elif ignore_zero_offline:
            rx_delta = 0
            tx_delta = 0
        else:
            last_rx = int(row[0] or 0)
            last_tx = int(row[1] or 0)

            rx_delta = rx - last_rx if rx >= last_rx else rx
            tx_delta = tx - last_tx if tx >= last_tx else tx

            rx_delta = max(0, rx_delta)
            tx_delta = max(0, tx_delta)

            db.execute(
                "UPDATE peer_counters SET last_rx = ?, last_tx = ?, updated_ts = ? WHERE public_key = ?",
                (rx, tx, now_ts, public_key),
            )

        if rx_delta or tx_delta:
            add_traffic_total(db, public_key, "day", today, rx_delta, tx_delta)
            add_traffic_total(db, public_key, "week", week, rx_delta, tx_delta)
            add_traffic_total(db, public_key, "month", month, rx_delta, tx_delta)
            add_traffic_total(db, public_key, "total", "all", rx_delta, tx_delta)

        online_today_seconds = update_online_total(db, public_key, today, online, now_ts)

        day = read_traffic_total(db, public_key, "day", today)
        week_total = read_traffic_total(db, public_key, "week", week)
        month_total = read_traffic_total(db, public_key, "month", month)
        total = read_traffic_total(db, public_key, "total", "all")

        cleanup_traffic_db(db)

    day_rx, day_tx = day["rx"], day["tx"]
    week_rx, week_tx = week_total["rx"], week_total["tx"]
    month_rx, month_tx = month_total["rx"], month_total["tx"]
    total_rx, total_tx = total["rx"], total["tx"]

    return {
        "today_rx_bytes": day_rx,
        "today_tx_bytes": day_tx,
        "today_total_bytes": day_rx + day_tx,
        "today_rx_human": bytes_to_human(day_rx),
        "today_tx_human": bytes_to_human(day_tx),
        "today_total_human": bytes_to_human(day_rx + day_tx),

        "week_rx_bytes": week_rx,
        "week_tx_bytes": week_tx,
        "week_total_bytes": week_rx + week_tx,
        "week_rx_human": bytes_to_human(week_rx),
        "week_tx_human": bytes_to_human(week_tx),
        "week_total_human": bytes_to_human(week_rx + week_tx),

        "month_rx_bytes": month_rx,
        "month_tx_bytes": month_tx,
        "month_total_bytes": month_rx + month_tx,
        "month_rx_human": bytes_to_human(month_rx),
        "month_tx_human": bytes_to_human(month_tx),
        "month_total_human": bytes_to_human(month_rx + month_tx),

        "saved_total_rx_bytes": total_rx,
        "saved_total_tx_bytes": total_tx,
        "saved_total_bytes": total_rx + total_tx,
        "saved_total_human": bytes_to_human(total_rx + total_tx),

        "online_today_seconds": online_today_seconds,
        "online_today_human": seconds_to_human(online_today_seconds),
    }




def parse_wg_dump(dump: str) -> Dict[str, Any]:
    lines = [line for line in dump.splitlines() if line.strip()]

    data = read_wg_easy_data()
    clients = data.get("clients", {})

    live_by_public_key: Dict[str, Dict[str, Any]] = {}

    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 8:
            continue

        public_key = parts[0]
        preshared_key = parts[1]
        endpoint = parts[2]
        allowed_ips = parts[3]
        latest_handshake = int(parts[4] or "0")
        transfer_rx = int(parts[5] or "0")
        transfer_tx = int(parts[6] or "0")
        persistent_keepalive = parts[7]

        online = latest_handshake > 0 and int(time.time()) - latest_handshake < ONLINE_THRESHOLD_SECONDS

        period = get_period_traffic(public_key, transfer_rx, transfer_tx, online)
        saved_last_seen = save_peer_last_seen(public_key, latest_handshake)

        live_by_public_key[public_key] = {
            "endpoint": endpoint if endpoint != "(none)" else None,
            "allowed_ips": allowed_ips,
            "latest_handshake": latest_handshake,
            "latest_handshake_text": handshake_to_text(saved_last_seen or latest_handshake),
            "saved_last_seen": saved_last_seen,
            "saved_last_seen_text": handshake_to_text(saved_last_seen),
            "online": online,
            "is_active_now": False,
            "transfer_rx_bytes": transfer_rx,
            "transfer_tx_bytes": transfer_tx,
            "transfer_total_bytes": transfer_rx + transfer_tx,
            "transfer_rx_human": bytes_to_human(transfer_rx),
            "transfer_tx_human": bytes_to_human(transfer_tx),
            "transfer_total_human": bytes_to_human(transfer_rx + transfer_tx),
            **period,
            "persistent_keepalive": persistent_keepalive,
            "has_preshared_key": preshared_key not in ("", "(none)"),
        }

    peers = []

    if isinstance(clients, dict):
        for client_id, client in clients.items():
            if not isinstance(client, dict):
                continue

            name = str(client.get("name", "")).strip()
            address = str(client.get("address", "")).strip()
            public_key = str(client.get("publicKey", "")).strip()
            enabled = bool(client.get("enabled", True))

            live = live_by_public_key.get(public_key)

            if live:
                endpoint = live["endpoint"]
                allowed_ips = live["allowed_ips"]
                latest_handshake = live["latest_handshake"]
                latest_handshake_text = live["latest_handshake_text"]
                online = live["online"]
                transfer_rx = live["transfer_rx_bytes"]
                transfer_tx = live["transfer_tx_bytes"]
                transfer_total = live["transfer_total_bytes"]
                transfer_rx_human = live["transfer_rx_human"]
                transfer_tx_human = live["transfer_tx_human"]
                transfer_total_human = live["transfer_total_human"]
                persistent_keepalive = live["persistent_keepalive"]
                has_preshared_key = live["has_preshared_key"]
            else:
                endpoint = None
                allowed_ips = f"{address}/32" if address else ""
                latest_handshake = 0
                saved_last_seen = get_peer_last_seen(public_key)
                latest_handshake_text = handshake_to_text(saved_last_seen) if saved_last_seen else "never"
                online = False
                transfer_rx = 0
                transfer_tx = 0
                transfer_total = 0
                transfer_rx_human = bytes_to_human(0)
                transfer_tx_human = bytes_to_human(0)
                transfer_total_human = bytes_to_human(0)
                persistent_keepalive = "off"
                has_preshared_key = bool(client.get("preSharedKey"))
                if public_key:
                    traffic_stats = read_traffic_stats()
                    last = traffic_stats.get("last", {}).get(public_key, {"rx": 0, "tx": 0})
                    period = get_period_traffic(public_key, int(last.get("rx", 0)), int(last.get("tx", 0)))
                else:
                    period = {}

            peer_name = name or address or public_key[:10]
            speed_limits = read_speed_limits()
            speed_item = speed_limits.get(address, {}) if address else {}

            peers.append({
                "id": public_key[:12],
                "name": peer_name,
                "ip": address,
                "client_id": str(client_id),
                "enabled": enabled,
                "protected": peer_name in PROTECTED_PEERS,
                "public_key": public_key,
                "public_key_short": public_key[:10] + "..." if public_key else "",
                "endpoint": endpoint,
                "allowed_ips": allowed_ips,
                "latest_handshake": latest_handshake,
                "latest_handshake_text": latest_handshake_text,
                "online": online,
                "transfer_rx_bytes": transfer_rx,
                "transfer_tx_bytes": transfer_tx,
                "transfer_total_bytes": transfer_total,
                "transfer_rx_human": transfer_rx_human,
                "transfer_tx_human": transfer_tx_human,
                "transfer_total_human": transfer_total_human,
                **(live if live else period),
                "persistent_keepalive": persistent_keepalive,
                "has_preshared_key": has_preshared_key,
            })

    speed_limits = read_speed_limits()
    for peer in peers:
        ip = peer.get("ip")
        item = speed_limits.get(ip, {}) if ip else {}
        peer["speed_limited"] = bool(item.get("enabled"))
        peer["speed_limit_rate"] = item.get("rate")

    peers.sort(key=lambda peer: peer.get("ip") or "")

    return {
        "interface": WG_INTERFACE,
        "peers": peers,
        "peer_count": len(peers),
        "online_peer_count": sum(1 for peer in peers if peer["online"]),
        "enabled_peer_count": sum(1 for peer in peers if peer["enabled"]),
        "disabled_peer_count": sum(1 for peer in peers if not peer["enabled"]),
        "online_threshold_seconds": ONLINE_THRESHOLD_SECONDS,
    }


def mark_active_peers(peer_data: Dict[str, Any]) -> Dict[str, Any]:
    top = get_top_user_now(peer_data.get("peers", []))

    peer_data["active_peer_count"] = int(top.get("active_peer_count", 0) or 0)
    peer_data["top_user_now"] = top

    return peer_data


def find_peer_by_client_id(client_id: str) -> Dict[str, Any]:
    data = parse_wg_dump(get_wg_dump())
    for peer in data["peers"]:
        if peer.get("client_id") == client_id:
            return peer
    raise HTTPException(status_code=404, detail=f"Peer not found: {client_id}")


def can_disable_peer(client_id: str) -> Dict[str, Any]:
    peer = find_peer_by_client_id(client_id)

    if peer["name"] in PROTECTED_PEERS:
        return {
            "allowed": False,
            "peer": peer["name"],
            "ip": peer["ip"],
            "client_id": client_id,
            "reason": "Protected peer",
        }

    data = parse_wg_dump(get_wg_dump())
    online_count = data["online_peer_count"]

    if peer["online"] and online_count <= 1:
        return {
            "allowed": False,
            "peer": peer["name"],
            "ip": peer["ip"],
            "client_id": client_id,
            "reason": "Last online peer",
        }

    return {
        "allowed": True,
        "peer": peer["name"],
        "ip": peer["ip"],
        "client_id": client_id,
        "reason": None,
    }



def disable_peer(client_id: str) -> Dict[str, Any]:
    check = can_disable_peer(client_id)

    if not check["allowed"]:
        raise HTTPException(status_code=403, detail=check)

    item = get_client_from_wg_easy(client_id)
    data = item["data"]
    client = item["client"]

    public_key = client.get("publicKey")
    name = client.get("name", client_id)
    address = client.get("address")

    if not public_key:
        raise HTTPException(status_code=500, detail="Client publicKey not found")

    backup_path = None

    client["enabled"] = False

    tmp_path = f"{WG_EASY_JSON}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, WG_EASY_JSON)

    try:
        run_cmd(
            ["docker", "exec", WG_EASY_CONTAINER, "wg", "set", WG_INTERFACE, "peer", public_key, "remove"],
            timeout=8,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Peer disabled in JSON but live wg remove failed",
                "error": str(e),
                "backup": backup_path,
            },
        )

    log_activity(
        action="disable",
        peer=name,
        client_id=client_id,
        ip=address or "",
        details={"backup": backup_path, "public_key_short": public_key[:10] + "..."},
    )

    return {
        "disabled": True,
        "peer": name,
        "ip": address,
        "client_id": client_id,
        "public_key_short": public_key[:10] + "...",
        "backup": backup_path,
    }


def enable_peer(client_id: str) -> Dict[str, Any]:
    item = get_client_from_wg_easy(client_id)
    data = item["data"]
    client = item["client"]

    name = client.get("name", client_id)
    address = client.get("address")
    public_key = client.get("publicKey")
    preshared_key = client.get("preSharedKey")

    if not address:
        raise HTTPException(status_code=500, detail="Client address not found")
    if not public_key:
        raise HTTPException(status_code=500, detail="Client publicKey not found")
    if not preshared_key:
        raise HTTPException(status_code=500, detail="Client preSharedKey not found")

    backup_path = None

    client["enabled"] = True

    tmp_path = f"{WG_EASY_JSON}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, WG_EASY_JSON)

    psk_tmp = f"/tmp/wg-psk-{client_id}"
    try:
        run_cmd(
            [
                "docker",
                "exec",
                WG_EASY_CONTAINER,
                "sh",
                "-c",
                f"umask 077 && cat > {psk_tmp} <<'EOF'\n{preshared_key}\nEOF",
            ],
            timeout=8,
        )

        run_cmd(
            [
                "docker",
                "exec",
                WG_EASY_CONTAINER,
                "wg",
                "set",
                WG_INTERFACE,
                "peer",
                public_key,
                "preshared-key",
                psk_tmp,
                "allowed-ips",
                f"{address}/32",
            ],
            timeout=8,
        )
    finally:
        try_run_cmd(["docker", "exec", WG_EASY_CONTAINER, "rm", "-f", psk_tmp], timeout=5)

    log_activity(
        action="enable",
        peer=name,
        client_id=client_id,
        ip=address or "",
        details={"backup": backup_path, "public_key_short": public_key[:10] + "...", "method": "live wg set peer"},
    )

    return {
        "enabled": True,
        "peer": name,
        "ip": address,
        "client_id": client_id,
        "public_key_short": public_key[:10] + "...",
        "backup": backup_path,
        "method": "live wg set peer",
    }


def get_client_from_wg_easy(client_id: str) -> Dict[str, Any]:
    data = read_wg_easy_data()
    clients = data.get("clients", {})
    client = clients.get(client_id)

    if not client:
        raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")

    return {"data": data, "client": client}



def allocate_next_client_ip(data: Dict[str, Any]) -> str:
    server_address = data.get("server", {}).get("address", "10.8.0.1")
    network = ipaddress.ip_network(f"{server_address}/24", strict=False)

    used = {str(network.network_address), str(network.broadcast_address), server_address}

    for client in data.get("clients", {}).values():
        if isinstance(client, dict) and client.get("address"):
            used.add(str(client["address"]))

    for ip in network.hosts():
        ip_str = str(ip)
        if ip_str not in used:
            return ip_str

    raise HTTPException(status_code=500, detail="No free VPN IP addresses")


def create_peer(name: str) -> Dict[str, Any]:
    name = name.strip()

    if not name:
        raise HTTPException(status_code=400, detail="Client name is required")

    data = read_wg_easy_data()
    clients = data.setdefault("clients", {})

    for client in clients.values():
        if isinstance(client, dict) and client.get("name") == name:
            raise HTTPException(status_code=409, detail=f"Client already exists: {name}")

    client_id = run_cmd(["cat", "/proc/sys/kernel/random/uuid"])
    address = allocate_next_client_ip(data)

    private_key = run_cmd(["docker", "exec", WG_EASY_CONTAINER, "wg", "genkey"])
    public_key = run_cmd([
        "docker", "exec", "-i", WG_EASY_CONTAINER, "wg", "pubkey"
    ], input_text=private_key + "\n")
    preshared_key = run_cmd(["docker", "exec", WG_EASY_CONTAINER, "wg", "genpsk"])

    backup_path = f"{WG_EASY_JSON}.bak-{int(time.time())}"
    shutil.copy2(WG_EASY_JSON, backup_path)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    clients[client_id] = {
        "name": name,
        "address": address,
        "privateKey": private_key,
        "publicKey": public_key,
        "preSharedKey": preshared_key,
        "createdAt": now,
        "updatedAt": now,
        "enabled": True,
    }

    tmp_path = f"{WG_EASY_JSON}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, WG_EASY_JSON)

    psk_tmp = f"/tmp/wg-psk-{client_id}"

    try:
        run_cmd(
            [
                "docker", "exec", WG_EASY_CONTAINER, "sh", "-c",
                f"umask 077 && cat > {psk_tmp} <<'EOF'\n{preshared_key}\nEOF",
            ],
            timeout=8,
        )

        run_cmd(
            [
                "docker", "exec", WG_EASY_CONTAINER,
                "wg", "set", WG_INTERFACE,
                "peer", public_key,
                "preshared-key", psk_tmp,
                "allowed-ips", f"{address}/32",
            ],
            timeout=8,
        )
    finally:
        try_run_cmd(["docker", "exec", WG_EASY_CONTAINER, "rm", "-f", psk_tmp], timeout=5)

    log_activity(
        action="create",
        peer=name,
        client_id=client_id,
        ip=address,
        details={"backup": backup_path, "public_key_short": public_key[:10] + "..."},
    )
    config_changed(f"peer-created:{name}")

    return {
        "created": True,
        "peer": name,
        "ip": address,
        "client_id": client_id,
        "public_key_short": public_key[:10] + "...",
        "backup": backup_path,
    }




def delete_peer(client_id: str) -> Dict[str, Any]:
    item = get_client_from_wg_easy(client_id)
    data = item["data"]
    client = item["client"]

    name = client.get("name", client_id)
    address = client.get("address", "")
    public_key = client.get("publicKey", "")

    backup_path = f"{WG_EASY_JSON}.bak-{int(time.time())}"
    shutil.copy2(WG_EASY_JSON, backup_path)

    clients = data.get("clients", {})
    clients.pop(client_id, None)

    tmp_path = f"{WG_EASY_JSON}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, WG_EASY_JSON)

    if public_key:
        try_run_cmd(
            ["docker", "exec", WG_EASY_CONTAINER, "wg", "set", WG_INTERFACE, "peer", public_key, "remove"],
            timeout=8,
        )

    log_activity(
        action="delete",
        peer=name,
        client_id=client_id,
        ip=address or "",
        details={"backup": backup_path, "public_key_short": public_key[:10] + "..." if public_key else ""},
    )
    config_changed(f"peer-deleted:{name}")

    return {
        "deleted": True,
        "peer": name,
        "ip": address,
        "client_id": client_id,
        "backup": backup_path,
    }



def build_client_config(client_id: str) -> str:
    item = get_client_from_wg_easy(client_id)
    data = item["data"]
    client = item["client"]
    server = data.get("server", {})

    client_private_key = client.get("privateKey")
    client_address = client.get("address")
    client_preshared_key = client.get("preSharedKey")
    server_public_key = server.get("publicKey")

    if not client_private_key:
        raise HTTPException(status_code=500, detail="Client privateKey not found")
    if not client_address:
        raise HTTPException(status_code=500, detail="Client address not found")
    if not server_public_key:
        raise HTTPException(status_code=500, detail="Server publicKey not found")

    lines = [
        "[Interface]",
        f"PrivateKey = {client_private_key}",
        f"Address = {client_address}/32",
        f"DNS = {WG_DNS}",
        "",
        "[Peer]",
        f"PublicKey = {server_public_key}",
    ]

    if client_preshared_key:
        lines.append(f"PresharedKey = {client_preshared_key}")

    lines.extend([
        f"AllowedIPs = {WG_ALLOWED_IPS}",
        f"Endpoint = {WG_HOST}:{WG_PORT}",
        "PersistentKeepalive = 25",
        "",
    ])

    return "\n".join(lines)


def get_loadavg() -> Dict[str, str]:
    with open("/proc/loadavg", "r", encoding="utf-8") as f:
        one, five, fifteen, *_ = f.read().split()
    return {"1m": one, "5m": five, "15m": fifteen}



def get_cpu_usage() -> Dict[str, Any]:
    def read_cpu():
        with open("/proc/stat", "r", encoding="utf-8") as f:
            fields = list(map(int, f.readline().split()[1:]))

        idle = fields[3] + fields[4]
        total = sum(fields)
        return idle, total

    samples = []

    idle1, total1 = read_cpu()
    time.sleep(0.2)
    idle2, total2 = read_cpu()

    total_delta = total2 - total1
    idle_delta = idle2 - idle1

    if total_delta > 0:
        usage = 100.0 * (1.0 - idle_delta / total_delta)
        samples.append(max(0.0, min(100.0, usage)))

    if not samples:
        percent = 0.0
    else:
        percent = sum(samples) / len(samples)

    return {
        "percent": round(percent, 1)
    }


def get_memory() -> Dict[str, Any]:
    data = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            key, value = line.split(":", 1)
            data[key] = int(value.strip().split()[0]) * 1024

    total = data.get("MemTotal", 0)
    available = data.get("MemAvailable", 0)
    used = max(0, total - available)

    return {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "total_human": bytes_to_human(total),
        "used_human": bytes_to_human(used),
        "available_human": bytes_to_human(available),
        "used_percent": round((used / total) * 100, 2) if total else 0,
    }


def get_disk_root() -> Dict[str, Any]:
    usage = shutil.disk_usage("/")
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_human": bytes_to_human(usage.total),
        "used_human": bytes_to_human(usage.used),
        "free_human": bytes_to_human(usage.free),
        "used_percent": round((usage.used / usage.total) * 100, 2) if usage.total else 0,
    }


def get_uptime() -> Dict[str, Any]:
    with open("/proc/uptime", "r", encoding="utf-8") as f:
        seconds = int(float(f.read().split()[0]))

    return {
        "seconds": seconds,
        "human": f"{seconds // 86400}d {(seconds % 86400) // 3600}h {(seconds % 3600) // 60}m",
    }



@app.get("/app")
def web_app():
    with open("/root/wg-admin-api/web/index.html", "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="text/html; charset=utf-8")


@app.get("/app.css")
def web_css():
    with open("/root/wg-admin-api/web/app.css", "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="text/css; charset=utf-8")


@app.get("/app.js")
def web_js():
    with open("/root/wg-admin-api/web/app.js", "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="application/javascript; charset=utf-8")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "service": "wg-admin-api", "version": "0.4.0", "hostname": socket.gethostname()}


@app.get("/settings")
def get_settings(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    return read_settings()


@app.post("/settings")
def update_settings(
    payload: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    require_auth(x_api_token)

    display_name = str(payload.get("display_name", "")).strip()

    if not display_name:
        raise HTTPException(status_code=400, detail="display_name is required")

    if len(display_name) > 40:
        raise HTTPException(status_code=400, detail="display_name is too long")

    result = write_settings({"display_name": display_name})
    config_changed("settings-updated")
    return result


@app.get("/status")
def status(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    return {
        "hostname": socket.gethostname(),
        "display_name": read_settings().get("display_name", "Family VPN"),
        "uptime": get_uptime(),
        "cpu": get_cpu_usage(),
        "loadavg": get_loadavg(),
        "memory": get_memory(),
        "disk_root": get_disk_root(),
    }


@app.post("/peer/create")
def peer_create(
    payload: Dict[str, Any] = Body(...),
    x_api_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    require_auth(x_api_token)
    return create_peer(str(payload.get("name", "")))


@app.get("/peers")
def peers(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    return mark_active_peers(parse_wg_dump(get_wg_dump()))


@app.get("/peer/{client_id}")
def peer_details(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    return find_peer_by_client_id(client_id)


@app.post("/peer/{client_id}/disable-check")
def peer_disable_check(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    return can_disable_peer(client_id)

@app.post("/peer/{client_id}/disable")
def peer_disable(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    return disable_peer(client_id)


@app.post("/peer/{client_id}/enable")
def peer_enable(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    return enable_peer(client_id)


@app.post("/peer/{client_id}/speed-limit")
def peer_speed_limit(
    client_id: str,
    payload: Dict[str, Any] = Body(default={}),
    x_api_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    require_auth(x_api_token)
    rate = str(payload.get("rate", "256kbit"))
    result = set_peer_speed_limit(client_id, True, rate)
    return result


@app.post("/peer/{client_id}/speed-normal")
def peer_speed_normal(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    result = set_peer_speed_limit(client_id, False)
    return result


@app.delete("/peer/{client_id}")
def peer_delete(client_id: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    return delete_peer(client_id)


@app.get("/peer/{client_id}/config")
def peer_config(client_id: str, x_api_token: Optional[str] = Header(default=None)):
    require_auth(x_api_token)
    config = build_client_config(client_id)
    return Response(
        content=config,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{client_id}.conf"'},
    )


@app.get("/peer/{client_id}/qr")
def peer_qr(client_id: str, x_api_token: Optional[str] = Header(default=None)):
    require_auth(x_api_token)
    config = build_client_config(client_id)
    img = qrcode.make(config)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@app.get("/traffic/history")
def traffic_history(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)

    peer_data = parse_wg_dump(get_wg_dump())

    def top(period: str):
        if period == "today":
            rx_key = "today_rx_bytes"
            tx_key = "today_tx_bytes"
            total_key = "today_total_bytes"
            rx_human = "today_rx_human"
            tx_human = "today_tx_human"
            total_human = "today_total_human"
        elif period == "week":
            rx_key = "week_rx_bytes"
            tx_key = "week_tx_bytes"
            total_key = "week_total_bytes"
            rx_human = "week_rx_human"
            tx_human = "week_tx_human"
            total_human = "week_total_human"
        elif period == "month":
            rx_key = "month_rx_bytes"
            tx_key = "month_tx_bytes"
            total_key = "month_total_bytes"
            rx_human = "month_rx_human"
            tx_human = "month_tx_human"
            total_human = "month_total_human"
        else:
            rx_key = "saved_total_rx_bytes"
            tx_key = "saved_total_tx_bytes"
            total_key = "saved_total_bytes"
            rx_human = "transfer_rx_human"
            tx_human = "transfer_tx_human"
            total_human = "saved_total_human"

        items = []
        for peer in peer_data["peers"]:
            items.append({
                "name": peer["name"],
                "ip": peer.get("ip"),
                "rx_bytes": peer.get(rx_key, 0),
                "tx_bytes": peer.get(tx_key, 0),
                "bytes": peer.get(total_key, 0),
                "rx_human": peer.get(rx_human, "0.00 B"),
                "tx_human": peer.get(tx_human, "0.00 B"),
                "human": peer.get(total_human, "0.00 B"),
                "online": peer.get("online", False),
                "enabled": peer.get("enabled", False),
            })

        items.sort(key=lambda x: x["bytes"], reverse=True)
        return items

    return {
        "today": top("today"),
        "week": top("week"),
        "month": top("month"),
        "total": top("total"),
    }


@app.get("/activity")
def activity(x_api_token: Optional[str] = Header(default=None), limit: int = 30) -> Dict[str, Any]:
    require_auth(x_api_token)
    limit = max(1, min(limit, 100))
    return {
        "events": read_activity(limit),
        "limit": limit,
    }





def load_top_user_event() -> Dict[str, Any]:
    try:
        with open(TOP_USER_EVENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_top_user_event(data: Dict[str, Any]) -> None:
    tmp = f"{TOP_USER_EVENT_FILE}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, TOP_USER_EVENT_FILE)
    except Exception:
        pass


def get_top_user_now(peers: List[Dict[str, Any]]) -> Dict[str, Any]:
    now_ts = time.time()

    for peer in peers:
        peer["is_active_now"] = False

    best = None
    active_peer_count = 0
    active_peer_keys = []
    active_peer_threshold_mbps = 0.01

    current_keys = set()

    for peer in peers:
        public_key = peer.get("public_key")
        if not public_key:
            continue

        current_keys.add(public_key)

        rx = int(peer.get("transfer_rx_bytes") or 0)
        tx = int(peer.get("transfer_tx_bytes") or 0)

        prev = LIVE_TRAFFIC_PREVIOUS.get(public_key)

        LIVE_TRAFFIC_PREVIOUS[public_key] = {
            "ts": now_ts,
            "rx": rx,
            "tx": tx,
        }

        if not prev:
            continue

        dt = max(1.0, now_ts - float(prev.get("ts") or now_ts))

        rx_delta = rx - int(prev.get("rx") or 0)
        tx_delta = tx - int(prev.get("tx") or 0)

        if rx_delta < 0 or tx_delta < 0:
            continue

        total_delta = rx_delta + tx_delta
        total_mbps = (total_delta * 8) / dt / 1_000_000
        rx_mbps = (rx_delta * 8) / dt / 1_000_000
        tx_mbps = (tx_delta * 8) / dt / 1_000_000

        if peer.get("online") and total_mbps >= active_peer_threshold_mbps:
            active_peer_count += 1
            active_peer_keys.append(public_key)
            peer["is_active_now"] = True

        if not best or total_mbps > best["total_mbps"]:
            best = {
                "name": peer.get("name"),
                "ip": peer.get("ip"),
                "rx_mbps": round(rx_mbps, 2),
                "tx_mbps": round(tx_mbps, 2),
                "total_mbps": round(total_mbps, 2),
                "today_total_bytes": peer.get("today_total_bytes", 0),
                "today_total_human": peer.get("today_total_human", "0.00 B"),
            }

    for key in list(LIVE_TRAFFIC_PREVIOUS.keys()):
        if key not in current_keys:
            LIVE_TRAFFIC_PREVIOUS.pop(key, None)

    if not best:
        return {
            "active": False,
            "threshold_mbps": 1.0,
            "active_peer_count": active_peer_count,
            "active_peer_keys": active_peer_keys,
            "active_peer_threshold_mbps": active_peer_threshold_mbps,
            "last_event": load_top_user_event(),
        }

    best["active"] = best["total_mbps"] >= 1.0
    best["threshold_mbps"] = 1.0
    best["active_peer_count"] = active_peer_count
    best["active_peer_keys"] = active_peer_keys
    best["active_peer_threshold_mbps"] = active_peer_threshold_mbps

    if best["active"]:
        best["ts"] = int(time.time())
        save_top_user_event(best)

    best["last_event"] = load_top_user_event()

    return best



@app.get("/dashboard")
def dashboard(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)

    peer_data = parse_wg_dump(get_wg_dump())
    total_rx = sum(peer["transfer_rx_bytes"] for peer in peer_data["peers"])
    total_tx = sum(peer["transfer_tx_bytes"] for peer in peer_data["peers"])

    vpn_today = sum(peer.get("today_total_bytes", 0) for peer in peer_data["peers"])
    vpn_week = sum(peer.get("week_total_bytes", 0) for peer in peer_data["peers"])
    vpn_month = sum(peer.get("month_total_bytes", 0) for peer in peer_data["peers"])
    vpn_saved_total = sum(peer.get("saved_total_bytes", peer.get("transfer_total_bytes", 0)) for peer in peer_data["peers"])

    top_user_now = get_top_user_now(peer_data["peers"])

    online_peers = [
        {
            "name": peer["name"],
            "ip": peer["ip"],
            "latest_handshake_text": peer["latest_handshake_text"],
            "transfer_total_human": peer["transfer_total_human"],
            "protected": peer["protected"],
        }
        for peer in peer_data["peers"]
        if peer["online"]
    ]

    return {
        "hostname": socket.gethostname(),
        "display_name": read_settings().get("display_name", "Family VPN"),
        "uptime": get_uptime(),
        "cpu": get_cpu_usage(),
        "loadavg": get_loadavg(),
        "memory": get_memory(),
        "disk_root": get_disk_root(),
        "wireguard": {
            "interface": peer_data["interface"],
            "peer_count": peer_data["peer_count"],
            "online_peer_count": peer_data["online_peer_count"],
            "online_threshold_seconds": peer_data["online_threshold_seconds"],
            "total_rx_bytes": total_rx,
            "total_tx_bytes": total_tx,
            "total_traffic_bytes": total_rx + total_tx,
            "total_rx_human": bytes_to_human(total_rx),
            "total_tx_human": bytes_to_human(total_tx),
            "total_traffic_human": bytes_to_human(total_rx + total_tx),

            "vpn_today_bytes": vpn_today,
            "vpn_week_bytes": vpn_week,
            "vpn_month_bytes": vpn_month,
            "vpn_saved_total_bytes": vpn_saved_total,

            "vpn_today_human": bytes_to_human(vpn_today),
            "vpn_week_human": bytes_to_human(vpn_week),
            "vpn_month_human": bytes_to_human(vpn_month),
            "vpn_saved_total_human": bytes_to_human(vpn_saved_total),

            "top_user_now": top_user_now,
            "online_peers": online_peers,
        },
    }






def backup_human_bytes(n: int) -> str:
    try:
        n = float(n)
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if n < 1024 or unit == units[-1]:
                return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
            n /= 1024
    except Exception:
        return str(n)


def backup_file_info(name: str) -> Dict[str, Any]:
    path = BACKUP_DIR / name
    if not path.exists():
        return {"exists": False, "name": name}
    st = path.stat()
    return {
        "exists": True,
        "name": name,
        "size": st.st_size,
        "size_human": backup_human_bytes(st.st_size),
        "mtime": int(st.st_mtime),
        "path": str(path),
    }


@app.get("/backup/status")
def backup_status(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    return {
        "latest": backup_file_info("latest.wgadmin"),
        "previous": backup_file_info("previous.wgadmin"),
    }


@app.post("/backup/create")
def backup_create(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    try:
        subprocess.run(["/usr/local/bin/wg-admin-backup"], check=True, timeout=60)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Backup script failed with exit code {e.returncode}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Backup script timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Backup script not found: /usr/local/bin/wg-admin-backup")
    return {
        "created": True,
        "latest": backup_file_info("latest.wgadmin"),
        "previous": backup_file_info("previous.wgadmin"),
    }


@app.get("/backup/download/{kind}")
def backup_download(
    kind: str,
    x_api_token: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
):
    require_auth(x_api_token or token)

    if kind not in ("latest", "previous"):
        raise HTTPException(status_code=400, detail="Invalid backup kind")

    path = BACKUP_DIR / f"{kind}.wgadmin"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")

    ts = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d_%H-%M")
    filename = f"WG-Admin-{ts}.wgadmin"

    return Response(
        content=path.read_bytes(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


@app.post("/backup/restore/{kind}")
def backup_restore(kind: str, x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)

    if kind not in ("latest", "previous"):
        raise HTTPException(status_code=400, detail="Invalid backup kind")

    path = BACKUP_DIR / f"{kind}.wgadmin"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")

    log_activity(
        "maintenance",
        "system",
        "",
        "",
        {"action": "restore-backup-started", "kind": kind, "file": str(path)},
    )

    subprocess.Popen(
        ["nohup", "/usr/local/bin/wg-admin-restore", kind],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    return {
        "ok": True,
        "accepted": True,
        "restore_started": True,
        "kind": kind,
        "file": str(path),
    }


@app.post("/maintenance/restart-wg-easy")
def maintenance_restart_wg_easy(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    out = run_cmd(["docker", "restart", WG_EASY_CONTAINER], timeout=30)
    log_activity("maintenance", "system", "", "", {"action": "restart-wg-easy"})
    return {"ok": True, "action": "restart-wg-easy", "output": out}


@app.post("/maintenance/restart-wg-admin")
def maintenance_restart_wg_admin(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    run_cmd(["nohup", "bash", "-c", "sleep 2 && systemctl restart wg-admin-api >/dev/null 2>&1 &"], timeout=5)
    return {"ok": True, "action": "restart-wg-admin", "message": "Restart scheduled"}


@app.post("/maintenance/reboot-server")
def maintenance_reboot_server(x_api_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_auth(x_api_token)
    run_cmd(["nohup", "bash", "-c", "sleep 5 && reboot >/dev/null 2>&1 &"], timeout=5)
    return {"ok": True, "action": "reboot-server", "message": "Reboot scheduled"}


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "service": "wg-admin-api",
        "version": "0.4.0",
        "endpoints": [
            "/health",
            "/status",
            "/settings",
            "/dashboard",
            "/activity",
            "/traffic/history",
            "/peers",
            "/peer/create",
            "/peer/{client_id}",
            "/peer/{client_id} [DELETE]",
            "/peer/{client_id}/disable-check",
            "/peer/{client_id}/config",
            "/peer/{client_id}/qr",
        ],
    }
