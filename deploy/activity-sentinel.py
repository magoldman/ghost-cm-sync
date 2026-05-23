#!/usr/bin/env python3
"""
Activity sentinel: pings Healthchecks.io when ghost-cm-sync receives new events.

Run on a cron schedule (suggested every 15 min). Healthchecks.io fires an alert
when no ping arrives for its configured grace period, catching the "service
healthy but no inbound traffic" failure mode that systemd OnFailure cannot
detect (e.g. the Ghost SSRF block of 2026-04-25).

State is persisted across runs so we detect counter resets on service restart.
"""

import json
import sys
import urllib.request
from pathlib import Path

METRICS_URL = "http://127.0.0.1:3000/metrics"
STATE_FILE = Path("/var/lib/ghost-cm-sync/activity-sentinel.state")
ENV_FILE = "/etc/ghost-cm-sync/notify.env"


def load_env_value(key: str) -> str:
    try:
        with open(ENV_FILE) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f"{key}="):
                    return stripped.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"last_count": 0, "last_uptime": 0.0}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def main() -> int:
    hc_url = load_env_value("HC_ACTIVITY_URL")
    if not hc_url:
        print("HC_ACTIVITY_URL not set in /etc/ghost-cm-sync/notify.env", file=sys.stderr)
        return 1

    try:
        with urllib.request.urlopen(METRICS_URL, timeout=10) as resp:
            metrics = json.loads(resp.read())
    except Exception as exc:
        print(f"fetch /metrics failed: {exc}", file=sys.stderr)
        return 1

    current_count = metrics.get("events_received", 0)
    current_uptime = metrics.get("uptime_seconds", 0.0)

    state = load_state()
    last_count = state.get("last_count", 0)
    last_uptime = state.get("last_uptime", 0.0)

    # Counter resets on service restart; rebaseline so we don't miss new events.
    if current_uptime < last_uptime:
        last_count = 0

    new_events = current_count - last_count

    if new_events > 0:
        try:
            urllib.request.urlopen(hc_url, timeout=10).read()
            print(f"ping ok (+{new_events} events, total {current_count})")
        except Exception as exc:
            print(f"ping failed: {exc}", file=sys.stderr)
            return 1

    save_state({"last_count": current_count, "last_uptime": current_uptime})
    return 0


if __name__ == "__main__":
    sys.exit(main())
