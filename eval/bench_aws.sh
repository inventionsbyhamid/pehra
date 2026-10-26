#!/usr/bin/env bash
# The live pool benchmark on AWS in one command. It starts c8g.large from the COOL AMI, which runs eval/bench.py in
# COOL's Python and then, on the same instance, with stock opencv-python-headless 5.0.0.93 for arm64
# (deploy/baseline_venv.sh); and c7i.large (x86, Ubuntu 24.04) with stock OpenCV. Both run at once, each benchmark for
# at most BENCH_MINUTES, and the JSONs come back into eval/results as bench-<setup>.json (c8g_cool, c8g_stock,
# c7i_stock). It spends money, so it prints the expected cost and starts nothing without --yes-spend. Every instance,
# the temporary IAM role and the bucket it makes are deleted when it exits (trap); each instance also powers off, and
# so terminates, after its hard cap by itself, in case this laptop goes away first.
#   eval/bench_aws.sh                           the plan and the expected cost; starts nothing
#   eval/bench_aws.sh --yes-spend               both instances
#   eval/bench_aws.sh --yes-spend --only c8g    one of them: c8g (COOL, then stock) or c7i (x86)
#   eval/bench_aws.sh --cameras 12 --seconds 180   cameras per run and the measured window (bench.py)
#   eval/bench_aws.sh --params FILE             another params file (default deploy/params.env)
# Needs the aws CLI with the params file's AWS_PROFILE (EC2, S3, IAM, SSM), the COOL subscription on AWS Marketplace,
# and committed code: git archive HEAD is what runs. (from packsure e97a509 eval/bench_aws.sh)
set -euo pipefail

BENCH_MINUTES=20  # one setup at most: the run and its --confirm run, warm ups included
SETUP_MINUTES=25  # packages, the Python requirements and the models before the first benchmark
SPARE_MINUTES=10  # booting, uploads and powering off
VOLUME_GB=40
COOL_VERSION=3.1  # the AWS Marketplace listing version of the default COOL AMI (eval/prices.json software.cool)
UBUNTU_X86=/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
POLL_S="${POLL_S:-30}"
CAMERAS=12
SECONDS_MEASURED=180

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS="${BENCH_RESULTS:-$ROOT/eval/results}"
die() { echo "bench_aws: $*" >&2; exit 1; }

# on the instance (the user data runs this from the unpacked bundle)

SRC=/opt/bench/src
OUT=/opt/bench/results
PY=""  # the Python of the setup being run
UPLOADER=""  # a Python with boto3: the last environment made

prepare() {  # setup: its Python environment, the models and sample clips once
  case "$1" in
    *_cool)
      PY=/opt/cool/venvs/python_3.12/bin/python  # COOL's own Python, as deploy/remote_setup.sh uses it
      [ -x "$PY" ] || { echo "COOL's Python is not at $PY"; ls /opt/cool /opt/cool/venvs; return 1; }
      export LD_LIBRARY_PATH=/opt/cool/cpp_sdk/lib
      grep -vE '^(opencv-python|opencv-contrib-python|pytest|moto|cfn-lint)([=<>~!; []|$)' \
        "$SRC/requirements.in" > /tmp/requirements.txt || return 1
      "$PY" -m pip --version > /dev/null 2>&1 || "$PY" -m ensurepip --upgrade || return 1
      "$PY" -m pip install -q -r /tmp/requirements.txt -c /tmp/constraints.txt || return 1 ;;
    *)
      unset LD_LIBRARY_PATH
      PY=/opt/bench/venv-stock/bin/python
      APP=/opt/bench SRC="$SRC" VENV=/opt/bench/venv-stock bash "$SRC/deploy/baseline_venv.sh" || return 1 ;;
  esac
  UPLOADER="$PY"
  if [ ! -f /opt/bench/models.ok ]; then
    echo "== models and sample clips"
    (cd "$SRC" && "$PY" scripts/fetch_assets.py) || return 1
    touch /opt/bench/models.ok
  fi
}

