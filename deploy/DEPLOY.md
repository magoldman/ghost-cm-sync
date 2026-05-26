# Deployment Guide: DigitalOcean Ubuntu Droplet

This guide covers deploying ghost-cm-sync to a DigitalOcean droplet running Ubuntu 22.04 or 24.04.

## Prerequisites

- DigitalOcean droplet (1GB RAM minimum recommended)
- Ubuntu 22.04 or 24.04 LTS
- Domain pointed to your droplet (for SSL)
- SSH access to your droplet

## 1. Initial Server Setup

SSH into your droplet and run:

### Ubuntu 24.04 LTS (Recommended)

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages (Python 3.12 is included)
sudo apt install -y python3-venv python3-dev \
    redis-server nginx certbot python3-certbot-nginx git
```

### Ubuntu 22.04 LTS

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Add deadsnakes PPA for Python 3.11
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update

# Install required packages
sudo apt install -y python3.11 python3.11-venv python3.11-dev \
    redis-server nginx certbot python3-certbot-nginx git
```

## 2. Install Redis

```bash
# Redis should already be installed, verify it's running
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Test Redis connection
redis-cli ping
# Should return: PONG
```

## 3. Create Application User (Optional)

For better security, create a dedicated user:

```bash
sudo useradd -r -s /bin/false ghost-cm-sync
```

Or use the existing `www-data` user (default in service files).

## 4. Clone and Install Application

```bash
# Create app directory
sudo mkdir -p /opt/ghost-cm-sync
cd /opt/ghost-cm-sync

# Clone repository (or upload files)
sudo git clone https://github.com/yourusername/ghost-cm-sync.git .

# Create virtual environment (use python3 on Ubuntu 24.04)
sudo python3 -m venv .venv

# Install dependencies
sudo /opt/ghost-cm-sync/.venv/bin/pip install --upgrade pip
sudo /opt/ghost-cm-sync/.venv/bin/pip install -e .

# Add PyJWT for full-sync script
sudo /opt/ghost-cm-sync/.venv/bin/pip install PyJWT

# Create logs directory
sudo mkdir -p /opt/ghost-cm-sync/logs

# Set ownership
sudo chown -R www-data:www-data /opt/ghost-cm-sync
```

## 5. Configure Environment

```bash
# Copy and edit environment file
sudo cp /opt/ghost-cm-sync/.env.example /opt/ghost-cm-sync/.env
sudo nano /opt/ghost-cm-sync/.env
```

Fill in your actual values. The application supports multiple Ghost sites.

> **Security Note**: Webhook secrets are **mandatory**. The application will fail to validate webhooks if secrets are not configured. Ghost URLs must use HTTPS in production.

```env
# Shared Configuration
CM_API_KEY=your-campaign-monitor-api-key
REDIS_URL=redis://localhost:6379
PORT=3000
LOG_LEVEL=info

# Site 1 Configuration
SITE1_NAME=mainblog
SITE1_GHOST_WEBHOOK_SECRET=webhook-secret-for-site1
SITE1_GHOST_URL=https://blog1.example.com
SITE1_GHOST_ADMIN_API_KEY=admin-api-key-for-site1
SITE1_CM_LIST_ID=cm-list-id-for-site1

# Site 2 Configuration (optional, add more sites as needed)
SITE2_NAME=newsletter
SITE2_GHOST_WEBHOOK_SECRET=webhook-secret-for-site2
SITE2_GHOST_URL=https://blog2.example.com
SITE2_GHOST_ADMIN_API_KEY=admin-api-key-for-site2
SITE2_CM_LIST_ID=cm-list-id-for-site2
```

Secure the environment file:

```bash
sudo chmod 600 /opt/ghost-cm-sync/.env
sudo chown www-data:www-data /opt/ghost-cm-sync/.env
```

## 6. Install Systemd Services

