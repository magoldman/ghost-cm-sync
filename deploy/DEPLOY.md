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

Fill in your actual values. The application supports multiple Ghost sites:

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

## Troubleshooting

### Service won't start

```bash
# Check for errors
sudo journalctl -u ghost-cm-sync -n 50

# Verify environment file exists and is readable
sudo -u www-data cat /opt/ghost-cm-sync/.env
```

### Webhooks not being received

1. Check Ghost webhook configuration
2. Verify SSL certificate is valid
3. Check nginx logs: `sudo tail -f /var/log/nginx/ghost-cm-sync.error.log`

### Campaign Monitor API errors

1. Verify CM_API_KEY and CM_LIST_ID are correct
2. Check custom fields exist in Campaign Monitor
3. Verify API key has correct permissions

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
