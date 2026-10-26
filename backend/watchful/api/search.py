"""Questions about the footage, a camera's timeline, and finding anything by words or a photo. Any member may read;
every answer comes from the member's own society only."""
import time
from dataclasses import asdict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from watchful import demo_rules
from watchful.agent.ask import AskContext, ask, people_groups
from watchful.api.common import camera_or_404
from watchful.auth.deps import CurrentMember, current_member
from watchful.search import SearchQuery, search
from watchful.vision.faces import ConsentRequired, NoFaceFound

TIMELINE_KINDS = ["enter", "exit", "cross", "falling", "fallen", "recovered", "tamper_covered", "tamper_blurred",
                  "tamper_moved", "tamper_restored", "watch_log"]  # watch_log: a row a log watch wrote


class AskIn(BaseModel):
    question: str
    camera_id: str | None = None


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
        """Find anything: words, or a photo (a photo of a person needs their consent)."""
        ws = member.workspace.id
        if request.headers.get("content-type", "").startswith("multipart/"):
            form = await request.form()
            photo = await form["photo"].read() if "photo" in form else None
            q = SearchQuery(text=form.get("text") or None, photo=photo, camera_id=form.get("camera_id") or None,
                            consent=str(form.get("consent", "")).lower() == "true")
        else:
            data = await request.json()
            q = SearchQuery(text=data.get("text"), person_id=data.get("person_id"), camera_id=data.get("camera_id"),
                            since=data.get("since"), until=data.get("until"), classes=data.get("classes") or [])
        if q.text is None and q.photo is None and q.person_id is None:
            raise HTTPException(400, "Type what to look for, or add a photo.")
        if refused := demo_rules.sandbox_search(member.workspace, q.photo is not None, bool(q.person_id)):
            raise HTTPException(403, refused)
        await run_in_threadpool(own_camera, member, q.camera_id)
        faces = await run_in_threadpool(ctx.faces_for, ws) if q.photo is not None or q.person_id else None
        try:
            r = await run_in_threadpool(search, q, ctx.repo_for(ws), ctx.blobs, ctx.embedder,
                                        ctx.llm_for(ws, "search"), faces, ctx.tuning.signals, None,
                                        member.workspace.timezone,
                                        ctx.person_detector if q.photo is not None else None)
        except ConsentRequired:
            raise HTTPException(400, "Searching with a photo of a person needs their consent.")
        except (NoFaceFound, ValueError) as e:
            raise HTTPException(400, str(e))
        return {"query": r.query, "problem": r.problem, "results": [asdict(h) for h in r.results]}

    return router