on_instance() {
  local setup status=0
  mkdir -p "$OUT" /opt/bench/data /opt/bench/logs
  exec > >(tee -a /opt/bench/logs/bench.log) 2>&1
  export DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore

  upload() {  # results, the log and a done marker, with the instance role
    [ -n "$UPLOADER" ] || { echo "no Python environment was made, nothing to upload"; return 0; }
    "$UPLOADER" - "$BENCH_BUCKET" "$BENCH_HOST" "$OUT" /opt/bench/logs/bench.log <<'PY' || true
import sys
from pathlib import Path
import boto3
bucket, host, out, log = sys.argv[1:]
s3 = boto3.client("s3")
for path in sorted(Path(out).glob("*.json")):
    s3.upload_file(str(path), bucket, f"results/{path.name}")
    print("uploaded", path.name)
s3.upload_file(log, bucket, f"logs/{host}.log")
s3.put_object(Bucket=bucket, Key=f"done/{host}", Body=b"done")
PY
  }
  trap upload EXIT

  echo "== $BENCH_HOST: $(uname -m), $(nproc) CPUs, $(date -u +%FT%TZ), code $BENCH_CODE"
  # a fresh Ubuntu may still be running its own apt jobs: wait for the lock rather than fail
  apt-get -o DPkg::Lock::Timeout=600 update -q
  apt-get -o DPkg::Lock::Timeout=600 install -y -q python3-venv python3-dev build-essential curl ca-certificates \
    ffmpeg
  grep -viE '^opencv' "$SRC/requirements.lock" > /tmp/constraints.txt
  for setup in $BENCH_SETUPS; do
    echo "== $setup: environment"
    if ! prepare "$setup"; then
      echo "== $setup: the environment could not be made"
      status=1
      continue
    fi
    if [ "${setup%_cool}" != "$setup" ]; then  # COOL's OpenCV as the app will load it
      echo "== $setup: deploy/cv2_info.py --require-cool"
      "$PY" "$SRC/deploy/cv2_info.py" --require-cool || { echo "== $setup: the COOL check failed with $?"; status=1; }
    fi
    echo "== $setup: benchmark, at most $BENCH_MINUTES minutes"
    # the app's settings need a session secret and a data key; bench.py makes throwaway ones for its own runs
    (cd "$SRC" && env DATA_DIR=/opt/bench/data PYTHONPATH="$SRC/backend" GIT_SHA="$BENCH_CODE" \
      ENGINE_SHA="$BENCH_ENGINE" SESSION_SECRET="$(head -c 32 /dev/urandom | base64)" \
      DATA_KEY="$(head -c 48 /dev/urandom | base64)" AI_PROVIDER=none \
      timeout -s INT -k 120 "${BENCH_MINUTES}m" "$PY" eval/bench.py --setup "$setup" --instance "$BENCH_INSTANCE" \
      --cameras "$BENCH_CAMERAS" --seconds "$BENCH_SECONDS" --confirm --out "$OUT") \
      || { echo "== $setup: the benchmark ended with $?"; status=1; }
  done
  return "$status"
}

if [ "${1:-}" = "--on-instance" ]; then
  on_instance
  exit $?
fi

# on the laptop

YES=0
ONLY=""
PARAMS="$ROOT/deploy/params.env"
while [ $# -gt 0 ]; do
  case "$1" in
    --yes-spend) YES=1 ;;
    --only) ONLY="$2"; shift ;;
    --cameras) CAMERAS="$2"; shift ;;
    --seconds) SECONDS_MEASURED="$2"; shift ;;
    --params) PARAMS="$2"; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done
case "$ONLY" in ''|c8g|c7i) ;; *) die "--only takes c8g or c7i" ;; esac
case "$CAMERAS$SECONDS_MEASURED" in *[!0-9]*) die "--cameras and --seconds take whole numbers" ;; esac

param() {  # a value from the params file (never printed), else the default
  local found=""
  [ -f "$PARAMS" ] && found="$(grep -E "^$1=" "$PARAMS" | tail -n 1 | cut -d= -f2- | sed -e "s/^[\"']//" -e "s/[\"']\$//")"
  printf '%s' "${found:-$2}"
}
SLUG="$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["name"].lower())' "$ROOT/brand.json")"
PROFILE="$(param AWS_PROFILE "$SLUG")"
REGION="$(param REGION ap-south-1)"
COOL_AMI="${COOL_AMI_ID:-$(param COOL_AMI_ID "")}"
DEFAULT_COOL_AMI="$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["software"]["cool"]["ami"].get(sys.argv[2], ""))' "$ROOT/eval/prices.json" "$REGION")"
case "$COOL_AMI" in ''|ami-0123456789abcdef0) COOL_AMI="$DEFAULT_COOL_AMI" ;; esac  # blank, or the example's
[ -n "$COOL_AMI" ] || die "no COOL AMI for $REGION: set COOL_AMI_ID in $PARAMS"

