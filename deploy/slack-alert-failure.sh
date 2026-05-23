#!/usr/bin/env bash
# Slack alert for failed systemd units. Invoked by alert-failure@.service.
# Usage: slack-alert-failure.sh <unit-name>

set -euo pipefail

UNIT="${1:-unknown.service}"
HOST="$(hostname)"

# shellcheck disable=SC1091
source /etc/ghost-cm-sync/notify.env

if [[ -z "${SLACK_WEBHOOK_URL:-}" ]]; then
    echo "SLACK_WEBHOOK_URL not configured in /etc/ghost-cm-sync/notify.env" >&2
    exit 1
fi

LOGS=$(journalctl -u "$UNIT" -n 20 --no-pager 2>/dev/null | tail -c 2000 || echo "(no logs)")

# Build JSON via python3 to avoid bash quoting issues with arbitrary log content
PAYLOAD=$(UNIT="$UNIT" HOST="$HOST" LOGS="$LOGS" python3 - <<'PYEOF'
import json, os
unit = os.environ["UNIT"]
host = os.environ["HOST"]
logs = os.environ["LOGS"]
text = "🚨 *" + unit + "* failed on `" + host + "`\n\n```\n" + logs + "\n```"
print(json.dumps({"text": text}))
PYEOF
)

curl -sS -X POST -H "Content-Type: application/json" \
    --max-time 10 \
    --data "$PAYLOAD" \
    "$SLACK_WEBHOOK_URL" \
    >/dev/null || {
    echo "Slack POST failed" >&2
    exit 0  # never propagate — alerter failure must not trigger more alerts
}
