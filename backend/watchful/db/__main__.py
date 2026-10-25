"""Database commands for the operator.

    python -m watchful.db rotate-keys

rotate-keys seals every camera address of every society (and any other sealed value, store/sql.py SEALED) again with
DATA_KEY, opening each with DATA_KEY or a DATA_KEY_OLD key. To rotate: move the key to DATA_KEY_OLD, set a new
DATA_KEY, restart the app and its workers, run this; once it reports nothing unreadable, remove DATA_KEY_OLD and
restart again."""
import argparse


def main(argv: list[str] | None = None, settings=None) -> None:
    from watchful.components import make_engine
    from watchful.config import get_settings
    from watchful.store.sql import rotate_keys

    args = argparse.ArgumentParser(prog="python -m watchful.db")
    sub = args.add_subparsers(dest="command", required=True)
    sub.add_parser("rotate-keys", help="seal every stored secret again with DATA_KEY")
    args.parse_args(argv)
    settings = settings or get_settings()
    counts = rotate_keys(make_engine(settings), [settings.data_key, *settings.data_key_old])
    print(f"sealed again with DATA_KEY: {counts['rotated']}, already sealed with it: {counts['current']}, "
          f"unreadable: {counts['unreadable']}")
    if counts["unreadable"]:
        args.exit(1, f"{counts['unreadable']} sealed values open with none of these keys: put the key that sealed "
                     "them in DATA_KEY_OLD and run this again\n")


if __name__ == "__main__":
    main()