HOSTS=""
[ "$ONLY" = c7i ] || HOSTS="c8g"
[ "$ONLY" = c8g ] || HOSTS="$HOSTS c7i"
HOSTS="${HOSTS# }"
setups() { [ "$1" = c8g ] && echo "c8g_cool c8g_stock" || echo "c7i_stock"; }
itype() { [ "$1" = c8g ] && echo c8g.large || echo c7i.large; }
cap() { local n; n=$(setups "$1" | wc -w | tr -d ' '); echo $((SETUP_MINUTES + n * BENCH_MINUTES + SPARE_MINUTES)); }

echo "Live pool benchmark on AWS, region $REGION, profile $PROFILE"
for h in $HOSTS; do
  if [ "$h" = c8g ]; then
    echo "  c8g.large from the COOL AMI $COOL_AMI (COOL $COOL_VERSION): c8g_cool, then c8g_stock; at most $(cap c8g) minutes"
  else
    echo "  c7i.large from Ubuntu 24.04 x86 ($UBUNTU_X86): c7i_stock; at most $(cap c7i) minutes"
  fi
done
echo "  each: $CAMERAS cameras for $SECONDS_MEASURED s, then the cameras that fit at 2 frames a second (--confirm)"
echo "  each benchmark at most $BENCH_MINUTES minutes; setup at most $SETUP_MINUTES; $VOLUME_GB GB gp3 each"
python3 -I - "$ROOT/eval/prices.json" "$VOLUME_GB" $(for h in $HOSTS; do echo "$(itype "$h"):$(cap "$h")"; done) <<'PY'
import json, math, sys
prices, gb = json.load(open(sys.argv[1])), float(sys.argv[2])
cool = prices["software"]["cool"]["usd_per_hour"]
total, parts = 0.0, []
for spec in sys.argv[3:]:
    itype, minutes = spec.split(":")
    hours = int(minutes) / 60
    inst = prices["instances"][itype]["usd_per_hour"] * hours
    fee = cool.get(itype, 0.0) * math.ceil(hours) if itype.startswith("c8g") else 0.0
    ipv4 = prices["public_ipv4"]["usd_per_hour"] * hours
    ebs = prices["ebs"]["gp3_gb_month"] * gb * hours / 730
    total += inst + fee + ipv4 + ebs
    parts.append(f"{itype} ${inst + fee + ipv4 + ebs:.3f} (instance ${inst:.3f}" +
                 (f", COOL ${fee:.2f} in whole hours" if fee else "") + f", IPv4 ${ipv4:.3f}, EBS ${ebs:.4f})")
print(f"expected cost: at most ${total:.2f} if every cap is reached: " + "; ".join(parts) +
      ". S3 and IAM: under $0.01. EC2 bills by the second, so a run that ends early costs less.")
print("prices: eval/prices.json (Mumbai, on demand, read " + prices["instances"]["c8g.large"]["checked"] + ")")
PY

if [ "$YES" != 1 ]; then
  echo "Nothing started. Run again with --yes-spend to start it." >&2
  exit 2
fi

for tool in aws tar gzip python3; do command -v "$tool" > /dev/null || die "needs $tool on PATH"; done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")
export AWS_PAGER=""
awsr() { aws "$@" "${AWS_ARGS[@]}"; }

RUN="$SLUG-bench-$(date -u +%Y%m%d%H%M%S)"
WORK="$(mktemp -d)"
LOGS="$(mktemp -d)"
INSTANCES=""
BUCKET=""
ROLE=""

