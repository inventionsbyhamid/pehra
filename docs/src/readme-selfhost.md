## Self hosting

Pehra runs on any 64 bit Linux machine with Docker, in the society's office or in a cloud. One container holds the
whole app and one volume holds its data. The image is {{build.selfhost.image_mb}}, most of it models. For a sense of
memory and CPU: our old dev box (a 2013 Xeon) ran {{bench.devbox_no_text.cameras}} cameras on
{{bench.devbox_no_text.vcpus}} CPUs in {{bench.devbox_no_text.memory_total}}, analysing
{{bench.devbox_no_text.fps_per_camera}} per camera.

```
git clone {{link.repo}}.git
cd pehra
cp .env.example .env        # then fill in the settings below
docker compose up -d --build
```

The first build downloads every model and checks it by SHA 256. These are the settings you set in `.env`; every other
one is explained in `.env.example`.

| Setting | What to put there |
| --- | --- |
| `SESSION_SECRET`, `DATA_KEY` | Two random strings: `python3 -c "import secrets; print(secrets.token_hex(32))"`, once each. Keep `DATA_KEY` safe: camera addresses sealed with a lost key cannot be read. |
| `PUBLIC_BASE_URL` | The https address people will open. |
| `OPENROUTER_API_KEY` | For the AI. Or `AI_PROVIDER=bedrock` with `BEDROCK_MODEL` and AWS credentials. Without either, critical moments still alert, marked unverified. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | Your bot's token from BotFather, and one more random string. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional Google sign in. |
| `ALLOW_PRIVATE_STREAMS` | `true` only when the machine sits inside the society's network. |

The app listens on port 8000 of the machine itself. For https, any reverse proxy or a Cloudflare Tunnel works.

Sign in the first time with a link printed on the server. It creates the society and makes you its owner:

```
docker compose exec pehra python -m watchful.auth signin-link you@example.com --workspace "Green Park"
```

Then add cameras from the control room. To connect Telegram, open Settings, Telegram, Connect a group, add your bot
to the group and send it the `/connect` code shown there.

Everything Pehra keeps (the database, pictures and clips) is in the `pehra-data` volume. To back it up, and to
upgrade (the database migrates itself on start):

```
docker compose stop pehra
docker compose run --rm --no-deps --user root -v "$PWD:/backup" pehra tar czf /backup/pehra-data.tgz -C /app/data .
docker compose start pehra

git pull && docker compose up -d --build
```
