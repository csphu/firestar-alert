#!/usr/bin/env bash
# ============================================================
# install.sh — Deploy firestar-alert on the Raspberry Pi
#
# Run from the directory where you cloned / copied the project:
#   chmod +x install.sh
#   ./install.sh
# ============================================================
set -euo pipefail

SERVICE_NAME="firestar-alert"
INSTALL_DIR="$HOME/$SERVICE_NAME"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "============================================"
echo "  Firestar XP Boiler Monitor — Installer"
echo "============================================"
echo

# ---- 1. Create installation directory ----
echo "▶  Creating $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"

# ---- 2. Copy project files (skip if already running from install dir) ----
echo "▶  Copying files ..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    cp "$SCRIPT_DIR/monitor.py" "$SCRIPT_DIR/config.ini" "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
else
    echo "   (already in install directory, skipping copy)"
fi

# ---- 3. Create Python virtual environment ----
echo "▶  Creating Python virtual environment ..."
python3 -m venv "$INSTALL_DIR/venv"

# ---- 4. Install dependencies ----
echo "▶  Installing Python dependencies ..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet

# ---- 5. Fix ownership ----
CURRENT_USER="$(id -un)"
echo "▶  Setting ownership to $CURRENT_USER ..."
sudo chown -R "$CURRENT_USER:$CURRENT_USER" "$INSTALL_DIR"

# ---- 6. Install systemd service ----
echo "▶  Installing systemd service ..."
# Replace the placeholder install dir with the actual path
sed "s|/home/pi/firestar-alert|$INSTALL_DIR|g; s|User=pi|User=$CURRENT_USER|g; s|Group=pi|Group=$CURRENT_USER|g" \
    firestar-alert.service \
    | sudo tee "$SERVICE_FILE" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

# ---- Done ----
echo
echo "============================================"
echo "  Installation complete!"
echo "============================================"
echo
echo "NEXT STEPS:"
echo
echo "  1. Edit the config file with your ntfy topic:"
echo "       nano $INSTALL_DIR/config.ini"
echo
echo "  2. Start the service:"
echo "       sudo systemctl start $SERVICE_NAME"
echo
echo "  3. Check that it's running:"
echo "       sudo systemctl status $SERVICE_NAME"
echo
echo "  4. Watch live logs:"
echo "       journalctl -u $SERVICE_NAME -f"
echo
echo "  To stop the service:    sudo systemctl stop $SERVICE_NAME"
echo "  To disable on boot:     sudo systemctl disable $SERVICE_NAME"
echo "  To view full logs:      journalctl -u $SERVICE_NAME --no-pager"
echo