```bash
# Copy service files
sudo cp /opt/ghost-cm-sync/deploy/ghost-cm-sync.service /etc/systemd/system/
sudo cp /opt/ghost-cm-sync/deploy/ghost-cm-worker.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable services to start on boot
sudo systemctl enable ghost-cm-sync ghost-cm-worker

# Start services
sudo systemctl start ghost-cm-sync ghost-cm-worker

# Check status
sudo systemctl status ghost-cm-sync
sudo systemctl status ghost-cm-worker
```

## 7. Configure Nginx with SSL

### Get SSL Certificate

```bash
# Replace with your domain
sudo certbot certonly --nginx -d sync.yourdomain.com
```

### Configure Nginx

```bash
# Edit the nginx config to use your domain
sudo nano /opt/ghost-cm-sync/deploy/nginx.conf
# Replace sync.yourdomain.com with your actual domain

# Copy to sites-available
sudo cp /opt/ghost-cm-sync/deploy/nginx.conf /etc/nginx/sites-available/ghost-cm-sync

# Enable the site
sudo ln -s /etc/nginx/sites-available/ghost-cm-sync /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

## 8. Configure Ghost Webhooks

For **each Ghost site** you want to sync:

1. Go to Ghost Admin → Settings → Integrations
2. Click "Add custom integration"
3. Name it "Campaign Monitor Sync"
4. Add three webhooks using your site's name in the URL:

   For site "mainblog" (SITE1_NAME=mainblog):
   - **Event:** Member added
     **URL:** `https://sync.yourdomain.com/webhook/ghost/mainblog?event=member.added`
   - **Event:** Member updated
     **URL:** `https://sync.yourdomain.com/webhook/ghost/mainblog?event=member.updated`
   - **Event:** Member deleted
     **URL:** `https://sync.yourdomain.com/webhook/ghost/mainblog?event=member.deleted`

   For site "newsletter" (SITE2_NAME=newsletter):
   - **Event:** Member added
     **URL:** `https://sync.yourdomain.com/webhook/ghost/newsletter?event=member.added`
   - **Event:** Member updated
     **URL:** `https://sync.yourdomain.com/webhook/ghost/newsletter?event=member.updated`
   - **Event:** Member deleted
     **URL:** `https://sync.yourdomain.com/webhook/ghost/newsletter?event=member.deleted`

5. Copy the "Webhook Secret" and update your `.env` file with the corresponding SITE{N}_GHOST_WEBHOOK_SECRET

## 9. Configure Campaign Monitor

Create these custom fields in your Campaign Monitor list:

| Field Name | Field Type |
|------------|------------|
| ghost_status | Text |
| ghost_signup_date | Date |
| ghost_last_updated | Date |
| ghost_status_changed_at | Date |
| ghost_previous_status | Text |
| ghost_labels | Text |
| ghost_email_enabled | Text |

## 10. Test the Integration

### Test health endpoint:

```bash
curl https://sync.yourdomain.com/health
```

### Test with a manual webhook (optional):

```bash
# Create a test member in Ghost and watch the logs
sudo journalctl -u ghost-cm-sync -f
sudo journalctl -u ghost-cm-worker -f
```

## 11. Initial Sync (Optional)

If you have existing Ghost members, run a full sync for each site:

```bash
cd /opt/ghost-cm-sync

# List configured sites
sudo -u www-data /opt/ghost-cm-sync/.venv/bin/python scripts/full_sync.py --list-sites

# Dry run first (replace 'mainblog' with your site name)
sudo -u www-data /opt/ghost-cm-sync/.venv/bin/python scripts/full_sync.py --site mainblog --dry-run

# Execute sync for a specific site
sudo -u www-data /opt/ghost-cm-sync/.venv/bin/python scripts/full_sync.py --site mainblog

# With verbose output to see names
sudo -u www-data /opt/ghost-cm-sync/.venv/bin/python scripts/full_sync.py --site mainblog --verbose
```

## Monitoring

### View Logs

```bash
# Webhook server logs
sudo journalctl -u ghost-cm-sync -f

# Worker logs
sudo journalctl -u ghost-cm-worker -f

# Last 100 lines
sudo journalctl -u ghost-cm-sync -n 100
```

