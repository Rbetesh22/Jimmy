#!/bin/bash
# Start Neuron server + Cloudflare tunnel
# Usage: ./start.sh
set -e
cd "$(dirname "$0")"

echo "Starting Neuron server..."
source .venv/bin/activate
python3 -m uvicorn neuron.api.server:app --port 7700 --host 0.0.0.0 --log-level warning &
SERVER_PID=$!

echo "Waiting for server..."
for i in $(seq 1 20); do
  sleep 1
  curl -s http://localhost:7700/health > /dev/null 2>&1 && break
done

echo "Starting Cloudflare tunnel..."
cloudflared tunnel --url http://localhost:7700 --no-autoupdate > /tmp/cf.log 2>&1 &
CF_PID=$!

sleep 5
CF_URL=$(grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" /tmp/cf.log | head -1)
echo ""
echo "======================================"
echo "  Neuron is running!"
echo "  URL: $CF_URL"
echo "  Set this in iOS Settings > Server URL"
echo "======================================"

# Keep running until Ctrl+C
trap "kill $SERVER_PID $CF_PID 2>/dev/null; exit" INT TERM
wait $SERVER_PID
