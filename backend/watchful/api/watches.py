"""Watches: presets and purposes, a camera's watches, the watch card (preview with its backtest), adding one in plain
words, switching one on or off, and lookouts. Previewing, adding and switching watches needs an admin; a lookout for a
person needs a supervisor."""
import time
from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException
from pydantic import BaseModel

from watchful.agent.compiler import Invalid, card, check_age, check_watch, compile_watch, context_for
from watchful.api.blobs import owns
from watchful.api.common import LIVE_KINDS, alert_or_404, camera_or_404
from watchful.auth.deps import CurrentMember, current_member, require_role
from watchful.ingest.push import push_parts
from watchful.logs import readers
from watchful.rules.backtest import backtest
from watchful.rules.presets import describe, preset, purposes
from watchful.rules.watch import SameAs, Watch
from watchful.store.db import Camera, WatchRow


class WatchIn(BaseModel):
    text: str | None = None
    preset: str | None = None
    fill: dict = {}
    watch: dict | None = None  # a watch already shown on the card by preview, saved as shown
    coverage: list[dict] = []
    setup: list[str] = []
    cannot: list[dict] = []
    scope: str | None = None  # "camera" or "site", overrides what the words said
    excluded_cameras: list[str] | None = None


class LookoutIn(BaseModel):
    camera_ids: list[str]
    person_id: str | None = None
    description: str | None = None
    event_id: str | None = None
    alert_id: str | None = None
    hours: float = 24


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    manager = ctx.manager
    hub, _ = push_parts(ctx)

    def repo(member: CurrentMember):
        return ctx.repo_for(member.workspace.id)

    @router.get("/api/presets")
    def presets(member: CurrentMember = Depends(current_member)):
        return describe()

    @router.get("/api/purposes")
    def camera_purposes(member: CurrentMember = Depends(current_member)):
        return purposes()

    def scoped(r, watch: Watch, body: WatchIn) -> Watch:
        watch.subject.same_as = None  # only a lookout points at a sighting, one of this society's own (lookout below)
        if body.scope in ("camera", "site"):
            watch.scope = body.scope
        if body.excluded_cameras is not None:
            own = {c.id for c in r.list_cameras()}
            watch.excluded_cameras = [c for c in body.excluded_cameras if c in own]
        return watch

    @router.post("/api/cameras/{camera_id}/watches/preview")
    def preview_watch(camera_id: str, body: WatchIn, member: CurrentMember = Depends(require_role("admin"))):
        """The watch card: what it will and won't watch, setup tasks, and how often it would have fired."""
        r = repo(member)
        cam = camera_or_404(r, camera_id)
        result = compile_watch(body.text or "", context_for(r, cam, attributes=readers.attributes_of(cam)),
                               ctx.llm_for(member.workspace.id, "compile"))
        out = {**card(result), "backtest": None, "turn_on": readers.needed(result.watch, cam)}
        if result.watch is not None:
            watch = scoped(r, result.watch, body)
            ids = [c.id for c in r.list_cameras() if watch.applies_to(c.id, cam.id)]
            out["watch"] = watch.model_dump()
            out["backtest"] = asdict(backtest(watch, r, ids, tz=member.workspace.timezone, tuning=ctx.tuning))
        return out

    @router.get("/api/cameras/{camera_id}/watches")
    def watches(camera_id: str, member: CurrentMember = Depends(current_member)):
        r = repo(member)
        camera_or_404(r, camera_id)
        return [asdict(x) for x in r.list_watches(camera_id)]

    def restart_where_it_runs(r, watch: Watch | WatchRow, home_camera_id: str) -> None:
        """A watch's cameras; for a stored row, from its own scope columns, so a row that no longer parses can
        still be switched off."""
        for c in r.list_cameras():
            if isinstance(watch, Watch):
                runs = watch.applies_to(c.id, home_camera_id)
            else:
                runs = c.id == home_camera_id or (watch.scope == "site" and c.id not in watch.excluded_cameras)
            if c.kind in LIVE_KINDS and runs:
                manager.restart(c.id)
            if c.kind == "webcam" and runs:
                hub.poke(c.id)  # a device sending this camera checks whether faces are still to be blurred

    def save_watch(member: CurrentMember, cam: Camera, text: str, watch: Watch, shown: dict | None = None) -> WatchRow:
        r, shown = repo(member), shown or {}
        row = r.add_watch(WatchRow(None, cam.id, text, watch.model_dump(), True, coverage=shown.get("coverage", []),
                                   setup=shown.get("setup", []), cannot=shown.get("cannot", [])))
        ctx.db.audit(member.workspace.id, member.user.id, "watch.add", {"watch": row.id, "camera": cam.id})
        restart_where_it_runs(r, watch, cam.id)
        return row

    def no_age(r, cam: Camera, watch: Watch) -> None:
        """A preset filled in, or a lookout's description, is checked for age like any other watch."""
        try:
            check_age(watch.model_dump(), context_for(r, cam))
        except Invalid as e:
            raise HTTPException(400, f"That watch can't be saved: {e}.")

    def checked(member: CurrentMember, cam: Camera, body: WatchIn) -> tuple[Watch, dict]:
        """A watch the client sends is checked again like the AI's, and may only point at this society's pictures and
        events; the card is worked out again rather than trusted."""
        r = repo(member)
        try:
            result = check_watch(body.watch, body.text or str(body.watch.get("name") or ""),
                                 context_for(r, cam, attributes=readers.attributes_of(cam)), body.coverage, body.cannot)
        except Invalid as e:
            raise HTTPException(400, f"That watch can't be saved: {e}.")
        same = result.watch.subject.same_as
        if same and ((same.crop_key and not owns(r, member.workspace.id, same.crop_key))
                     or (same.event_id and r.get_event(same.event_id) is None)):
            raise HTTPException(400, "That watch can't be saved: it points at a picture from somewhere else.")
        return result.watch, card(result)

    @router.post("/api/cameras/{camera_id}/watches")
    def add_watch(camera_id: str, body: WatchIn, member: CurrentMember = Depends(require_role("admin"))):
        r = repo(member)
        cam = camera_or_404(r, camera_id)
        shown: dict = {}
        if body.preset:
            try:
                watch = preset(body.preset, **body.fill)
            except (KeyError, ValueError) as e:
                raise HTTPException(400, f"That preset can't be used: {e}")
            no_age(r, cam, watch)
            text = body.preset
        elif body.watch is not None:
            watch, shown = checked(member, cam, body)
            text = body.text or watch.name
        else:
            result = compile_watch(body.text or "", context_for(r, cam, attributes=readers.attributes_of(cam)),
                                   ctx.llm_for(member.workspace.id, "compile"))
            shown = {**card(result), "turn_on": readers.needed(result.watch, cam)}
            if result.watch is None:
                return {**shown, "saved": False}
            if result.cannot:  # only part of the words can be watched: show the card and let a person decide
                return {**shown, "watch": scoped(r, result.watch, body).model_dump(), "saved": False,
                        "problem": "Only part of this can be watched. Check the card, then save it."}
            watch, text = result.watch, body.text or ""
        row = save_watch(member, cam, text, scoped(r, watch, body), shown)
        return {**shown, "id": row.id, "watch": row.watch, "problem": None, "saved": True}

    @router.patch("/api/watches/{watch_id}")
    def toggle_watch(watch_id: str, enabled: bool = Body(..., embed=True),
                     member: CurrentMember = Depends(require_role("admin"))):
        r = repo(member)
        row = next((x for x in r.list_watches() if x.id == watch_id), None)
        if row is None:
            raise HTTPException(404, "No such watch.")
        row.enabled = enabled
        r.update_watch(row)
        ctx.db.audit(member.workspace.id, member.user.id, "watch.toggle", {"watch": row.id, "enabled": enabled})
        restart_where_it_runs(r, row, row.camera_id)
        return asdict(row)

    def alert_sighting(r, alert_id: str):
        """The best recorded sighting of the person an alert is about."""
        a = alert_or_404(r, alert_id)
        if a.track_id is None:
            return None
        rows = [e for e in r.query_events(camera_id=a.camera_id, since=a.ts - 3600, until=a.ts + 60, limit=5000)
                if e.track_id == a.track_id]
        return max(rows, key=lambda e: (e.embedding is not None, e.signature is not None), default=None)

    @router.post("/api/lookouts")
    def lookout(body: LookoutIn, member: CurrentMember = Depends(require_role("supervisor"))):
        r = repo(member)
        fill: dict = {}
        if body.person_id:
            if not any(e.person_id == body.person_id for e in r.list_enrollments()):
                raise HTTPException(404, "No such person.")
            fill["who"] = body.person_id
        elif body.description:
            fill["text"] = body.description
        elif body.event_id or body.alert_id:
            e = r.get_event(body.event_id) if body.event_id else alert_sighting(r, body.alert_id)
            if e is None:
                raise HTTPException(404, "No such sighting.")
            fill["same_as"] = SameAs(event_id=e.id, signature=e.signature, embedding=e.embedding,
                                     crop_key=e.keyframe_key,
                                     expires_at=time.time() + min(max(body.hours, 1), 24) * 3600).model_dump()
        else:
            raise HTTPException(400, "Pick a person, a description or a sighting.")
        cams = [camera_or_404(r, cid) for cid in body.camera_ids]
        if cams:
            no_age(r, cams[0], preset("lookout", **fill))
        return {"watch_ids": [save_watch(member, cam, "lookout", preset("lookout", **fill)).id for cam in cams]}

    return router
