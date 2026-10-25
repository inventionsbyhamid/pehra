#!/usr/bin/env bash
# The baseline for the COOL benchmark: a second Python environment on the same instance with stock
# opencv-python-headless 5.0.0.93 from PyPI and the same requirements, beside COOL's. Run on the instance as root:
#   deploy/deploy.sh --run 'bash /opt/watchful/current/deploy/baseline_venv.sh'
# Then /usr/local/bin/watchful-python-stock runs a script with it, as /usr/local/bin/watchful-python does with COOL's.
# A benchmark can make the same environment on its own instances with APP, SRC and VENV set; on a machine without
# the app's settings (/etc/watchful/watchful.env) no wrapper is written.
set -euo pipefail
APP="${APP:-/opt/watchful}"
SRC="${SRC:-$APP/current}"
VENV="${VENV:-$APP/venv-stock}"
STOCK_OPENCV="opencv-python-headless==5.0.0.93"
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore

[ -d "$VENV" ] || python3.12 -m venv "$VENV"
grep -vE '^(opencv-python|opencv-contrib-python|pytest|moto|cfn-lint)([=<>~!; []|$)' \
  "$SRC/requirements.in" > /tmp/watchful-stock-requirements.txt
echo "$STOCK_OPENCV" >> /tmp/watchful-stock-requirements.txt
env -u LD_LIBRARY_PATH "$VENV/bin/python" -m pip install -q -r /tmp/watchful-stock-requirements.txt \
  -c "$SRC/requirements.lock"

if [ ! -f /etc/watchful/watchful.env ]; then
  env -u LD_LIBRARY_PATH "$VENV/bin/python" "$SRC/deploy/cv2_info.py"
  exit 0
fi
cat > /usr/local/bin/watchful-python-stock <<WRAP
#!/bin/bash
# stock OpenCV from PyPI (no COOL), with the app's settings, as the watchful user, in the current release
set -a; . /etc/watchful/watchful.env; set +a
unset LD_LIBRARY_PATH
cd $APP/current
exec setpriv --reuid=watchful --regid=watchful --init-groups env HOME=/var/lib/watchful "$VENV/bin/python" $APP/current/deploy/ssm_env.py -- "$VENV/bin/python" "\$@"
WRAP
chmod 755 /usr/local/bin/watchful-python-stock
/usr/local/bin/watchful-python-stock "$SRC/deploy/cv2_info.py"
