#!/bin/bash
set -euo pipefail

# Jimmy → Fly.io deploy script
# Prerequisites: brew install flyctl && fly auth login

APP="jimmy-rb"

echo "▶ Deploying $APP to Fly.io..."
fly deploy --app "$APP" --remote-only

echo ""
echo "✅ Live at: https://$APP.fly.dev"
echo ""
echo "Health check:"
curl -s "https://$APP.fly.dev/health"
