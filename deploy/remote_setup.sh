#!/usr/bin/env bash
# Runs on the instance as root; deploy/deploy.sh sends it through SSM Run Command after unpacking the release.
# Installs packages, our requirements into COOL's Python (without opencv-python-headless, so COOL's OpenCV 5 is
# the cv2 that loads), the models and the web build, then the systemd units (the app as one process with the camera
# worker processes as its children, the job worker on its own so systemd restarts it, Litestream), Caddy for TLS and
# the CloudWatch agent for logs, disk and memory. Safe to run again: each step checks what is already there.
# Without /opt/cool (deploy.sh with a blank COOL_AMI_ID: Amazon Linux 2023 arm64) it is the stock mode instead: a
# Python 3.12 venv with requirements.lock's opencv-python-headless, the packages from dnf, and no COOL anywhere.
set -euo pipefail
: "${STACK:?}" "${DOMAIN:?}" "${REGION:?}" "${BUCKET:?}" "${APP_LOG_GROUP:?}" "${ACCESS_LOG_GROUP:?}"
: "${BEDROCK_MODEL:?}" "${ACME_EMAIL:?}" "${RELEASE:?}"

APP=/opt/watchful
SRC="$APP/releases/$RELEASE"
COOL_VENV="${COOL_VENV:-/opt/cool/venvs/python_3.12}"  # COOL's own Python 3.12 environment
COOL_LIB=/opt/cool/cpp_sdk/lib
PY="$COOL_VENV/bin/python"
MODE=cool
[ -d /opt/cool ] || { MODE=stock; PY="$APP/venv/bin/python"; COOL_LIB=""; }
DATA=/var/lib/watchful
LOGS=/var/log/watchful
NODE_VERSION=22.23.4
NODE_SHA256=6287aa46b6c375d816e63ae66d024fbbfd70404825aabdfdf898a970871d2f4b
LITESTREAM_VERSION=0.5.17
LITESTREAM_SHA256=f8ca4a050095c1efbda2c4365172e61bf9d955ea0d9ac42f448b52e51819baa5
CWAGENT_DEB=https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/arm64/latest/amazon-cloudwatch-agent.deb
# stock mode only: Amazon Linux 2023 has neither Caddy nor FFmpeg in its repositories
CADDY_VERSION=2.11.7
CADDY_SHA512=3db36ba90c7a6e8dda40ee3dd71fa08844c76b5fb08f61b31e5e78d2ed38e71c51dc7baed875e50d1ca1279196e84302967237386ae87c91ae9f2aaceada682e
FFMPEG_VERSION=7.0.2
FFMPEG_SHA256=f4149bb2b0784e30e99bdda85471c9b5930d3402014e934a5098b41d0f7201b1

mkdir -p "$LOGS"
exec 3>&1 >> "$LOGS/deploy.log" 2>&1  # the details go to the log (and CloudWatch); say() also reaches deploy.sh
say() { echo "$*"; echo "$*" >&3; }
check() { local out; if out="$("$@" 2>&1)"; then say "$out"; else say "$out"; return 1; fi; }
trap 'say "failed at line $LINENO; see $LOGS/deploy.log"' ERR
say "release $RELEASE on $(hostname), $(date -u +%FT%TZ)"
[ "$(uname -m)" = aarch64 ] || { say "this setup is for Graviton (aarch64)"; exit 1; }
[ "$MODE" = stock ] || [ -x "$PY" ] || { say "COOL's Python is not at $COOL_VENV: launch from the COOL Marketplace AMI"; exit 1; }
export DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore

if [ "$MODE" = stock ]; then
say "packages (stock OpenCV, not COOL: Amazon Linux 2023)"
export PATH="/usr/local/bin:$PATH"
dnf install -y -q python3.12 python3.12-devel python3.12-pip gcc gcc-c++ make tar xz gzip logrotate nftables util-linux \
  shadow-utils findutils amazon-cloudwatch-agent
