# ThinkCentre Proxmox LXC Deployment

Run `local-agent-router` as a small dedicated LXC on the ThinkCentre Proxmox host.

The router is a lightweight FastAPI policy service. It should be durable, always on, and independent from whichever inference backend is currently online. The ThinkCentre is the control-plane host. The Sparks, RTX 5090 workstation, Mac Studio, and Strix Halo boxes are capability nodes.

## Recommended Shape

- Proxmox host: `pve-thinkcentre`
- Guest type: unprivileged LXC
- OS: Debian 12 or Ubuntu 24.04
- Resources: 1 vCPU, 512 MB to 1 GB RAM, 4 to 8 GB disk
- Network: static LAN IP or DHCP reservation
- Service port: `8088/tcp`
- Run user: `router`
- App path: `/opt/local-agent-router`
- Config path: `/etc/local-agent-router/config.yaml`

## Why LXC on the ThinkCentre

Use an LXC instead of running directly on the Proxmox host so Python dependencies, logs, updates, and restarts stay isolated from the hypervisor.

Do not run this inside Home Assistant OS. HAOS is appliance-like, and the router is shared homelab infrastructure. Home Assistant should consume the router, not own it.

Do not run this permanently on the Sparks. The Sparks are inference workers. The router should remain available when Spark inference is offline, restarting, saturated, or being benchmarked.

## Network

Expose the router on the LXC LAN IP:

```text
http://<router-lxc-ip>:8088
```

Open only what is needed:

- inbound to LXC: `8088/tcp` from trusted LAN clients
- outbound from LXC:
  - Spark/vLLM endpoint, for example `http://192.168.0.4:8000/v1`
  - Spark health endpoint, for example `http://192.168.0.4:8000/health`
  - RTX 5090 workstation endpoint when it exists
  - GitHub for updates if pulling from the repo directly

Client base URL:

```text
OPENAI_BASE_URL=http://<router-lxc-ip>:8088/v1
```

## Install

Inside the LXC:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
sudo useradd --system --home /opt/local-agent-router --shell /usr/sbin/nologin router
sudo mkdir -p /opt/local-agent-router /etc/local-agent-router
sudo chown router:router /opt/local-agent-router
```

Clone and install:

```bash
sudo -u router git clone https://github.com/carryologist/local-agent-router.git /opt/local-agent-router
cd /opt/local-agent-router
sudo -u router python3 -m venv .venv
sudo -u router .venv/bin/pip install --upgrade pip
sudo -u router .venv/bin/pip install -e .
```

Copy the example config and customize it:

```bash
sudo cp /opt/local-agent-router/examples/config.yaml /etc/local-agent-router/config.yaml
sudo chown root:router /etc/local-agent-router/config.yaml
sudo chmod 0640 /etc/local-agent-router/config.yaml
```

Keep production config outside the git checkout so updates do not overwrite local routing policy.

## systemd Service

Create `/etc/systemd/system/local-agent-router.service`:

```ini
[Unit]
Description=Local Agent Router
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=router
Group=router
WorkingDirectory=/opt/local-agent-router
Environment=LOCAL_AGENT_ROUTER_CONFIG=/etc/local-agent-router/config.yaml
Environment=LOCAL_AGENT_ROUTER_HOST=0.0.0.0
Environment=LOCAL_AGENT_ROUTER_PORT=8088
Environment=LOCAL_AGENT_ROUTER_HEALTH_TTL_SECONDS=5
ExecStart=/opt/local-agent-router/.venv/bin/local-agent-router
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/opt/local-agent-router

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now local-agent-router
```

Check status:

```bash
systemctl status local-agent-router
journalctl -u local-agent-router -f
curl http://127.0.0.1:8088/health
curl http://127.0.0.1:8088/routes
curl http://127.0.0.1:8088/v1/models
```

## Update Flow

```bash
cd /opt/local-agent-router
sudo -u router git fetch --all --prune
sudo -u router git pull --ff-only
sudo -u router .venv/bin/pip install -e .
sudo systemctl restart local-agent-router
sudo systemctl status local-agent-router
curl http://127.0.0.1:8088/health
```

Optional safer flow:

1. Snapshot the LXC in Proxmox.
2. Pull and reinstall.
3. Restart the service.
4. Verify `/health`, `/routes`, and `/v1/models`.
5. Roll back the snapshot if the router does not come up cleanly.

## Operations

- Give the LXC a stable IP or DHCP reservation.
- Monitor `http://<router-lxc-ip>:8088/health` from Uptime Kuma.
- Use `/routes` when debugging degraded or fallback behavior.
- Use `/v1/models` to confirm what clients see.
- Keep backend health timeouts short, around 2 seconds, so clients do not hang when a backend is down.
- Keep at least one generic local fallback route available when possible.
