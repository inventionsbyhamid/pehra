"""Before the SQLAlchemy schema, Watchful kept JSON documents in a plain SQLite file. Such a file's cameras, watches,
alerts, events, people and labels become the `default` society's: they are imported into a new file beside it, which
takes the old file's place only when the whole import worked; the old file is kept, never deleted.

One process imports at a time (a lock file beside the database); the others wait and then find the new file. The old
file gets its new name first (a hard link, or a copy), then one atomic rename puts the new file at the path, so a
crash at any point leaves a whole database there: the old one, imported again on the next start, or the new one."""
import fcntl
import json
import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
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


def _remove(path: Path) -> None:
    for side, _ in _files(path):
        side.unlink(missing_ok=True)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    with open(path.with_name(f"{path.name}.import-lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _checkpoint(path: Path, *, single_file: bool = False) -> None:
    """Every write in the main file, so the file alone is the whole database. The old file keeps its journal mode;
    the new one (single_file) leaves WAL mode too, so no -wal has to travel with it, until make_engine's next
    connection puts it back."""
    con = sqlite3.connect(path)
    try:
        con.execute("pragma wal_checkpoint(truncate)")
        if single_file:
            con.execute("pragma journal_mode=delete")
    finally:
        con.close()


def _keep(path: Path, old: Path) -> None:
    try:
        os.link(path, old)
    except OSError:  # a file system without hard links
        shutil.copy2(path, old)


def adopt_legacy(url: str, keys: list[str] | None) -> Path | None:
    """Imports a legacy file at this SQLite URL into the new schema; returns where the old file is kept."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or parsed.database in (None, "", ":memory:"):
        return None
    path = Path(parsed.database)
    if not path.is_file():
        return None
    with _locked(path):  # is_legacy too: it must not open the file while another process swaps it
        if not is_legacy(path):  # a new database, or another process imported it while this one waited
            return None
        _checkpoint(path)
        tmp = path.with_name(f"{path.stem}.importing{path.suffix}")
        _remove(tmp)  # a partial import from an earlier start
        try:
            counts = import_legacy(path, f"sqlite:///{tmp}", keys)
            _checkpoint(tmp, single_file=True)
        except BaseException:
            _remove(tmp)
            raise
        old = path.with_name(f"{path.stem}.legacy-{int(time.time())}{path.suffix}")
        if not old.exists():  # an attempt this second that stopped before the rename kept it already
            _keep(path, old)
        for side, _ in _files(path)[1:]:  # the old file's empty -wal and -shm, before the new file arrives
            side.unlink(missing_ok=True)
        tmp.replace(path)
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
