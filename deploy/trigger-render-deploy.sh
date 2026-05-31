#!/usr/bin/env bash
# Trigger Render redeploy for seo-bot-ping.
# Usage:
#   RENDER_API_KEY=rnd_... bash deploy/trigger-render-deploy.sh
#   RENDER_DEPLOY_HOOK=https://api.render.com/deploy/srv-... bash deploy/trigger-render-deploy.sh

set -euo pipefail

SERVICE_NAME="${RENDER_SERVICE_NAME:-seo-bot-ping}"
HOOK="${RENDER_DEPLOY_HOOK:-${RENDER_DEPLOY_HOOK_URL:-}}"

if [[ -n "$HOOK" ]]; then
  echo "==> Deploy hook"
  curl -sf -X POST "$HOOK"
  echo
  echo "Hook triggered"
else
  KEY="${RENDER_API_KEY:-}"
  if [[ -z "$KEY" ]]; then
    echo "Set RENDER_API_KEY or RENDER_DEPLOY_HOOK" >&2
    echo "  Render Dashboard → Account Settings → API Keys" >&2
    echo "  or Service → Settings → Deploy Hook" >&2
    exit 1
  fi
  echo "==> Find service: $SERVICE_NAME"
  SID="$(
    curl -sf -H "Authorization: Bearer $KEY" -H "Accept: application/json" \
      "https://api.render.com/v1/services?limit=100" \
      | SERVICE_NAME="$SERVICE_NAME" python3 -c "
import json, os, sys
name = os.environ['SERVICE_NAME'].lower()
data = json.load(sys.stdin)
for item in data:
    s = item.get('service') or item
    if (s.get('name') or '').lower() == name:
        print(s.get('id', ''))
        sys.exit(0)
for item in data:
    s = item.get('service') or item
    url = (s.get('serviceDetails') or {}).get('url') or ''
    if 'seo-bot-ping.onrender.com' in url:
        print(s.get('id', ''))
        sys.exit(0)
"
  )"
  if [[ -z "$SID" ]]; then
    echo "Service not found. Connect repo https://github.com/Boundaryploice/seo-bot-ping" >&2
    exit 1
  fi
  echo "==> Deploy $SID (clear cache)"
  curl -sf -X POST \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -d '{"clearCache":"clear"}' \
    "https://api.render.com/v1/services/${SID}/deploys"
  echo
fi

echo "==> Wait for health..."
for i in $(seq 1 30); do
  body="$(curl -sf -m 20 "https://seo-bot-ping.onrender.com/health" 2>/dev/null || true)"
  if [[ "$body" == *"2026-05-31-inject"* ]]; then
    echo "OK: $body"
    exit 0
  fi
  echo "  [$i/30] $body"
  sleep 10
done
echo "Deploy triggered but health not updated yet — check Render dashboard" >&2
exit 1
