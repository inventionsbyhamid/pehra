"""Settings from AWS Systems Manager Parameter Store, then the service's command. On AWS the app's secrets (the
session secret, Telegram's bot token and webhook secret) and the owner's setting choices live as SecureString
parameters under SSM_PREFIX, which deploy.sh writes from deploy/params.env: the parameter /<stack>/<NAME> becomes the
environment variable NAME, winning over the unit's environment file, which holds only what the stack decides. The
systemd units start the app and the job worker through it, so the camera processes inherit the same settings:

    python deploy/ssm_env.py -- python -m uvicorn ...

Without SSM_PREFIX it only runs the command. Names are printed, values never; if SSM cannot be read the service
does not start (systemd tries again)."""
import os
import re
import sys

NAME = re.compile(r"[A-Z][A-Z0-9_]*")


def load(prefix: str, region: str | None = None, environ=None, client=None) -> list[str]:
    """Copies every parameter directly under `prefix` into `environ` (os.environ by default), decrypted; returns
    the names it set. Names that are not upper case setting names are skipped."""
    environ = os.environ if environ is None else environ
    if client is None:
        import boto3
        client = boto3.client("ssm", region_name=region)
    loaded = []
    for page in client.get_paginator("get_parameters_by_path").paginate(
            Path="/" + prefix.strip("/"), Recursive=False, WithDecryption=True):
        for parameter in page["Parameters"]:
            name = parameter["Name"].rsplit("/", 1)[-1]
            if NAME.fullmatch(name):
                environ[name] = parameter["Value"]
                loaded.append(name)
    return sorted(loaded)


def main(argv: list[str] | None = None, environ=None, execvpe=os.execvpe, client=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--" not in argv or argv.index("--") == len(argv) - 1:
        print("usage: python deploy/ssm_env.py -- command [arguments]", file=sys.stderr)
        return 2
    command = argv[argv.index("--") + 1:]
    environ = os.environ if environ is None else environ
    if prefix := environ.get("SSM_PREFIX"):
        names = load(prefix, environ.get("AWS_REGION") or environ.get("AWS_DEFAULT_REGION"), environ, client)
        print(f"ssm: {len(names)} settings from {prefix}: {', '.join(names)}", file=sys.stderr, flush=True)
    execvpe(command[0], command, environ)
    return 0


if __name__ == "__main__":
    sys.exit(main())
