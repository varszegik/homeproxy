# homeproxy

A tiny web UI for giving your self-hosted services friendly names on your home network.

Instead of remembering `http://192.168.1.10:2283`, you open `http://immich.home.arpa`.
Add, edit, or remove names in a browser. Changes apply right away, with no config files to edit and no restarts.

```
  browser ──► immich.home.arpa ──► Caddy (:80) ──► 192.168.1.10:2283
                                       ▲
                    homeproxy UI ──────┘  (pushes config via Caddy's admin API)
```

## Features

- **One-click routes**: name, target IP/host, and port, and that's it.
- **Live status**: a green or red dot shows whether each backend is reachable.
- **Default site**: the bare IP or an unknown name goes to a route you pick (for example, your dashboard).
- **HTTPS backends**: you can proxy services that only speak HTTPS with a self-signed cert (Proxmox, UniFi, Frigate, ...).
- **Keeps working if the UI is down**: Caddy runs with `--resume`, so it keeps the last config it was given.
- **Tiny**: one Python file using only the standard library, plus the official Caddy image.

## Requirements

- A Linux machine on your network (a server, NAS, Raspberry Pi, ...) with **Docker** and **Docker Compose**
- **Port 80** free on that machine. If another web server already uses it, see [Configuration](#configuration).
- The ability to add a **DNS record** on your network. Your router, Pi-hole, AdGuard Home, etc. usually can.

## Setup

### 1. Get the code

```bash
git clone https://github.com/varszegik/homeproxy.git
cd homeproxy
```

### 2. (Optional) Adjust settings

```bash
cp .env.example .env
nano .env        # set DEFAULT_TARGET to the IP of your main server, your timezone, etc.
```

The defaults work fine if you skip this step.

### 3. Start it

```bash
docker compose up -d --build
```

The UI is now at `http://<machine-ip>:8099`.

### 4. Point DNS at the machine

For names like `immich.home.arpa` to work, every `*.home.arpa` name needs to resolve to the machine running homeproxy.
Add a **wildcard DNS record** in whatever handles DNS on your network.

| DNS server | How |
|---|---|
| **Pi-hole** (v6) | Settings → All settings → Miscellaneous → `misc.dnsmasq_lines`, add: `address=/home.arpa/192.168.1.5` |
| **AdGuard Home** | Filters → DNS rewrites → Add: domain `*.home.arpa`, answer `192.168.1.5` |
| **OpenWrt / dnsmasq** | Add `address=/home.arpa/192.168.1.5` to the dnsmasq config |
| **Router with no wildcard support** | Add one entry per service (`immich.home.arpa → 192.168.1.5`, ...) |
| **Just testing on one PC** | Add lines to `/etc/hosts` (or `C:\Windows\System32\drivers\etc\hosts`) |

Replace `192.168.1.5` with the IP of the machine running homeproxy.

> **Why `home.arpa`?** It's the domain officially reserved for home networks ([RFC 8375](https://www.rfc-editor.org/rfc/rfc8375)), so it will never clash with a real website. You can use a different one; see below.

### 5. Add your services

Open `http://proxy.home.arpa` (or `http://<machine-ip>:8099`) and add routes:

| Field | Example | Meaning |
|---|---|---|
| Name | `immich` | Becomes `immich.home.arpa` |
| Target | `192.168.1.10` | IP or hostname where the service runs (`127.0.0.1` = the same machine) |
| Port | `2283` | The port the service listens on |
| Backend uses HTTPS | off | Turn on if the service itself only serves `https://` (self-signed certs are accepted) |
| Default site | off | Send the bare IP and unknown names here |

That's it. Visit `http://immich.home.arpa`.

## Configuration

All settings live in `.env` (copy from `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `DOMAIN` | `home.arpa` | Base domain. Routes become `<name>.DOMAIN`, and the UI is at `proxy.DOMAIN`. |
| `PROXY_PORT` | `80` | Port Caddy listens on for proxied traffic |
| `UI_PORT` | `8099` | Port of the management UI |
| `DEFAULT_TARGET` | *(empty)* | Pre-fills the Target field when adding a route |
| `TZ` | `UTC` | Timezone for logs |

After changing `.env`, run `docker compose up -d` again.

**Port 80 already in use?** Set `PROXY_PORT=8080` (for example). URLs then need the port: `http://immich.home.arpa:8080`.

## Where things are stored

- `data/routes.json` holds your routes. Back this file up. `routes.example.json` shows the format.
- The Caddy config is generated from `routes.json` automatically, so you never need to edit it.

## Updating

```bash
git pull
docker compose up -d --build
```

## Security notes

- The UI has **no login**. Anyone on your network can change routes. Don't expose port 8099 (or `proxy.DOMAIN`) to the internet.
- Traffic is plain HTTP inside your network. This tool is meant for LAN convenience, not for publishing services publicly.
- Caddy's admin API listens only on `127.0.0.1:2019`.
- Both containers use `network_mode: host`, so they can reach services on the host and anywhere on the LAN.

## Troubleshooting

| Problem | Fix |
|---|---|
| `xyz.home.arpa` doesn't load at all | DNS isn't set up. Run `nslookup xyz.home.arpa`; it should return your machine's IP. |
| Page loads but shows the wrong service | Check the route's port, and whether it needs "Backend uses HTTPS". |
| Red dot next to a route | homeproxy can't reach that IP and port. Is the service running? |
| UI header shows Caddy as down | `docker compose logs caddy` |
| Container fails to start: "address already in use" | Something else is using port 80 or 8099. Change `PROXY_PORT` / `UI_PORT`. |
| Browser goes to Google search instead | Type the full `http://` the first time. |