if ! /usr/local/bin/ffmpeg -version 2> /dev/null | grep -q "version $FFMPEG_VERSION"; then
  curl -fsSL -o /tmp/ffmpeg.tar.xz "https://johnvansickle.com/ffmpeg/releases/ffmpeg-$FFMPEG_VERSION-arm64-static.tar.xz"
  echo "$FFMPEG_SHA256  /tmp/ffmpeg.tar.xz" | sha256sum -c -
  tar -xJf /tmp/ffmpeg.tar.xz -C /usr/local/bin --strip-components=1 \
    "ffmpeg-$FFMPEG_VERSION-arm64-static/ffmpeg" "ffmpeg-$FFMPEG_VERSION-arm64-static/ffprobe"
  rm -f /tmp/ffmpeg.tar.xz
fi
if ! /usr/bin/caddy version 2> /dev/null | grep -q "v$CADDY_VERSION"; then
  curl -fsSL -o /tmp/caddy.tgz \
    "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION/caddy_${CADDY_VERSION}_linux_arm64.tar.gz"
  echo "$CADDY_SHA512  /tmp/caddy.tgz" | sha512sum -c -
  tar -xzf /tmp/caddy.tgz -C /usr/bin caddy && rm -f /tmp/caddy.tgz
fi
# what Caddy's Debian package makes: its user, /etc/caddy and the unit
getent group caddy > /dev/null || groupadd --system caddy
id caddy > /dev/null 2>&1 || useradd --system --gid caddy --home-dir /var/lib/caddy --create-home \
  --shell /usr/sbin/nologin caddy
install -d -m 755 /etc/caddy
install -m 644 "$SRC/deploy/caddy.service" /etc/systemd/system/caddy.service
else
say "packages"
apt-get update -q
apt-get install -y -q ffmpeg curl ca-certificates xz-utils logrotate python3-venv python3-dev build-essential nftables \
  gnupg debian-keyring debian-archive-keyring apt-transport-https
if ! command -v caddy > /dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi
if [ ! -x /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl ]; then
  curl -fsSL -o /tmp/cwagent.deb "$CWAGENT_DEB" && dpkg -i -E /tmp/cwagent.deb && rm -f /tmp/cwagent.deb
fi
fi
if [ "$(/opt/node/bin/node -v 2> /dev/null || true)" != "v$NODE_VERSION" ]; then
  curl -fsSL -o /tmp/node.tar.xz "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-arm64.tar.xz"
  echo "$NODE_SHA256  /tmp/node.tar.xz" | sha256sum -c -
  rm -rf /opt/node && mkdir -p /opt/node && tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1
  rm -f /tmp/node.tar.xz
fi
if ! /usr/local/bin/litestream version 2> /dev/null | grep -q "$LITESTREAM_VERSION"; then
  curl -fsSL -o /tmp/litestream.tgz \
    "https://github.com/benbjohnson/litestream/releases/download/v$LITESTREAM_VERSION/litestream-$LITESTREAM_VERSION-linux-arm64.tar.gz"
  echo "$LITESTREAM_SHA256  /tmp/litestream.tgz" | sha256sum -c -
  tar -xzf /tmp/litestream.tgz -C /usr/local/bin litestream && rm -f /tmp/litestream.tgz
fi

say "user and folders"
id watchful > /dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin watchful
install -d -o watchful -g watchful -m 750 "$DATA" "$DATA/samples"
install -d -o root -g watchful -m 750 /etc/watchful
install -d -m 755 "$APP/models"

if [ "$MODE" = stock ]; then
say "python: a Python 3.12 venv with our requirements, opencv-python-headless from the lock (stock OpenCV, not COOL)"
[ -x "$PY" ] || python3.12 -m venv "$APP/venv"
grep -vE '^(pytest|moto|cfn-lint)([=<>~!; []|$)' "$SRC/requirements.in" > /tmp/watchful-requirements.txt
cp "$SRC/requirements.lock" /tmp/watchful-constraints.txt
else
say "python: our requirements into COOL's environment, without opencv-python-headless"
# tools for tests and development stay out of production; the lock pins every version
grep -vE '^(opencv-python|opencv-contrib-python|pytest|moto|cfn-lint)([=<>~!; []|$)' \
  "$SRC/requirements.in" > /tmp/watchful-requirements.txt
