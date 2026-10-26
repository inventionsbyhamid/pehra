#!/usr/bin/env bash
# One command AWS deploy: the CloudFormation stack, app settings and secrets into SSM, the committed code as a
# release in S3, then the instance set up through SSM Run Command (no SSH). Run it again to ship an update.
#   deploy/deploy.sh                  deploy, reading deploy/params.env
#   deploy/deploy.sh --dry-run        print every AWS command instead of running it
#   deploy/deploy.sh --run 'command'  run one shell command on the instance as root and print its output
#   deploy/deploy.sh --params FILE    read another params file
# The stack, AWS profile and domain default to names from brand.json. A real deploy ends by writing
# eval/results/aws.json (AWS_RESULTS to change it) from the stack's outputs and the instance's checks.
# A blank COOL_AMI_ID is the stock mode: Amazon Linux 2023 arm64 with stock OpenCV from PyPI, not COOL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PARAMS="$ROOT/deploy/params.env"
DRY=0
RUN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --params) PARAMS="$2"; shift ;;
    --run) RUN="$2"; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "deploy: $*" >&2; exit 1; }
say() { printf '\n== %s\n' "$*"; }
exec 3>&1  # dry run lines go here even from inside $(...)
show() { printf '+' >&3; printf ' %q' "$@" >&3; printf '\n' >&3; }

[ -f "$PARAMS" ] || die "copy deploy/params.env.example to deploy/params.env and fill it in"
command -v python3 > /dev/null || die "needs python3 on PATH"

# names that configure the deploy itself; every other name in the params file is an app setting and goes to SSM
DEPLOY_NAMES=" AWS_PROFILE REGION STACK DOMAIN COOL_AMI_ID INSTANCE_TYPE VOLUME_GB VPC_ID SUBNET_ID ALERT_EMAIL \
 MONTHLY_BUDGET_USD BEDROCK_MODEL_ID APP_LOG_RETENTION_DAYS ACCESS_LOG_RETENTION_DAYS MAX_LOCK_DAYS "
GENERATED=" SESSION_SECRET DATA_KEY TELEGRAM_WEBHOOK_SECRET "  # made once when blank, then kept
# names remote_setup.sh sets on the instance, and TRUST_CLOUDFLARE (never for AWS): SSM wins over the instance's file,
# so one of these in params.env would silently override the stack. Refused, as is any AWS_ name but AWS_PROFILE.
STACK_DECIDED=" SSM_PREFIX STORAGE S3_BUCKET METRICS RUN_JOBS_IN_APP BEDROCK_MODEL PUBLIC_BASE_URL DATA_DIR \
 DATABASE_URL MODELS_DIR PYTHONPATH PYTHONUNBUFFERED LD_LIBRARY_PATH OPENCV_FFMPEG_CAPTURE_OPTIONS TRUST_CLOUDFLARE "

APP_NAMES=""
n=0
while IFS= read -r line || [ -n "$line" ]; do
  n=$((n + 1))
  line="${line%$'\r'}"
  case "$line" in ''|'#'*) continue ;; esac
  name="${line%%=*}"
  value="${line#*=}"
  if [ "$name" = "$line" ] || ! printf '%s' "$name" | grep -Eq '^[A-Z][A-Z0-9_]*$'; then
    die "$PARAMS line $n: expected NAME=value"
  fi
  case "$value" in
    \"*\") value="${value#\"}"; value="${value%\"}" ;;
    \'*\') value="${value#\'}"; value="${value%\'}" ;;
  esac
  case "$STACK_DECIDED" in *" $name "*) die "$PARAMS line $n: $name is set by the stack, remove it" ;; esac
  case "$name" in AWS_PROFILE) ;; AWS_*) die "$PARAMS line $n: $name is set by the stack, remove it" ;; esac
  printf -v "P_$name" '%s' "$value"
  case "$DEPLOY_NAMES" in *" $name "*) ;; *) APP_NAMES="$APP_NAMES $name" ;; esac
done < "$PARAMS"
param() { local ref="P_$1"; printf '%s' "${!ref:-${2:-}}"; }

