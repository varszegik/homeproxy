"""homeproxy: tiny UI that manages Caddy reverse-proxy routes for *.DOMAIN.

Source of truth is DATA_DIR/routes.json. Every change regenerates the full
Caddy JSON config and POSTs it to Caddy's admin API (/load). Caddy runs with
--resume, so the last pushed config survives restarts even if this app is down.
"""

import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOMAIN = os.environ.get("DOMAIN", "home.arpa")
UI_PORT = int(os.environ.get("UI_PORT", "8099"))
PROXY_PORT = int(os.environ.get("PROXY_PORT", "80"))
CADDY_ADMIN = os.environ.get("CADDY_ADMIN", "http://127.0.0.1:2019")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
DEFAULT_TARGET = os.environ.get("DEFAULT_TARGET", "")
ROUTES_FILE = os.path.join(DATA_DIR, "routes.json")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

RESERVED = {"proxy"}  # proxy.DOMAIN always points at this UI
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
TARGET_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")

lock = threading.Lock()


# ---------- storage ----------

def load_routes():
    try:
        with open(ROUTES_FILE) as f:
            return json.load(f).get("routes", [])
    except FileNotFoundError:
        return []


def save_routes(routes):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = ROUTES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"routes": routes}, f, indent=2)
    os.replace(tmp, ROUTES_FILE)


def validate(r):
    name = str(r.get("name", "")).strip().lower()
    target = str(r.get("target", "")).strip()
    try:
        port = int(r.get("port"))
    except (TypeError, ValueError):
        raise ValueError("Port must be a number")
    if not NAME_RE.match(name):
        raise ValueError("Name may contain only a-z, 0-9 and '-' (not at start/end)")
    if name in RESERVED:
        raise ValueError(f"'{name}' is reserved for this UI")
    if not TARGET_RE.match(target):
        raise ValueError("Target must be an IP address or hostname")
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    return {
        "name": name,
        "target": target,
        "port": port,
        "https": bool(r.get("https")),
        "default": bool(r.get("default")),
    }


# ---------- caddy ----------

def upstream(target, port, https):
    host = f"[{target}]" if ":" in target and not target.startswith("[") else target
    h = {"handler": "reverse_proxy", "upstreams": [{"dial": f"{host}:{port}"}]}
    if https:
        # Backends with self-signed certs (Frigate, Proxmox, UniFi, ...)
        h["transport"] = {"protocol": "http", "tls": {"insecure_skip_verify": True}}
    return h


def build_caddy_config(routes):
    ui = upstream("127.0.0.1", UI_PORT, False)
    caddy_routes = [{
        "match": [{"host": [f"proxy.{DOMAIN}"]}],
        "handle": [ui],
        "terminal": True,
    }]
    for r in routes:
        caddy_routes.append({
            "match": [{"host": [f"{r['name']}.{DOMAIN}"]}],
            "handle": [upstream(r["target"], r["port"], r["https"])],
            "terminal": True,
        })
    # Anything else (bare IP, unknown names) -> default route, or this UI.
    default = next((r for r in routes if r.get("default")), None)
    caddy_routes.append({
        "handle": [upstream(default["target"], default["port"], default["https"]) if default else ui],
    })
    return {
        "admin": {"listen": CADDY_ADMIN.split("//", 1)[-1]},
        "apps": {"http": {"servers": {"homeproxy": {
            "listen": [f":{PROXY_PORT}"],
            "routes": caddy_routes,
            "automatic_https": {"disable": True},
        }}}},
    }


def push_to_caddy(routes):
    body = json.dumps(build_caddy_config(routes)).encode()
    req = urllib.request.Request(
        f"{CADDY_ADMIN}/load", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Caddy rejected config: {e.read().decode(errors='replace')[:300]}")
    except OSError as e:
        raise RuntimeError(f"Caddy unreachable: {e}")


def caddy_alive():
    try:
        urllib.request.urlopen(f"{CADDY_ADMIN}/config/", timeout=1).read()
        return True
    except Exception:
        return False


def initial_push():
    """Caddy may start after us; keep trying until it accepts the config."""
    for _ in range(60):
        try:
            with lock:
                push_to_caddy(load_routes())
            print("Pushed config to Caddy", flush=True)
            return
        except RuntimeError as e:
            print(f"Waiting for Caddy: {e}", flush=True)
            time.sleep(2)


# ---------- health ----------

pool = ThreadPoolExecutor(max_workers=16)


def reachable(r):
    try:
        with socket.create_connection((r["target"], r["port"]), timeout=0.8):
            return True
    except OSError:
        return False


# ---------- http ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "homeproxy"

    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            routes = load_routes()
            status = list(pool.map(reachable, routes))
            for r, ok in zip(routes, status):
                r["up"] = ok
            self.send_json(200, {
                "domain": DOMAIN,
                "default_target": DEFAULT_TARGET,
                "caddy": caddy_alive(),
                "routes": sorted(routes, key=lambda r: r["name"]),
            })
        else:
            self.send_json(404, {"error": "not found"})

    def mutate(self, fn):
        """Apply fn to the route list, push to Caddy, and only then persist."""
        with lock:
            old = load_routes()
            try:
                new = fn([dict(r) for r in old])
                push_to_caddy(new)
            except (ValueError, RuntimeError) as e:
                return self.send_json(400, {"error": str(e)})
            save_routes(new)
        self.send_json(200, {"ok": True})

    def do_POST(self):
        # POST /api/routes            -> create
        # POST /api/routes/<original> -> update (rename allowed)
        m = re.fullmatch(r"/api/routes(?:/([a-z0-9-]+))?", self.path)
        if not m:
            return self.send_json(404, {"error": "not found"})
        original = m.group(1)
        try:
            route = validate(self.read_json())
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json(400, {"error": str(e)})

        def apply(routes):
            if original and not any(r["name"] == original for r in routes):
                raise ValueError(f"No route named '{original}'")
            routes = [r for r in routes if r["name"] != original]
            if any(r["name"] == route["name"] for r in routes):
                raise ValueError(f"'{route['name']}' already exists")
            if route["default"]:
                for r in routes:
                    r["default"] = False
            routes.append(route)
            return routes

        self.mutate(apply)

    def do_DELETE(self):
        m = re.fullmatch(r"/api/routes/([a-z0-9-]+)", self.path)
        if not m:
            return self.send_json(404, {"error": "not found"})
        name = m.group(1)
        self.mutate(lambda routes: [r for r in routes if r["name"] != name])


if __name__ == "__main__":
    threading.Thread(target=initial_push, daemon=True).start()
    print(f"homeproxy UI on :{UI_PORT}, proxying *.{DOMAIN} on :{PROXY_PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", UI_PORT), Handler).serve_forever()
