"""Blob keys from before societies (`cam_x/kf/...`, `people/per_x.jpg`) moved under their society,
`{workspace_id}/...`, so the blob route can serve a key by its prefix alone. Runs once per society when the app
starts (Meta blob_keys); each blob is copied, then the rows point at the copy, then the old blob goes, so a stop
midway leaves every row readable and a rerun finishes the job."""
import logging
import mimetypes

from sqlalchemy import select, update

from watchful.db import models as m

log = logging.getLogger("watchful.rekey")
DONE = "society"  # Meta blob_keys once a society's keys all carry its prefix


def key_columns() -> list[tuple[type, str]]:
    """Every (model, column) holding a blob key in a society's rows: columns named *_key on models with a
    workspace_id."""
    out = []
    for mapper in m.Base.registry.mappers:
        cls = mapper.class_
        if "workspace_id" not in mapper.columns:
            continue
        out += [(cls, name) for name in mapper.columns.keys() if name.endswith("_key")]
    return out


def _crop_key(w) -> str | None:
    same_as = ((w.spec_json or {}).get("subject") or {}).get("same_as") or {}
    return same_as.get("crop_key") if isinstance(same_as, dict) else None


def rekey_society(repo, blobs) -> int:
    """Moves one society's keys under its prefix; the number of blobs moved."""
    if repo.get_meta("blob_keys") == DONE:
        return 0
    ws, prefix = repo.workspace_id, f"{repo.workspace_id}/"
    old: set[str] = set()
    with repo.tx() as s:
        for model, column in key_columns():
            col = getattr(model, column)
            old.update(k for k in s.scalars(select(col).where(model.workspace_id == ws, col.is_not(None),
                                                              col.not_like(f"{prefix}%"))) if k)
        old.update(k for k in (_crop_key(w) for w in s.scalars(select(m.Watch).where(m.Watch.workspace_id == ws)))
                   if k and not k.startswith(prefix))
    moved = []
    for key in sorted(old):
        try:
            data = blobs.get(key)
        except (OSError, KeyError):
            continue  # gone already (retention); the row still moves to the society's prefix
        blobs.put(prefix + key, data, mimetypes.guess_type(key)[0] or "application/octet-stream")
        moved.append(key)
    with repo.tx() as s:
        for model, column in key_columns():
            col = getattr(model, column)
            s.execute(update(model).where(model.workspace_id == ws, col.is_not(None), col.not_like(f"{prefix}%"))
                      .values({column: prefix + col}).execution_options(synchronize_session=False))
        for w in s.scalars(select(m.Watch).where(m.Watch.workspace_id == ws)):
            key = _crop_key(w)
            if key and not key.startswith(prefix):  # a "same as this person" watch keeps its crop
                spec = dict(w.spec_json)
                subject = dict(spec["subject"])
                subject["same_as"] = {**subject["same_as"], "crop_key": prefix + key}
                w.spec_json = {**spec, "subject": subject}
    for key in moved:
        try:
            blobs.delete(key)
        except (OSError, KeyError):
            pass
    repo.set_meta("blob_keys", DONE)
    if old:
        log.info("society %s: %d blob keys moved under its prefix (%d blobs copied)", ws, len(old), len(moved))
    return len(moved)
