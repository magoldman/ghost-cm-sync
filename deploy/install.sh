#!/bin/bash
# Ghost to Campaign Monitor Sync - Installation Script
# Run as root or with sudo

set -e

APP_DIR="/opt/ghost-cm-sync"
APP_USER="www-data"

echo "Installing Ghost → Campaign Monitor Sync"
echo "========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or with sudo"
    exit 1
fi

# Install Redis if not present
if ! command -v redis-server &> /dev/null; then
    echo "Installing Redis..."
    apt-get update
    apt-get install -y redis-server
    systemctl enable redis-server
    systemctl start redis-server
fi

# Verify Redis is running
if ! redis-cli ping > /dev/null 2>&1; then
    echo "Error: Redis is not running"
    exit 1
fi
echo "Redis: OK"

# Install Python 3.11+ if not present
if ! command -v python3.11 &> /dev/null && ! command -v python3.12 &> /dev/null; then
    echo "Installing Python 3.11..."
    apt-get update
    apt-get install -y python3.11 python3.11-venv python3.11-dev
fi

# Create application directory
echo "Creating application directory..."
mkdir -p $APP_DIR
mkdir -p $APP_DIR/logs

# Copy application files (assumes you're running from the repo root)
echo "Copying application files..."
cp -r src $APP_DIR/
cp -r scripts $APP_DIR/
cp pyproject.toml $APP_DIR/
cp .env.example $APP_DIR/

# Create virtual environment
echo "Creating virtual environment..."
cd $APP_DIR
python3.11 -m venv .venv || python3.12 -m venv .venv

# Install dependencies
echo "Installing dependencies..."
$APP_DIR/.venv/bin/pip install --upgrade pip
$APP_DIR/.venv/bin/pip install -e .

# Set ownership
chown -R $APP_USER:$APP_USER $APP_DIR

# Install systemd services
echo "Installing systemd services..."
cp deploy/ghost-cm-sync.service /etc/systemd/system/
cp deploy/ghost-cm-worker.service /etc/systemd/system/
systemctl daemon-reload

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "1. Copy .env.example to .env and configure:"
echo "   cp $APP_DIR/.env.example $APP_DIR/.env"
echo "   nano $APP_DIR/.env"
echo ""
echo "2. Configure nginx (optional):"
echo "   cp $APP_DIR/deploy/nginx.conf /etc/nginx/sites-available/ghost-cm-sync"
echo "   ln -s /etc/nginx/sites-available/ghost-cm-sync /etc/nginx/sites-enabled/"
echo "   nginx -t && systemctl reload nginx"
echo ""
echo "3. Start the services:"
echo "   systemctl enable ghost-cm-sync ghost-cm-worker"
echo "   systemctl start ghost-cm-sync ghost-cm-worker"
echo ""
echo "4. Check status:"
echo "   systemctl status ghost-cm-sync"
echo "   systemctl status ghost-cm-worker"
echo ""
echo "5. View logs:"
echo "   journalctl -u ghost-cm-sync -f"
echo "   journalctl -u ghost-cm-worker -f"
