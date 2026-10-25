"""Engines and migrations. SQLite runs in WAL mode with foreign keys on. The schema always comes from Alembic
(`upgrade head`), never create_all, so each later change is a new revision in backend/migrations/versions."""
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
SQLITE_PRAGMAS = ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON", "busy_timeout=30000")


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return create_engine(url, pool_pre_ping=True)
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
    if parsed.database in (None, "", ":memory:"):
        kwargs["poolclass"] = StaticPool  # one shared connection, or each would see its own empty database
    else:
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def _sqlite_pragmas(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    for pragma in SQLITE_PRAGMAS:
        cursor.execute(f"PRAGMA {pragma}")
    cursor.close()


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


@contextmanager
def migration_connection(engine: Engine) -> Iterator[Connection]:
    """A connection in one transaction for schema changes. On SQLite foreign keys are off meanwhile, because a
    batch table rebuild drops the old table and that would cascade into child rows; they are checked before the
    commit and switched on again before the connection goes back to the pool."""
    with engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")  # has no effect inside a transaction
            connection.commit()
        try:
            with connection.begin():
                yield connection
                if sqlite and (broken := connection.exec_driver_sql("PRAGMA foreign_key_check").all()):
                    raise RuntimeError(f"the migration left rows with broken foreign keys: {broken[:5]}")
        finally:
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()


def migrate(engine: Engine, revision: str = "head") -> None:
    config = alembic_config()
    with migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
