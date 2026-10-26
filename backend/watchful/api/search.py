"""Questions about the footage, a camera's timeline, and finding anything by words. Any member may read; finding an
enrolled staff member by face needs a supervisor, and looks only at entry cameras. There is no search by photo, by face
or by appearance: nobody is found from a picture of them. Every answer comes from the member's own society only."""
import time
from dataclasses import asdict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError

from watchful import demo_rules
from watchful.agent.ask import AskContext, ask, people_groups
from watchful.api.common import camera_or_404
from watchful.auth.deps import CurrentMember, current_member
from watchful.search import SearchQuery, search
from watchful.vision.faces import NoFaceFound

TIMELINE_KINDS = ["enter", "exit", "cross", "falling", "fallen", "recovered", "tamper_covered", "tamper_blurred",
                  "tamper_moved", "tamper_restored", "watch_log"]  # watch_log: a row a log watch wrote


class AskIn(BaseModel):
    question: str
    camera_id: str | None = None


class SearchIn(BaseModel):
    text: str | None = None
    person_id: str | None = None  # an enrolled staff member (supervisors and above)
    camera_id: str | None = None
    since: float | None = None
    until: float | None = None
    classes: list[str] = []


BY_WORDS = "Search by words. Finding someone from a photo is not offered."


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def make_router(ctx) -> APIRouter:
    router = APIRouter()

    def ask_ctx(member: CurrentMember, camera_id: str | None) -> AskContext:
        return AskContext(ctx.repo_for(member.workspace.id), ctx.blobs, camera_id, member.workspace.timezone,
                          ctx.tuning.appearance)

    def own_camera(member: CurrentMember, camera_id: str | None) -> None:
        if camera_id is not None:
            camera_or_404(ctx.repo_for(member.workspace.id), camera_id)

    @router.post("/api/ask")
    def ask_route(body: AskIn, member: CurrentMember = Depends(current_member)):
        own_camera(member, body.camera_id)
        a = ask(body.question, body.camera_id, ask_ctx(member, body.camera_id),
                ctx.llm_for(member.workspace.id, "ask"), now=time.time())
        return {**asdict(a), "trace": [asdict(t) for t in a.trace]}

    @router.get("/api/cameras/{camera_id}/timeline")
    def timeline(camera_id: str, since: float | None = None, until: float | None = None,
                 member: CurrentMember = Depends(current_member)):
        repo = ctx.repo_for(member.workspace.id)
        camera_or_404(repo, camera_id)
        events = repo.query_events(camera_id=camera_id, kinds=TIMELINE_KINDS, since=since, until=until, limit=2000)
        people = people_groups(ask_ctx(member, camera_id), since, until)
        for g in people:
            g["thumbnail_url"] = ctx.blobs.url(g.pop("thumbnail_key")) if g.get("thumbnail_key") else None
        return {"events": [{**asdict(e), "signature": None, "embedding": None,
                            "thumbnail_url": ctx.blobs.url(e.keyframe_key) if e.keyframe_key else None}
                           for e in events], "people": people}

    @router.post("/api/search")
    async def search_route(request: Request, member: CurrentMember = Depends(current_member)):
        """Find anything by words, or an enrolled staff member (supervisors and above). A photo is refused."""
        ws = member.workspace.id
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(415, BY_WORDS)
        try:
            body = SearchIn.model_validate(await request.json())
        except (ValueError, ValidationError):
            raise HTTPException(400, BY_WORDS) from None
        q = SearchQuery(text=body.text or None, person_id=body.person_id or None, camera_id=body.camera_id,
                        since=body.since, until=body.until, classes=body.classes)
        if q.text is None and q.person_id is None:
            raise HTTPException(400, "Type what to look for.")
        if q.person_id and not member.can("supervisor"):
            raise HTTPException(403, "Finding a staff member needs a supervisor or above.")
        if refused := demo_rules.sandbox_search(member.workspace, bool(q.person_id)):
            raise HTTPException(403, refused)
        await run_in_threadpool(own_camera, member, q.camera_id)
        faces = await run_in_threadpool(ctx.faces_for, ws) if q.person_id else None
        try:
            r = await run_in_threadpool(search, q, ctx.repo_for(ws), ctx.blobs, ctx.embedder,
                                        ctx.llm_for(ws, "search"), faces, ctx.tuning.signals, None,
                                        member.workspace.timezone)
        except (NoFaceFound, ValueError) as e:
            raise HTTPException(400, str(e))
        return {"query": r.query, "problem": r.problem, "results": [asdict(h) for h in r.results]}

    return router
