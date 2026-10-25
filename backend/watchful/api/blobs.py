"""Pictures and clips on local storage, behind signed short lived links (store/blobs.py MediaSigner). A link is
served only to a signed in member of the society that owns the key, which is every key under `{workspace_id}/`
(older keys were moved there, store/rekey.py). With S3 the links are S3's own presigned ones and this route is
unused."""
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from watchful.auth.deps import session_member
from watchful.store.blobs import in_society


def owns(repo, workspace_id: str, key: str) -> bool:
    """A key is the society's when it is under the society's prefix; `repo` is kept for callers."""
    return in_society(key, workspace_id)


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
        if not owns(None, member.workspace.id, key):
            raise HTTPException(404, "Not found.")
        path = (root / key).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "Not found.")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})

    return router