# the product's name and domain; the stack (and with it the SSM path, log groups, alarms) is the name in lower case
brand() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$ROOT/brand.json" "$1"; }
BRAND="$(brand name)" || die "brand.json has no name"
SLUG="$(printf '%s' "$BRAND" | tr 'A-Z' 'a-z' | tr -cs 'a-z0-9-' '-' | sed 's/^-*//; s/-*$//')"

PROFILE="$(param AWS_PROFILE "$SLUG")"
REGION="$(param REGION ap-south-1)"
STACK="$(param STACK "$SLUG")"
printf '%s' "$STACK" | grep -Eq '^[a-z][a-z0-9-]{1,20}$' || die "stack name $STACK: lower case letters, digits and -"
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")
export AWS_PAGER=""

aws_do() {  # an AWS call made for its effect
  if [ "$DRY" = 1 ]; then show aws "$@" "${AWS_ARGS[@]}"; return 0; fi
  aws "$@" "${AWS_ARGS[@]}"
}
aws_get() {  # an AWS call whose output the script reads; a dry run prints the call and answers with $1
  local answer="$1"; shift
  if [ "$DRY" = 1 ]; then show aws "$@" "${AWS_ARGS[@]}"; printf '%s\n' "$answer"; return 0; fi
  aws "$@" "${AWS_ARGS[@]}"
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ "$DRY" = 0 ]; then
  for tool in aws curl; do command -v "$tool" > /dev/null || die "needs $tool on PATH"; done
fi

say "account"
ACCOUNT="$(aws_get 123456789012 sts get-caller-identity --query Account --output text)"
echo "account $ACCOUNT, region $REGION, stack $STACK"

stack_outputs() {
  local fake
  fake="$(printf 'InstanceId\ti-dryrun\nPublicIp\t203.0.113.10\nBucketName\t%s-bucket-dryrun\nAppLogGroup\t/%s/app\nAccessLogGroup\t/%s/access\nBedrockModel\tglobal.%s\nDashboardUrl\thttps://console.aws.amazon.com/cloudwatch\n' \
    "$STACK" "$STACK" "$STACK" "$(param BEDROCK_MODEL_ID anthropic.claude-haiku-5-5)")"
  OUTPUTS="$(aws_get "$fake" cloudformation describe-stacks --stack-name "$STACK" \
    --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output text)"
}
output() { printf '%s\n' "$OUTPUTS" | awk -F'\t' -v k="$1" '$1 == k { print $2 }'; }

wait_online() {
  local status i
  for i in $(seq 1 90); do
    status="$(aws_get Online ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE" \
      --query 'InstanceInformationList[0].PingStatus' --output text)"
    [ "$status" = Online ] && return 0
    [ "$i" = 1 ] && echo "waiting for $INSTANCE to register with SSM (a few minutes after its first boot)"
    sleep 10
  done
  die "$INSTANCE never came online in SSM; it needs outbound HTTPS and the SSM agent (the stack's user data starts it)"
}

remote() {  # runs a shell script on the instance as root through SSM Run Command, prints its output
  local script="$1" timeout_s="$2" params id status waited=0
  params="$(python3 -c 'import json, sys; print(json.dumps({"commands": sys.argv[1].splitlines(), "executionTimeout": [sys.argv[2]]}))' "$script" "$timeout_s")"
  id="$(aws_get cmd-dryrun ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
    --comment "$STACK deploy" --timeout-seconds 600 --parameters "$params" --query Command.CommandId --output text)"
  while :; do
    status="$(aws_get Success ssm get-command-invocation --command-id "$id" --instance-id "$INSTANCE" \
      --query Status --output text 2> /dev/null || echo Pending)"
    case "$status" in
      Pending|InProgress|Delayed)
        [ "$waited" -lt $((timeout_s + 600)) ] || die "command $id still $status after ${waited}s"
        sleep 10; waited=$((waited + 10)) ;;
      *) break ;;
    esac
  done
  aws_get "(the command's output)" ssm get-command-invocation --command-id "$id" --instance-id "$INSTANCE" \
    --query StandardOutputContent --output text
  if [ "$status" != Success ]; then
    aws_get "" ssm get-command-invocation --command-id "$id" --instance-id "$INSTANCE" \
      --query StandardErrorContent --output text >&2
    die "the command on the instance ended $status; the whole log is in /var/log/watchful/deploy.log and in CloudWatch"
  fi
}

