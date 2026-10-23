#!/usr/bin/env bash
# One command deploy to AWS Graviton: stack, code, web build, secrets, service, HTTPS.
# usage: deploy/deploy.sh            (reads deploy/params.env)
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/params.env
export AWS_PROFILE AWS_REGION="$REGION"

AMI="${AMI_ID:-$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)}"
aws cloudformation deploy --stack-name "$STACK" --template-file deploy/cloudformation.yaml \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset --parameter-overrides \
  AmiId="$AMI" InstanceType="$INSTANCE_TYPE" KeyName="$KEY_NAME" SshCidr="$SSH_CIDR" \
  CaregiverEmail="$CAREGIVER_EMAIL" BudgetEmail="$BUDGET_EMAIL" Prefix="$STACK"

out() { aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
IP=$(out PublicIp); BUCKET=$(out BucketName); TOPIC=$(out TopicArn)
echo "instance $IP, bucket $BUCKET"
SSH="ssh -o StrictHostKeyChecking=accept-new ubuntu@$IP"

until $SSH true 2>/dev/null; do sleep 5; done
rsync -az --delete --exclude .git --exclude node_modules --exclude /data --exclude 'web/dist' \
  --exclude 'models/*.onnx' --exclude 'models/*/' --exclude '.env*' --exclude __pycache__ --exclude .superpowers \
  --exclude deploy/params.env ./ "ubuntu@$IP:watchful/"

# secrets go straight into a root only file on the instance, never into git or the terminal
KEY=$(grep -h '^OPENROUTER_API_KEY=' "${OPENROUTER_KEY_FILE/#\~/$HOME}" | cut -d= -f2-)
{
  echo "STORAGE=s3"; echo "DB=dynamodb"; echo "METRICS=cloudwatch"; echo "NOTIFY=sns"
  echo "AWS_REGION=$REGION"; echo "S3_BUCKET=$BUCKET"; echo "SNS_TOPIC_ARN=$TOPIC"; echo "DDB_TABLE_PREFIX=$STACK"
  echo "DATA_DIR=/var/lib/watchful"; echo "MODELS_DIR=/home/ubuntu/watchful/models"; echo "AI_PROVIDER=$AI_PROVIDER"
  echo "OPENROUTER_API_KEY=$KEY"
  echo "DEMO_PASSWORD=$(openssl rand -hex 6)"; echo "SESSION_SECRET=$(openssl rand -hex 32)"
} | $SSH 'sudo test -f /etc/watchful.env && cat > /dev/null || (sudo install -m 600 /dev/stdin /etc/watchful.env)'

$SSH "DOMAIN=$DOMAIN bash watchful/deploy/remote_setup.sh"
echo "open https://$DOMAIN/healthz once DNS points $DOMAIN at $IP"
