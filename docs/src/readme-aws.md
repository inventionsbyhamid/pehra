## AWS

Pehra runs on AWS at {{link.aws}}: one CloudFormation stack in Mumbai (`ap-south-1`) around one Graviton4
instance, deployed with one command, `deploy/deploy.sh`. The main demo at {{link.demo}} runs the same code on our own
server.

| Service | What it does for Pehra |
| --- | --- |
| EC2 {{aws.instance_type}} (Graviton4) | Runs everything: the web app, the camera processes and the job worker |
| S3 | Pictures, clips and a Litestream copy of the database |
| Bedrock | The AI check, writing watches and answering questions; the role may call one model only |
| SSM Parameter Store | Settings and secrets |
| CloudWatch | Logs, metrics, a dashboard and alarms |
| SNS | Alarm emails |
| CloudFormation | The whole stack, with a budget alarm |

The deploy checks itself before it finishes: OpenCV {{aws.opencv}} on arm64, KleidiCV {{aws.kleidicv}}, H.264 and
H.265 decoding, memory {{aws.memory_mb}} of {{aws.memory_budget_mb}}, and the app's health check over HTTPS. The
results are in [eval/results/aws.json](eval/results/aws.json).

**COOL.** The same template runs on the COOL image for Graviton: put its image id in `COOL_AMI_ID`, and the deploy
then refuses to finish unless COOL's OpenCV is the one loaded. Our deploy runs standard OpenCV 5 from
`requirements.lock` instead, because our AWS India account can't subscribe to Marketplace products yet (AWS
Marketplace doesn't take saved cards or UPI from AWS India accounts). For the same reason its AI is Amazon Nova 2 Lite
on Bedrock; with Marketplace access it is Claude Haiku 5.5. Bedrock on our new account was still in AWS's verification
when we submitted; until it clears, moments there are filed as not checked by the AI.

To deploy your own:

```
cp deploy/params.env.example deploy/params.env && chmod 600 deploy/params.env   # alert email, domain, COOL image id or blank
deploy/deploy.sh --dry-run
deploy/deploy.sh
```

Point your domain at the address it prints (DNS only); Caddy gets the certificate within a minute.