if [ -n "$RUN" ]; then
  stack_outputs
  INSTANCE="$(output InstanceId)"
  [ -n "$INSTANCE" ] || die "no instance in stack $STACK; deploy first"
  wait_online
  remote "$RUN" 3600
  exit 0
fi

DOMAIN="$(param DOMAIN "$(brand domain)")"
AMI="$(param COOL_AMI_ID)"
EMAIL="$(param ALERT_EMAIL)"
[ -n "$DOMAIN" ] || die "set DOMAIN in $PARAMS"
[ -n "$EMAIL" ] || die "set ALERT_EMAIL in $PARAMS"

say "image"
if [ -n "$AMI" ]; then
  BUILD=cool
  echo "COOL: the instance launches from $AMI"
else
  BUILD=stock
  echo "stock OpenCV, not COOL: COOL_AMI_ID is blank, so the instance launches from Amazon Linux 2023 arm64"
  echo "(/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64) with opencv-python-headless from PyPI"
fi

say "network"
VPC="$(param VPC_ID)"
if [ -z "$VPC" ]; then
  VPC="$(aws_get vpc-dryrun ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
  [ -n "$VPC" ] && [ "$VPC" != None ] || die "no default VPC in $REGION; set VPC_ID and SUBNET_ID (a public subnet)"
fi
echo "vpc $VPC"

say "stack"
INSTANCE_TYPE="$(param INSTANCE_TYPE c8g.xlarge)"
aws_do cloudformation deploy --stack-name "$STACK" --template-file "$ROOT/deploy/cloudformation.yaml" \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset --tags "project=$STACK" \
  --parameter-overrides "Prefix=$STACK" "BrandName=$BRAND" "Domain=$DOMAIN" "CoolAmiId=$AMI" "VpcId=$VPC" \
  "SubnetId=$(param SUBNET_ID)" "InstanceType=$INSTANCE_TYPE" "VolumeGb=$(param VOLUME_GB 40)" \
  "AlertEmail=$EMAIL" "MonthlyBudgetUsd=$(param MONTHLY_BUDGET_USD 150)" \
  "BedrockModelId=$(param BEDROCK_MODEL_ID anthropic.claude-haiku-5-5)" "MaxLockDays=$(param MAX_LOCK_DAYS 100)" \
  "AppLogRetentionDays=$(param APP_LOG_RETENTION_DAYS 30)" "AccessLogRetentionDays=$(param ACCESS_LOG_RETENTION_DAYS 90)"
stack_outputs
INSTANCE="$(output InstanceId)"
IP="$(output PublicIp)"
BUCKET="$(output BucketName)"
echo "instance $INSTANCE, ip $IP, bucket $BUCKET"

say "settings and secrets in SSM under /$STACK/"
put_param() {  # name value overwrite; the value goes through a 600 file, never the command line
  local name="$1" value="$2" overwrite="$3" file="$WORK/value" err
  local args=(ssm put-parameter --name "/$STACK/$name" --type SecureString --query Version --output text)
  [ "$overwrite" = 1 ] && args+=(--overwrite)
  if [ "$DRY" = 1 ]; then show aws "${args[@]}" --value "file://<$name>" "${AWS_ARGS[@]}"; return 0; fi
  (umask 077; printf '%s' "$value" > "$file")
  if err="$(aws "${args[@]}" --value "file://$file" "${AWS_ARGS[@]}" 2>&1 > /dev/null)"; then
    echo "$name set"
  elif [ "$overwrite" = 0 ] && printf '%s' "$err" | grep -q ParameterAlreadyExists; then
    echo "$name kept"
  else
    rm -f "$file"; die "could not set $name: $err"
  fi
  rm -f "$file"
}
for name in $APP_NAMES; do
  value="$(param "$name")"
  if [ -n "$value" ]; then
    put_param "$name" "$value" 1
  else
    case "$GENERATED" in
      *" $name "*) put_param "$name" "$(python3 -c 'import secrets; print(secrets.token_hex(32))')" 0 ;;
      *) echo "$name blank, skipped" ;;
    esac
  fi
