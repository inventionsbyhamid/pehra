## AWS

We built the production deployment for AWS in Mumbai (`ap-south-1`): one CloudFormation stack around one Graviton
instance. It has not run on AWS yet.

| Service | What it does for Pehra |
| --- | --- |
| EC2 c8g (Graviton4), from the COOL image | Runs everything, with OpenCV 5 built for Graviton |
| S3 | Pictures, clips and a Litestream copy of the database |
| Bedrock | Claude Haiku 5.5, for the AI check, writing watches and answering questions |
| SSM Parameter Store | Settings and secrets |
| CloudWatch | Logs, metrics, a dashboard and alarms |
| SNS | Alarm emails |
| CloudFormation | The whole stack, with a budget alarm |

To deploy, subscribe to COOL for Graviton4 in AWS Marketplace, turn on Claude Haiku 5.5 in Bedrock (the account
needs the Paid plan for both), then:

```
cp deploy/params.env.example deploy/params.env && chmod 600 deploy/params.env   # COOL image id, alert email, keys
deploy/deploy.sh --dry-run
deploy/deploy.sh
```

Point your domain at the address it prints. The deploy refuses to finish unless OpenCV from COOL is the one running.
