#!/bin/bash
# ── Cloudflare Named Tunnel Setup for Jimmy ──────────────────────────────────
#
# This creates a persistent named tunnel with a stable URL (no more random
# trycloudflare.com URLs). Free forever with Cloudflare.
#
# Prerequisites:
#   brew install cloudflared
#   cloudflared login          # opens browser to auth with your Cloudflare account
#
# You need a domain managed by Cloudflare (even a free one works).
# If you don't have one, use the quick-tunnel mode instead (./start.sh).
#
# Usage:
#   ./scripts/setup-tunnel.sh              # interactive setup
#   ./scripts/setup-tunnel.sh jimmy.yourdomain.com   # with hostname
#
set -euo pipefail

TUNNEL_NAME="jimmy"
HOSTNAME="${1:-}"
LOCAL_PORT="${JIMMY_PORT:-7700}"

# ── Preflight checks ────────────────────────────────────────────────────────
if ! command -v cloudflared &>/dev/null; then
    echo "ERROR: cloudflared not found."
    echo "Install: brew install cloudflared"
    exit 1
fi

if [ ! -f "$HOME/.cloudflared/cert.pem" ]; then
    echo "You need to authenticate first."
    echo "Running: cloudflared login"
    cloudflared login
fi

if [ -z "$HOSTNAME" ]; then
    echo ""
    echo "Enter the hostname for Jimmy (e.g., jimmy.yourdomain.com):"
    read -r HOSTNAME
    if [ -z "$HOSTNAME" ]; then
        echo "ERROR: Hostname is required for named tunnels."
        echo "If you want a quick random URL, use ./start.sh instead."
        exit 1
    fi
fi

# ── Create tunnel ────────────────────────────────────────────────────────────
echo ""
echo "Creating Cloudflare tunnel: $TUNNEL_NAME"

# Check if tunnel already exists
if cloudflared tunnel list | grep -q "$TUNNEL_NAME"; then
    echo "Tunnel '$TUNNEL_NAME' already exists. Reusing it."
    TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}')
else
    cloudflared tunnel create "$TUNNEL_NAME"
    TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}')
fi

echo "Tunnel ID: $TUNNEL_ID"

# ── Write config ─────────────────────────────────────────────────────────────
CONFIG_DIR="$HOME/.cloudflared"
CONFIG_FILE="$CONFIG_DIR/config.yml"

# Back up existing config if present
if [ -f "$CONFIG_FILE" ]; then
    cp "$CONFIG_FILE" "${CONFIG_FILE}.bak.$(date +%s)"
    echo "Backed up existing config to ${CONFIG_FILE}.bak.*"
fi

cat > "$CONFIG_FILE" <<EOF
tunnel: $TUNNEL_ID
credentials-file: $CONFIG_DIR/${TUNNEL_ID}.json

ingress:
  - hostname: $HOSTNAME
    service: http://localhost:$LOCAL_PORT
    originRequest:
      noTLSVerify: true
  - service: http_status:404
EOF

echo "Wrote config to $CONFIG_FILE"

# ── Create DNS record ───────────────────────────────────────────────────────
echo ""
echo "Creating DNS CNAME record: $HOSTNAME -> $TUNNEL_ID.cfargotunnel.com"
cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME" 2>/dev/null || true

# ── Print instructions ──────────────────────────────────────────────────────
echo ""
echo "======================================================================"
echo "  Cloudflare Tunnel configured!"
echo ""
echo "  Hostname: https://$HOSTNAME"
echo "  Tunnel:   $TUNNEL_NAME ($TUNNEL_ID)"
echo ""
echo "  To start Jimmy + tunnel:"
echo "    cd $(dirname "$0")/.."
echo "    source .venv/bin/activate"
echo "    uvicorn jimmy.api.server:app --port $LOCAL_PORT --host 0.0.0.0 &"
echo "    cloudflared tunnel run $TUNNEL_NAME"
echo ""
echo "  Or run as a system service (auto-starts on boot):"
echo "    sudo cloudflared service install"
echo ""
echo "  To use with Docker Compose:"
echo "    1. Get your tunnel token:"
echo "       cloudflared tunnel token $TUNNEL_NAME"
echo "    2. Add to .env.local:"
echo "       CLOUDFLARE_TUNNEL_TOKEN=<token>"
echo "    3. Run:"
echo "       docker compose --profile tunnel up -d"
echo "======================================================================"
