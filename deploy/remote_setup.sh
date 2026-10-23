#!/usr/bin/env bash
# Runs on the instance: packages, Python env (COOL when present), models, web build, service, Caddy.
set -euo pipefail
cd ~/watchful
sudo apt-get update -q
sudo apt-get install -y -q ffmpeg python3-venv python3-pip nodejs npm debian-keyring debian-archive-keyring apt-transport-https curl
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list
  sudo apt-get update -q && sudo apt-get install -y -q caddy
fi

COOL=/opt/cool/venvs/python_3.12
if [ -x "$COOL/bin/python" ]; then
  PY="$COOL/bin/python"
  grep -v '^opencv' requirements.lock > /tmp/req.txt
  sudo "$PY" -m pip install -q -r /tmp/req.txt
else
  [ -d ~/venv ] || python3 -m venv ~/venv
  PY=~/venv/bin/python
  "$PY" -m pip install -q -r requirements.lock
fi
"$PY" -c "import cv2; assert cv2.__version__.startswith('5'), cv2.__version__; print('opencv', cv2.__version__)"
"$PY" scripts/fetch_assets.py > /dev/null
(cd web && npm ci --silent && npm run build --silent)
sudo mkdir -p /var/lib/watchful && sudo chown ubuntu /var/lib/watchful

sudo tee /etc/systemd/system/watchful.service > /dev/null <<UNIT
[Unit]
Description=Watchful
After=network-online.target
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/watchful
EnvironmentFile=/etc/watchful.env
Environment=PYTHONPATH=backend
ExecStart=$PY -m uvicorn watchful.api:create_app --factory --host 127.0.0.1 --port 8000
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
echo "$DOMAIN {
  encode gzip
  reverse_proxy 127.0.0.1:8000
}" | sudo tee /etc/caddy/Caddyfile > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now watchful
sudo systemctl restart watchful caddy
sleep 3
curl -s 127.0.0.1:8000/healthz
