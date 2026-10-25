"""Before the SQLAlchemy schema, Watchful kept JSON documents in a plain SQLite file. Such a file's cameras, watches,
alerts, events, people and labels become the `default` society's: they are imported into a new file beside it, which
takes the old file's place only when the whole import worked; the old file is kept, never deleted."""
import json
import logging
import sqlite3
import time
from pathlib import Path

from sqlalchemy.engine import make_url

log = logging.getLogger("watchful.db")
BATCH = 500


def is_legacy(path: Path) -> bool:
    if not path.is_file():
        return False
    con = sqlite3.connect(path)
    try:
        tables = {r[0] for r in con.execute("select name from sqlite_master where type = 'table'")}
        if "alembic_version" in tables or "cameras" not in tables:
            return False
        return {r[1] for r in con.execute("pragma table_info(cameras)")} == {"id", "data"}
    finally:
        con.close()


def _files(path: Path) -> list[tuple[Path, str]]:
    return [(Path(f"{path}{suffix}"), suffix) for suffix in ("", "-wal", "-shm")]


def _move(src: Path, dst: Path) -> None:
    for side, suffix in _files(src):
        if side.exists():
            side.replace(Path(f"{dst}{suffix}"))


def _remove(path: Path) -> None:
    for side, _ in _files(path):
        side.unlink(missing_ok=True)


def adopt_legacy(url: str, keys: list[str] | None) -> Path | None:
    """Imports a legacy file at this SQLite URL into the new schema; returns where the old file now is."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or parsed.database in (None, "", ":memory:"):
        return None
    path = Path(parsed.database)
    if not is_legacy(path):
        return None
    con = sqlite3.connect(path)
    con.execute("pragma wal_checkpoint(truncate)")
    con.close()
    tmp = path.with_name(f"{path.stem}.importing{path.suffix}")
    _remove(tmp)  # a partial import from an earlier start
    try:
        counts = import_legacy(path, f"sqlite:///{tmp}", keys)
    except BaseException:
        _remove(tmp)
        raise
    old = path.with_name(f"{path.stem}.legacy-{int(time.time())}{path.suffix}")
    _move(path, old)
    _move(tmp, path)
    log.warning("imported the earlier database into the default society (%s); the old file is %s", counts, old)
    return old


def import_legacy(old: Path, url: str, keys: list[str] | None) -> dict[str, int]:
    """Copies a legacy file's rows into the database at `url` (made and migrated here); the old file is only read."""
    from watchful.db.session import make_engine, migrate
    from watchful.store.db import DEFAULT_SOCIETY_NAME, DEFAULT_WORKSPACE, AlertRow, Camera, EventRow, WatchRow
    from watchful.store.sql import SqlRepository, ensure_workspace
    from watchful.vision.faces import Enrollment
    from watchful.vision.zones import Zone

    engine = make_engine(url)
    migrate(engine)
    ensure_workspace(engine, DEFAULT_WORKSPACE, DEFAULT_SOCIETY_NAME)
    repo = SqlRepository(engine, DEFAULT_WORKSPACE, secret=keys)
    counts = dict.fromkeys(("cameras", "watches", "alerts", "events", "enrollments", "person_labels", "meta"), 0)
    src = sqlite3.connect(old)  # only read
    tables = {r[0] for r in src.execute("select name from sqlite_master where type = 'table'")}

    def docs(table: str):
        if table in tables:
            for (data,) in src.execute(f"select data from {table} order by rowid"):
                yield json.loads(data)

    try:
        for d in docs("cameras"):
            d["zones"] = [Zone(z["id"], z["name"], [tuple(p) for p in z["polygon"]]) for z in d.get("zones", [])]
            repo.add_camera(Camera(**d))
            counts["cameras"] += 1
        for d in docs("watches"):
            repo.add_watch(WatchRow(**d))
            counts["watches"] += 1
        for d in docs("alerts"):
            repo.add_alert(AlertRow(**d))
            counts["alerts"] += 1
        batch = []
        for d in docs("events"):
            batch.append(EventRow(**d))
            if len(batch) >= BATCH:
                repo.add_events(batch)
                counts["events"] += len(batch)
                batch = []
        repo.add_events(batch)
        counts["events"] += len(batch)
        for d in docs("enrollments"):
            repo.add_enrollment(Enrollment(**d))
            counts["enrollments"] += 1
        if "person_labels" in tables:
            for camera_id, group_id, label in src.execute("select camera_id, group_id, label from person_labels"):
                repo.set_person_label(camera_id, group_id, label)
                counts["person_labels"] += 1
        if "meta" in tables:
            for key, value in src.execute("select key, value from meta"):
                repo.set_meta(key, value)
                counts["meta"] += 1
    finally:
        src.close()
        engine.dispose()
    return counts
