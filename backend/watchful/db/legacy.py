"""Before the SQLAlchemy schema, Watchful kept JSON documents in a plain SQLite file. Such a file is moved aside, never
deleted, and its cameras, watches, alerts, events, people and labels become the default society's."""
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


def adopt_legacy(url: str, secret: str | None) -> Path | None:
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
    old = path.with_name(f"{path.stem}.legacy-{int(time.time())}{path.suffix}")
    path.rename(old)
    for suffix in ("-wal", "-shm"):
        side = Path(f"{path}{suffix}")
        if side.exists():
            side.rename(Path(f"{old}{suffix}"))
    _import(url, old, secret)
    log.warning("moved the earlier database to %s and imported it into the default society", old)
    return old


def _import(url: str, old: Path, secret: str | None) -> None:
    from watchful.db.session import make_engine, migrate
    from watchful.store.db import DEFAULT_WORKSPACE, AlertRow, Camera, EventRow, WatchRow
    from watchful.store.sql import SqlRepository, ensure_workspace
    from watchful.vision.faces import Enrollment
    from watchful.vision.zones import Zone

    engine = make_engine(url)
    migrate(engine)
    ensure_workspace(engine, DEFAULT_WORKSPACE)
    repo = SqlRepository(engine, DEFAULT_WORKSPACE, secret=secret)
    src = sqlite3.connect(old)
    tables = {r[0] for r in src.execute("select name from sqlite_master where type = 'table'")}

    def docs(table: str):
        if table in tables:
            for (data,) in src.execute(f"select data from {table} order by rowid"):
                yield json.loads(data)

    try:
        for d in docs("cameras"):
            d["zones"] = [Zone(z["id"], z["name"], [tuple(p) for p in z["polygon"]]) for z in d.get("zones", [])]
            repo.add_camera(Camera(**d))
        for d in docs("watches"):
            repo.add_watch(WatchRow(**d))
        for d in docs("alerts"):
            repo.add_alert(AlertRow(**d))
        batch = []
        for d in docs("events"):
            batch.append(EventRow(**d))
            if len(batch) >= BATCH:
                repo.add_events(batch)
                batch = []
        repo.add_events(batch)
        for d in docs("enrollments"):
            repo.add_enrollment(Enrollment(**d))
        if "person_labels" in tables:
            for camera_id, group_id, label in src.execute("select camera_id, group_id, label from person_labels"):
                repo.set_person_label(camera_id, group_id, label)
        if "meta" in tables:
            for key, value in src.execute("select key, value from meta"):
                repo.set_meta(key, value)
    finally:
        src.close()
        engine.dispose()