cleanup() {
  local code=$?
  trap - EXIT INT TERM
  set +e
  if [ -n "$INSTANCES" ]; then
    echo "terminating $INSTANCES"
    awsr ec2 terminate-instances --instance-ids $INSTANCES --query 'TerminatingInstances[].CurrentState.Name' --output text
    awsr ec2 wait instance-terminated --instance-ids $INSTANCES
  fi
  if [ -n "$ROLE" ]; then
    awsr iam remove-role-from-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE" > /dev/null 2>&1
    awsr iam delete-instance-profile --instance-profile-name "$ROLE" > /dev/null 2>&1
    awsr iam delete-role-policy --role-name "$ROLE" --policy-name results > /dev/null 2>&1
    awsr iam delete-role --role-name "$ROLE" > /dev/null 2>&1 && echo "deleted the role $ROLE"
  fi
  if [ -n "$BUCKET" ]; then
    awsr s3 rb "s3://$BUCKET" --force > /dev/null 2>&1 && echo "deleted the bucket $BUCKET"
  fi
  rm -rf "$WORK"
  exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if [ -n "${BENCH_TARBALL:-}" ]; then  # a prebuilt tar of the code instead of `git archive HEAD` (the tests use it)
  cp "$BENCH_TARBALL" "$WORK/bundle.tar"
  CODE="$(git -C "$ROOT" rev-parse --short HEAD 2> /dev/null || echo unknown)"
  ENGINE="$CODE"
else
  command -v git > /dev/null || die "needs git"
  CODE="$(git -C "$ROOT" rev-parse --short HEAD)"
  ENGINE="$(git -C "$ROOT" log -1 --format=%h -- backend)"
  [ -z "$(git -C "$ROOT" status --porcelain -- backend deploy eval scripts requirements.*)" ] || \
    echo "warning: uncommitted changes are not shipped; the benchmark runs $CODE"
  git -C "$ROOT" archive --format=tar HEAD > "$WORK/bundle.tar"
fi
ACCOUNT="$(awsr sts get-caller-identity --query Account --output text)"
echo "account $ACCOUNT, run $RUN, code $CODE"

echo "== bucket"
BUCKET="$RUN-$ACCOUNT"
awsr s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$REGION" > /dev/null
awsr s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "== bundle"
gzip -1 "$WORK/bundle.tar"
awsr s3 cp "$WORK/bundle.tar.gz" "s3://$BUCKET/bundle.tgz" --only-show-errors
URL="$(awsr s3 presign "s3://$BUCKET/bundle.tgz" --expires-in 14400)"

echo "== role"
ROLE="$RUN"
awsr iam create-role --role-name "$ROLE" --query Role.Arn --output text --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
awsr iam put-role-policy --role-name "$ROLE" --policy-name results --policy-document \
  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"s3:PutObject\",\"Resource\":[\"arn:aws:s3:::$BUCKET/results/*\",\"arn:aws:s3:::$BUCKET/logs/*\",\"arn:aws:s3:::$BUCKET/done/*\"]}]}"
awsr iam create-instance-profile --instance-profile-name "$ROLE" > /dev/null
awsr iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
awsr iam wait instance-profile-exists --instance-profile-name "$ROLE"

echo "== instances"
X86_AMI="$(awsr ssm get-parameter --name "$UBUNTU_X86" --query Parameter.Value --output text)"
SUBNETS="$(awsr ec2 describe-subnets --filters Name=default-for-az,Values=true --query 'Subnets[].SubnetId' --output text)"
[ -n "$SUBNETS" ] || die "no default subnets in $REGION"

launch() {  # host: starts its instance in the first default subnet that has the type; prints its id
  local host="$1" ami root ud id="" try subnet
  if [ "$host" = c8g ]; then ami="$COOL_AMI"; else ami="$X86_AMI"; fi
  root="$(awsr ec2 describe-images --image-ids "$ami" --query 'Images[0].RootDeviceName' --output text)"
  ud="$WORK/user-data-$host.sh"
  cat > "$ud" <<UD
#!/bin/bash
shutdown -h +$(cap "$host")
export BENCH_BUCKET='$BUCKET' BENCH_HOST='$host' BENCH_SETUPS='$(setups "$host")' BENCH_CODE='$CODE'
export BENCH_ENGINE='$ENGINE' BENCH_INSTANCE='$(itype "$host")' BENCH_CAMERAS='$CAMERAS' BENCH_SECONDS='$SECONDS_MEASURED'
export BENCH_MINUTES='$BENCH_MINUTES' COOL_VERSION='$COOL_VERSION' AWS_DEFAULT_REGION='$REGION'
mkdir -p /opt/bench/src
curl -fsS --retry 5 -o /opt/bench/bundle.tgz '$URL'
tar -xzf /opt/bench/bundle.tgz -C /opt/bench/src
bash /opt/bench/src/eval/bench_aws.sh --on-instance
shutdown -h now
UD
  for try in 1 2 3 4 5 6; do  # a new instance profile takes a few seconds to reach EC2
    for subnet in $SUBNETS; do
      id="$(awsr ec2 run-instances --image-id "$ami" --instance-type "$(itype "$host")" --subnet-id "$subnet" \
        --iam-instance-profile "Name=$ROLE" --user-data "file://$ud" --instance-initiated-shutdown-behavior terminate \
        --metadata-options HttpTokens=required,HttpPutResponseHopLimit=1,HttpEndpoint=enabled \
        --block-device-mappings "DeviceName=$root,Ebs={VolumeSize=$VOLUME_GB,VolumeType=gp3,DeleteOnTermination=true}" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$RUN-$host},{Key=$SLUG-bench,Value=$RUN}]" \
        --count 1 --query 'Instances[0].InstanceId' --output text 2> "$WORK/launch.err")" && break 2
      id=""
      grep -q 'Invalid IAM Instance Profile' "$WORK/launch.err" && break
      grep -qE 'Unsupported|InsufficientInstanceCapacity' "$WORK/launch.err" || { cat "$WORK/launch.err" >&2; return 1; }
    done
    grep -q 'Invalid IAM Instance Profile' "$WORK/launch.err" || break
    sleep 10
  done
  [ -n "$id" ] || { cat "$WORK/launch.err" >&2; return 1; }
  printf '%s' "$id"
}

