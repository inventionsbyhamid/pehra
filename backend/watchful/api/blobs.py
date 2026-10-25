"""Pictures and clips on local storage, behind signed short lived links (store/blobs.py MediaSigner). A link is
served only to a signed in member of the society that owns the key: keys under `{workspace_id}/`, and until every
key carries that prefix, keys under one of the society's camera ids or its people's photos. With S3 the links are
S3's own presigned ones and this route is unused."""
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from watchful.auth.deps import session_member


def owns(repo, workspace_id: str, key: str) -> bool:
    first, _, rest = key.partition("/")
    if not rest:
        return False
    if first == workspace_id:
        return True
    if first == "people":
        return any(e.thumbnail_key == key for e in repo.list_enrollments())
    return repo.get_camera(first) is not None


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    root = (Path(ctx.settings.data_dir) / "blobs").resolve()
    signer = getattr(ctx.blobs, "signer", None)

    @router.get("/api/blobs/{key:path}")
    async def blob(key: str, request: Request, e: int = 0, s: str = ""):
        if signer is None or not signer.valid(key, e, s):
            raise HTTPException(404, "Not found.")
        member = await run_in_threadpool(session_member, request)
        if member is None:
            raise HTTPException(401, "Sign in first.")
        if not await run_in_threadpool(owns, ctx.repo_for(member.workspace.id), member.workspace.id, key):
            raise HTTPException(404, "Not found.")
        path = (root / key).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "Not found.")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})

    return router
