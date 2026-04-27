#!/usr/bin/env bash
# Install voicetodo as a systemd service on Debian.
# Run as root from the unpacked source directory:
#   sudo bash install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "Run as root (e.g. sudo bash install.sh)." >&2
   exit 1
fi

INSTALL_DIR=/opt/voicetodo
DATA_DIR=/var/lib/voicetodo
CONFIG_DIR=/etc/voicetodo
SRC_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

echo "==> Installing system packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip ffmpeg

echo "==> Creating service user"
id -u voicetodo &>/dev/null || \
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin voicetodo

echo "==> Creating directories"
mkdir -p "$INSTALL_DIR" "$DATA_DIR" "$DATA_DIR/audio" "$CONFIG_DIR"

echo "==> Copying source to $INSTALL_DIR"
cp -r "$SRC_DIR/voicetodo" "$INSTALL_DIR/"
cp "$SRC_DIR/pyproject.toml" "$INSTALL_DIR/"
cp "$SRC_DIR/requirements.txt" "$INSTALL_DIR/"
[[ -f "$SRC_DIR/README.md" ]] && cp "$SRC_DIR/README.md" "$INSTALL_DIR/"

echo "==> Creating virtualenv and installing"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

echo "==> Installing default config (if missing)"
if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
    cp "$SRC_DIR/config.example.yaml" "$CONFIG_DIR/config.yaml"
fi

# Generate a random api key on first install if user didn't set one.
if grep -qE '^api_key:\s*""\s*$' "$CONFIG_DIR/config.yaml" \
   && [[ ! -f "$CONFIG_DIR/voicetodo.env" ]]; then
    KEY="$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | cut -c1-40)"
    echo "VOICETODO_API_KEY=${KEY}" > "$CONFIG_DIR/voicetodo.env"
    chmod 640 "$CONFIG_DIR/voicetodo.env"
    echo
    echo "    Generated API key (also saved to $CONFIG_DIR/voicetodo.env):"
    echo "    ${KEY}"
    echo
fi

echo "==> Setting permissions"
chown -R voicetodo:voicetodo "$INSTALL_DIR" "$DATA_DIR"
chown -R root:voicetodo      "$CONFIG_DIR"
chmod 750 "$CONFIG_DIR"
chmod 640 "$CONFIG_DIR/config.yaml"

echo "==> Installing systemd unit"
cp "$SRC_DIR/voicetodo.service" /etc/systemd/system/voicetodo.service
systemctl daemon-reload
systemctl enable voicetodo

cat <<EOF

Done.

Edit /etc/voicetodo/config.yaml to taste, then:

    systemctl start voicetodo
    journalctl -u voicetodo -f

The HTTP API listens on 0.0.0.0:8765 by default. Smoke test:

    curl http://localhost:8765/health

EOF
