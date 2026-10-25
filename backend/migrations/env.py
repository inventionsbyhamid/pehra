"""The app passes its own connection in (watchful.db.session.migrate); the alembic command line opens
DATABASE_URL from the settings."""
from alembic import context

from watchful.db.models import Base, UTCDateTime

config = context.config


def render_item(type_, obj, autogen_context):
    """Revisions spell UTC times as DateTime(timezone=True), so they never import app types."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run(connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True,
                      render_item=render_item, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    from logging.config import fileConfig

    from watchful.config import get_settings
    from watchful.db.session import make_engine, migration_connection
    if config.config_file_name:
        fileConfig(config.config_file_name)
    with migration_connection(make_engine(get_settings().database)) as connection:
        run(connection)
