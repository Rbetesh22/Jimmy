#!/bin/bash
# Auto-restart localtunnel for Neuron
# Run this whenever you start the server: ./start_tunnel.sh
while true; do
    echo "$(date): Starting tunnel https://neuron-ralph.loca.lt → localhost:7700"
    npx localtunnel --port 7700 --subdomain neuron-ralph
    echo "$(date): Tunnel died, restarting in 3s..."
    sleep 3
done
