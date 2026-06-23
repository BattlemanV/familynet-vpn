"""Initial WireGuard configuration generator.
Called from entrypoint.sh at first container startup."""

import json
import os
import subprocess
import sys
import tempfile

CLIENTS_FILE = os.environ.get("CLIENTS_FILE", "/data/clients.json")
WG_CONF = os.environ.get("WG_CONF", "/data/wg0.conf")

SERVER_PORT = os.environ.get("WG_PORT", "51820")
SERVER_ADDRESS = os.environ.get("WG_SERVER_ADDRESS", "10.8.0.1")
WG_VARIANT = os.environ.get("WG_VARIANT", "wg")


def resolve_external_iface() -> str:
    try:
        out = subprocess.check_output(
            ["ip", "route", "get", "8.8.8.8"], stderr=subprocess.DEVNULL, timeout=5
        ).decode()
        for word in out.split():
            if word == "dev":
                idx = out.split().index(word)
                return out.split()[idx + 1]
    except Exception:
        pass
    return "eth0"


def main():
    if os.path.exists(WG_CONF):
        return  # already configured

    print("Generating wg0.conf...")
    external_iface = resolve_external_iface()

    server_private = None
    if os.path.exists(CLIENTS_FILE):
        try:
            with open(CLIENTS_FILE) as f:
                d = json.load(f)
            server_private = d.get("server", {}).get("privateKey")
        except Exception:
            pass

    if not server_private:
        server_private = subprocess.check_output(["wg", "genkey"]).decode().strip()
        server_public = subprocess.check_output(
            ["wg", "pubkey"], input=server_private.encode()
        ).decode().strip()
        try:
            with open(CLIENTS_FILE) as f:
                d = json.load(f)
        except Exception:
            d = {"server": {}, "clients": {}}
        d.setdefault("server", {})["privateKey"] = server_private
        d["server"]["publicKey"] = server_public
        d["server"]["address"] = SERVER_ADDRESS
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CLIENTS_FILE) or '.')
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, CLIENTS_FILE)
    else:
        server_public = subprocess.check_output(
            ["wg", "pubkey"], input=server_private.encode()
        ).decode().strip()

    with open(WG_CONF, "w") as f:
        f.write("[Interface]\n")
        f.write(f"PrivateKey = {server_private}\n")
        f.write(f"Address = {SERVER_ADDRESS}/24\n")
        f.write(f"ListenPort = {SERVER_PORT}\n")
        if WG_VARIANT == "awg":
            f.write("Jc = 4\n")
            f.write("Jmin = 10\n")
            f.write("Jmax = 50\n")
            f.write("S1 = 97\n")
            f.write("S2 = 99\n")
        f.write(
            f"PostUp = iptables -A FORWARD -i %i -j ACCEPT; "
            f"iptables -t nat -A POSTROUTING -o {external_iface} -j MASQUERADE\n"
        )
        f.write(
            f"PostDown = iptables -D FORWARD -i %i -j ACCEPT; "
            f"iptables -t nat -D POSTROUTING -o {external_iface} -j MASQUERADE\n"
        )
        if os.path.exists(CLIENTS_FILE):
            try:
                with open(CLIENTS_FILE) as cf:
                    d = json.load(cf)
                for cid, c in d.get("clients", {}).items():
                    pub = c.get("publicKey", "")
                    psk = c.get("preSharedKey", "")
                    addr = c.get("address", "")
                    if pub and addr:
                        f.write(f"\n[Peer]\n")
                        f.write(f"PublicKey = {pub}\n")
                        if psk:
                            f.write(f"PresharedKey = {psk}\n")
                        f.write(f"AllowedIPs = {addr}/32\n")
            except Exception:
                pass

    os.chmod(WG_CONF, 0o600)
    print(f"{WG_CONF} generated")


if __name__ == "__main__":
    main()