grep -viE '^opencv' "$SRC/requirements.lock" > /tmp/watchful-constraints.txt
fi
"$PY" -m pip --version > /dev/null 2>&1 || "$PY" -m ensurepip --upgrade
"$PY" -m pip install -q -r /tmp/watchful-requirements.txt -c /tmp/watchful-constraints.txt
cat > /usr/local/bin/watchful-python <<EOF
#!/bin/bash
# the app's Python (COOL's, or the stock venv) with its settings (the env file, then SSM), as the watchful user, in
# the current release
set -a; . /etc/watchful/watchful.env; set +a
cd $APP/current
exec setpriv --reuid=watchful --regid=watchful --init-groups env HOME=$DATA "$PY" $APP/current/deploy/ssm_env.py -- "$PY" "\$@"
EOF
chmod 755 /usr/local/bin/watchful-python

say "models and sample clips"
# fetch_assets.py writes models/ and data/samples/ under the folder it runs in
(cd "$APP" && env ${COOL_LIB:+LD_LIBRARY_PATH="$COOL_LIB"} "$PY" "$SRC/scripts/fetch_assets.py")
cp -rn "$APP"/data/samples/* "$DATA/samples/" 2> /dev/null || true  # -r: demo clips sit in samples/demo/
chown -R watchful:watchful "$DATA/samples"
# paths under data/ (demo cameras' clips) resolve to the data folder, as in development
ln -sfn "$DATA" "$SRC/data"

say "web build"
# the lockfile is made on x86, so on Graviton the bundlers' arm64 builds are added at the versions it pins
(cd "$SRC/web" && export PATH="/opt/node/bin:$PATH" && npm ci --no-audit --no-fund --loglevel=error \
  && if [ "$(uname -m)" = aarch64 ]; then npm install --no-save --no-audit --no-fund --loglevel=error \
       "@rollup/rollup-linux-arm64-gnu@$(node -p "require('rollup/package.json').version")" \
       "@esbuild/linux-arm64@$(node -p "require('esbuild/package.json').version")"; fi \
  && SITE_URL="https://$DOMAIN" VITE_HOSTING=aws npm run build)
[ -f "$SRC/web/dist/index.html" ] || { say "the web build made no index.html"; exit 1; }

say "settings"
# what the stack decides, then defaults the owner may change. Secrets (Telegram's among them) and the owner's choices
# come from SSM under /$STACK/ when a service starts (deploy/ssm_env.py) and win over this file, so deploy.sh refuses
# the stack's names in params.env. PUBLIC_BASE_URL also sets the socket origin in the page's policy. RTSP goes over
# TCP: the security group lets no UDP out, and FFmpeg would try UDP first.
cat > /etc/watchful/watchful.env <<EOF
# decided by the stack (deploy.sh STACK_DECIDED)
SSM_PREFIX=/$STACK/
AWS_REGION=$REGION
AWS_DEFAULT_REGION=$REGION
STORAGE=s3
S3_BUCKET=$BUCKET
METRICS=cloudwatch
RUN_JOBS_IN_APP=false
BEDROCK_MODEL=$BEDROCK_MODEL
PUBLIC_BASE_URL=https://$DOMAIN
DATA_DIR=$DATA
DATABASE_URL=sqlite:///$DATA/watchful.db
MODELS_DIR=$APP/models
PYTHONPATH=$APP/current/backend
PYTHONUNBUFFERED=1
LD_LIBRARY_PATH=$COOL_LIB
OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp"
# defaults the owner may change in deploy/params.env
AI_PROVIDER=bedrock
EOF
[ -n "$COOL_LIB" ] || sed -i '/^LD_LIBRARY_PATH=/d' /etc/watchful/watchful.env  # stock: no COOL libraries
chown root:watchful /etc/watchful/watchful.env && chmod 640 /etc/watchful/watchful.env

say "services"
for unit in watchful-app watchful-worker watchful-litestream; do
  sed -e "s|@PYTHON@|$PY|g" -e "s|@APP@|$APP|g" "$SRC/deploy/systemd/$unit.service" > "/etc/systemd/system/$unit.service"
done
sed -e "s|@APP@|$APP|g" "$SRC/deploy/egress.service" > /etc/systemd/system/watchful-egress.service
sed -e "s|@DOMAIN@|$DOMAIN|g" -e "s|@ACME_EMAIL@|$ACME_EMAIL|g" "$SRC/deploy/Caddyfile" > /etc/caddy/Caddyfile
install -d -o caddy -g caddy /var/log/caddy
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
install -m 644 "$SRC/deploy/logrotate.conf" /etc/logrotate.d/watchful
sed -e "s|@APP_LOG_GROUP@|$APP_LOG_GROUP|g" -e "s|@ACCESS_LOG_GROUP@|$ACCESS_LOG_GROUP|g" \
  "$SRC/deploy/cloudwatch-agent.json" > /opt/aws/amazon-cloudwatch-agent/etc/watchful.json
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/watchful.json

# a new instance starts from the replica in S3 (db/), if there is one
if [ ! -s "$DATA/watchful.db" ]; then
  setpriv --reuid=watchful --regid=watchful --init-groups env HOME="$DATA" S3_BUCKET="$BUCKET" AWS_REGION="$REGION" \
    /usr/local/bin/litestream restore -config "$SRC/deploy/litestream.yml" -if-replica-exists "$DATA/watchful.db"
fi

ln -sfn "$SRC" "$APP/current"
systemctl daemon-reload
# one app process (sessions, live views and the camera pool live in its memory) with RUN_JOBS_IN_APP=false, and
# one job worker beside it
systemctl enable caddy watchful-egress watchful-app watchful-worker watchful-litestream
# the app user's new connections to private, loopback and link local addresses are dropped before the app starts:
# FFmpeg follows RTSP redirects (deploy/egress.nft, backend/watchful/netguard.py)
systemctl restart watchful-egress
nft list table inet watchful_egress > /dev/null
systemctl restart watchful-app watchful-worker watchful-litestream
systemctl reload-or-restart caddy

for i in $(seq 1 100); do  # the first start loads the models
  curl -fsS http://127.0.0.1:8000/healthz > /tmp/healthz 2> /dev/null && break
  [ "$i" = 100 ] && { say "the app did not answer /healthz in 5 minutes; see $LOGS/app.log"; exit 1; }
  sleep 3
done
say "healthz $(cat /tmp/healthz)"
say "services: $(systemctl is-active watchful-app watchful-worker watchful-litestream caddy | tr '\n' ' ')"

failed=0
if [ "$MODE" = stock ]; then
say "opencv in the app's Python: stock OpenCV, not COOL; OpenCV 5 on arm64, and it decodes H.264 and H.265"
check /usr/local/bin/watchful-python "$SRC/deploy/cv2_info.py" --require-stock || failed=1
else
say "opencv in the app's Python (COOL proof), and it decodes H.264 and H.265 as camera streams and recordings need"
check /usr/local/bin/watchful-python "$SRC/deploy/cv2_info.py" --require-cool || failed=1
fi
say "memory: the app, its camera processes and the job worker within the budget"
check /usr/local/bin/watchful-python "$SRC/deploy/check_memory.py" || failed=1

# keep the current release and the two before it
{ ls -1dt "$APP"/releases/*/ | tail -n +4 | grep -v "^$(readlink -f "$APP/current")/\?$" || true; } | xargs -r rm -rf
[ "$failed" = 0 ] || { say "the app runs, but a check above failed"; exit 1; }
say "done"