### Check Queue Status

```bash
cd /opt/ghost-cm-sync
sudo -u www-data /opt/ghost-cm-sync/.venv/bin/rq info
```

### Health Check

```bash
curl -s https://sync.yourdomain.com/health | jq
curl -s https://sync.yourdomain.com/metrics | jq
```

## Failure Alerts (Slack)

Both `ghost-cm-sync.service` and `ghost-cm-worker.service` are configured with `OnFailure=alert-failure@%n.service`. When either enters the `failed` state, systemd starts a template service that runs `/usr/local/bin/slack-alert-failure.sh`, which tails ~20 lines of journal output for the failed unit and POSTs it to a Slack incoming webhook.

### Setup

1. Create a Slack incoming webhook at <https://api.slack.com/apps> → your app → Incoming Webhooks → Add to channel. Copy the URL.

2. Install the script and template service:

   ```bash
   cd /opt/ghost-cm-sync
   sudo install -m 0755 -o root -g root deploy/slack-alert-failure.sh /usr/local/bin/slack-alert-failure.sh
   sudo cp deploy/alert-failure@.service /etc/systemd/system/
   ```

3. Create the secrets file with your webhook URL:

   ```bash
   sudo mkdir -p /etc/ghost-cm-sync
   sudo cp deploy/notify.env.example /etc/ghost-cm-sync/notify.env
   sudo chmod 600 /etc/ghost-cm-sync/notify.env
   sudo chown root:root /etc/ghost-cm-sync/notify.env
   sudo nano /etc/ghost-cm-sync/notify.env   # paste your SLACK_WEBHOOK_URL
   ```

4. Reload systemd so the updated `ghost-cm-sync.service` / `ghost-cm-worker.service` unit files take effect:

   ```bash
   sudo cp deploy/ghost-cm-sync.service /etc/systemd/system/
   sudo cp deploy/ghost-cm-worker.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl restart ghost-cm-sync ghost-cm-worker
   ```

### Testing

Trigger the alert path without touching the real services:

```bash
sudo systemctl start alert-failure@ghost-cm-sync.service.service
```

A Slack message should arrive within seconds. If not, check `journalctl -u alert-failure@ghost-cm-sync.service.service` for script errors.

### What it doesn't catch

OnFailure only fires when systemd marks a unit `failed` (e.g., crash beyond `Restart=` limits). It does **not** catch:
- Service running fine but receiving no inbound traffic (e.g., Ghost stopped delivering webhooks). See [Troubleshooting: Webhooks not being received](#webhooks-not-being-received).
- Errors logged by the app that don't crash it (signature failures, CM API errors, etc.).

For those, monitor via `journalctl -u ghost-cm-sync -f` or set up an external uptime/log monitor.

## Troubleshooting

### Service won't start

```bash
# Check for errors
sudo journalctl -u ghost-cm-sync -n 50

# Verify environment file exists and is readable
sudo -u www-data cat /opt/ghost-cm-sync/.env
```

### Webhooks not being received

1. Check Ghost webhook configuration in Ghost Admin → Settings → Integrations
2. Verify SSL certificate is valid
3. Check nginx logs: `sudo tail -f /var/log/nginx/ghost-cm-sync.access.log /var/log/nginx/ghost-cm-sync.error.log`
4. **Check Ghost's own outbound error log** (most diagnostic — does Ghost think delivery succeeded?):
   ```bash
   sudo grep -iE "WEBHOOK_DELIVERY_FAILURE|URL_PRIVATE_INVALID" \
       /var/www/*/content/logs/*.error.log | tail -20
   ```

#### Ghost shows "Last triggered" but nginx sees nothing

Ghost's UI updates "Last triggered" even when its outbound HTTP client *aborts* the delivery. If `production.error.log` shows:

```
[WEBHOOK_DELIVERY_FAILURE] ... error_code=URL_PRIVATE_INVALID
message=URL resolves to a non-permitted private IP block
```

...Ghost is refusing to deliver because the webhook hostname resolves to a private/loopback IP. This typically happens when `/etc/hosts` maps the webhook host's FQDN to `127.0.1.1` (Ubuntu's default for the local hostname).

