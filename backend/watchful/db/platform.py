"""Access to the platform tables (users, societies, members, invites, sign in links, audit) for the account routes.
A society's own data goes through store.sql.SqlRepository, which is bound to that society."""
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, orm

from watchful.db import models as m


class Database:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._sessions = orm.sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def tx(self) -> Iterator[orm.Session]:
        with self._sessions.begin() as s:
            yield s

    def add(self, *rows):
        """Inserts new rows; returns the row, or the rows when given several."""
        with self.tx() as s:
            s.add_all(rows)
        return rows[0] if len(rows) == 1 else list(rows)

    def audit(self, workspace_id: str | None, actor: str, action: str, data: dict | None = None) -> None:
        self.add(m.Audit(workspace_id=workspace_id, actor=actor, action=action, data=data or {}))