START=$(date +%s)
for h in $HOSTS; do
  id="$(launch "$h")" || die "could not start $(itype "$h"); for the COOL AMI, subscribe on AWS Marketplace first"
  INSTANCES="$INSTANCES $id"
  INSTANCES="${INSTANCES# }"
  printf -v "ID_$h" '%s' "$id"
  echo "$(itype "$h") $id: setup, then $(setups "$h")"
done

echo "== waiting (each instance uploads its results and stops by itself)"
waiting="$HOSTS"
while [ -n "$waiting" ]; do
  sleep "$POLL_S"
  done_keys="$(awsr s3api list-objects-v2 --bucket "$BUCKET" --prefix done/ --query 'Contents[].Key' --output text)"
  left=""
  for h in $waiting; do
    ref="ID_$h"
    if printf '%s\n' $done_keys | grep -qx "done/$h"; then
      echo "$(itype "$h") finished after $(( ($(date +%s) - START) / 60 )) minutes"
      continue
    fi
    state="$(awsr ec2 describe-instances --instance-ids "${!ref}" --query 'Reservations[0].Instances[0].State.Name' --output text)"
    if [ "$state" != pending ] && [ "$state" != running ]; then
      echo "$(itype "$h") is $state without results; the end of its console:"
      awsr ec2 get-console-output --instance-id "${!ref}" --latest --query Output --output text | tail -n 40
      continue
    fi
    if [ $(( $(date +%s) - START )) -gt $(( ($(cap "$h") + 5) * 60 )) ]; then
      echo "$(itype "$h") passed its cap without results"
      continue
    fi
    left="$left $h"
  done
  waiting="${left# }"
done

echo "== results"
awsr s3 cp "s3://$BUCKET/logs/" "$LOGS/" --recursive --only-show-errors || true
awsr s3 cp "s3://$BUCKET/results/" "$WORK/results/" --recursive --only-show-errors || true
got=0
mkdir -p "$RESULTS"
for f in "$WORK"/results/bench-*.json; do
  [ -f "$f" ] || continue
  cp "$f" "$RESULTS/"
  echo "$RESULTS/$(basename "$f")"
  got=$((got + 1))
done
echo "logs: $LOGS"
[ "$got" -gt 0 ] || die "no results came back; see the logs and the console output above"
echo "next: python3 docs/fill.py, then commit eval/results/bench-*.json"