done

say "release"
if [ -n "${RELEASE_TARBALL:-}" ]; then  # a prebuilt tarball instead of `git archive HEAD` (the tests use it)
  TARBALL="$RELEASE_TARBALL"
  RELEASE="$(basename "$TARBALL" .tar.gz)"
elif [ "$DRY" = 1 ]; then
  RELEASE="$(git -C "$ROOT" rev-parse --short=12 HEAD 2> /dev/null || echo dryrun)"
  TARBALL="$WORK/$RELEASE.tar.gz"
  show git -C "$ROOT" archive --format=tar.gz -o "$TARBALL" HEAD
else
  command -v git > /dev/null || die "needs git"
  RELEASE="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
  git -C "$ROOT" diff --quiet HEAD -- || echo "note: uncommitted changes are not deployed; the release is commit $RELEASE"
  TARBALL="$WORK/$RELEASE.tar.gz"
  git -C "$ROOT" archive --format=tar.gz -o "$TARBALL" HEAD
fi
printf '%s' "$RELEASE" | grep -Eq '^[A-Za-z0-9._-]+$' || die "bad release name $RELEASE"
aws_do s3 cp "$TARBALL" "s3://$BUCKET/releases/$RELEASE.tar.gz" --only-show-errors
URL="$(aws_get "https://$BUCKET.s3.amazonaws.com/releases/$RELEASE.tar.gz?presigned" s3 presign \
  "s3://$BUCKET/releases/$RELEASE.tar.gz" --expires-in 3600)"
echo "release $RELEASE"

say "instance setup (first run about 15 minutes: packages, Python, models, web build)"
SETUP_ENV="STACK=$STACK DOMAIN=$DOMAIN REGION=$REGION BUCKET=$BUCKET APP_LOG_GROUP=$(output AppLogGroup)
ACCESS_LOG_GROUP=$(output AccessLogGroup) BEDROCK_MODEL=$(output BedrockModel) ACME_EMAIL=$EMAIL RELEASE=$RELEASE"
case "$SETUP_ENV$URL" in *"'"*) die "a deploy value contains a single quote" ;; esac
ENV_ARGS=""
for pair in $SETUP_ENV; do ENV_ARGS="$ENV_ARGS ${pair%%=*}='${pair#*=}'"; done
DIR="/opt/watchful/releases/$RELEASE"
SCRIPT="set -e
mkdir -p /opt/watchful/releases
curl -fsSL --retry 3 -o $DIR.tar.gz '$URL'
rm -rf $DIR.new && mkdir -p $DIR.new && tar -xzf $DIR.tar.gz -C $DIR.new && rm -f $DIR.tar.gz
rm -rf $DIR && mv $DIR.new $DIR
env$ENV_ARGS bash $DIR/deploy/remote_setup.sh"
wait_online
remote "$SCRIPT" 3600 | tee "$WORK/setup.out"

say "checks"
if [ "$DRY" = 1 ]; then
  show curl -fsS "https://$DOMAIN/healthz"
elif curl -fsS --max-time 10 "https://$DOMAIN/healthz"; then
  echo
else
  echo "https://$DOMAIN/healthz is not reachable yet: point an A record for $DOMAIN at $IP (DNS only), then Caddy"
  echo "gets the certificate within a minute"
fi
echo "dashboard $(output DashboardUrl)"
echo "a shell on the instance: aws ssm start-session --target $INSTANCE --profile $PROFILE --region $REGION"

say "results"
RESULTS="${AWS_RESULTS:-$ROOT/eval/results/aws.json}"
if [ "$DRY" = 1 ]; then
  echo "a real run writes $RESULTS from the stack outputs and the checks above (build $BUILD)"
else
  printf '%s\n' "$OUTPUTS" > "$WORK/outputs.tsv"
  python3 "$ROOT/deploy/aws_results.py" --out "$RESULTS" --region "$REGION" --stack "$STACK" \
    --instance-type "$INSTANCE_TYPE" --domain "$DOMAIN" --release "$RELEASE" \
    --outputs "$WORK/outputs.tsv" --setup "$WORK/setup.out" --build "$BUILD"
  echo "wrote $RESULTS"
fi