**Fix:** edit `/etc/hosts` and remove the FQDN from the loopback line, keeping only the short hostname:

```
# Before
127.0.1.1 publishing.example.com publishing
# After
127.0.1.1 publishing
```

The FQDN then resolves via public DNS to the server's public IP. The kernel still short-circuits the connection through loopback, but Ghost's SSRF check only inspects the destination IP — it permits the now-public address. No service restart needed; Ghost re-resolves per request.

### Signature validation failing

```bash
# Check for signature-related errors
sudo journalctl -u ghost-cm-sync | grep -i "signature"
```

Common issues:
- **"signature_timestamp_expired"**: Ensure server clock is synchronized (`timedatectl status`). The application handles both second and millisecond timestamps from Ghost.
- **"signature_mismatch"**: Verify the webhook secret in `.env` matches Ghost's integration settings exactly.
- **"webhook secret not configured"**: Ensure `SITE*_GHOST_WEBHOOK_SECRET` is set for the site.

### Campaign Monitor API errors

1. Verify CM_API_KEY and CM_LIST_ID are correct
2. Check custom fields exist in Campaign Monitor
3. Verify API key has correct permissions

#### Specific user not syncing to CM

Search the worker journal for the user's email hash:

```bash
HASH=$(python3 -c "import hashlib; print(hashlib.sha256('USER@EXAMPLE.COM'.lower().encode()).hexdigest()[:12])")
sudo journalctl -u ghost-cm-worker --no-pager | grep "$HASH" | tail -10
```

Look for the structured event:

- `skipped_cm_opt_out` → the user is `Unsubscribed`/`Bounced`/`Deleted` in CM (per-list state). Working as designed; don't reactivate.
- `skipped_cm_suppressed` → the user is on CM's **account-wide suppression list** (cross-list block, distinct from per-list State). Worker silently skips after one attempt rather than retrying. Common causes: spam complaint, manual admin suppression, hard-bounce escalation, or user-requested suppression. Verify in CM Admin → Account → Manage Settings → Suppression List. Decisions to unsuppress should reflect whether the user actually wants email.
- `process_member_*_failed` → genuine CM API failure. Check the error message.

### Redis connection issues

```bash
# Check Redis is running
sudo systemctl status redis-server

# Test connection
redis-cli ping
```

## Updates

To update the application:

```bash
cd /opt/ghost-cm-sync

# Pull latest changes
sudo git pull

# Reinstall dependencies
sudo /opt/ghost-cm-sync/.venv/bin/pip install -e .

# Restart services
sudo systemctl restart ghost-cm-sync ghost-cm-worker
```

## Firewall Configuration (UFW)

If using UFW firewall:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## Security Considerations

### Mandatory Configuration
- **Webhook secrets**: Required for all sites. Application will reject webhooks without valid signatures.
- **HTTPS**: Ghost URLs must use HTTPS in production (HTTP only allowed for localhost in development).

### Rate Limiting
- Webhook endpoint: 100 requests/minute per IP (application-level)
- Campaign Monitor API: 10 requests/second per site with burst of 20
- Consider adding nginx rate limiting as shown in the nginx config

### Environment File Security
```bash
# Ensure .env is only readable by the application user
sudo chmod 600 /opt/ghost-cm-sync/.env
sudo chown www-data:www-data /opt/ghost-cm-sync/.env
```

### Monitoring for Security
```bash
# Watch for signature validation failures (potential attacks)
sudo journalctl -u ghost-cm-sync | grep -i "signature"

# Watch for rate limit hits
sudo journalctl -u ghost-cm-sync | grep -i "rate"
```

### Regular Maintenance
- Rotate webhook secrets periodically in Ghost and update `.env`
- Rotate Campaign Monitor API key periodically
- Keep the application updated with `git pull` and reinstall
- Monitor logs for unusual patterns
