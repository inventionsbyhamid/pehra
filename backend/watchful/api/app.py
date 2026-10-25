"""The HTTP app: health check, sign in and societies, the society routers, live sockets and the built web app.
Every router is added with `mount(app, ctx)`; a route reads and writes a society's data only through
`ctx.repo_for(member.workspace.id)`."""
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException

from watchful.api import account, alerts, blobs, cameras, live, people, search, watches
from watchful.api.common import LIVE_KINDS
from watchful.api.headers import SecurityHeaders
from watchful.auth.deps import AuthContext
from watchful.brand import BRAND
from watchful.components import make_blobs, make_detector, make_embedder, make_faces, make_llm, make_repo
from watchful.config import Settings, get_settings
from watchful.db import models as m
from watchful.store.db import DEFAULT_WORKSPACE

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
NOT_APP = ("api", "ws", "auth", "healthz", "blobs")  # paths the web app never answers
SHELL = "app.html"  # the app's own page; index.html is the prerendered landing


class AppContext(AuthContext):
    """Everything the routes share: the auth context, storage, the camera manager, and per society repositories,
    AI clients (each counts against that society's cap) and face indexes (a society's enrolled staff only)."""

    def __init__(self, settings: Settings, llm=None, manager=None, providers: dict | None = None, tuning=None,
                 db=None):
        super().__init__(settings, db=db, providers=providers, tuning=tuning)
        self.blobs = make_blobs(settings, self.tuning)
        self.llm = llm
        self._repos: dict[str, object] = {}
        self._llms: dict[tuple[str, str], object] = {}
        self._faces: dict[str, object] = {}
        self._embedder = None
        self._detector = None
        self._parts = threading.Lock()
        if manager is None:
            from watchful.workers import WorkerManager
            manager = WorkerManager(settings, self.tuning, self.repo_for(DEFAULT_WORKSPACE), self.blobs,
                                    repo_for=self.repo_for)
        self.manager = manager

    def repo_for(self, workspace_id: str):
        with self._parts:
            if workspace_id not in self._repos:
                self._repos[workspace_id] = make_repo(self.settings, workspace_id)
            return self._repos[workspace_id]

    def llm_for(self, workspace_id: str, task: str):
        if self.llm is not None:
            return self.llm
        repo = self.repo_for(workspace_id)
        with self._parts:
            if (workspace_id, task) not in self._llms:
                self._llms[workspace_id, task] = make_llm(self.settings, repo, task)
            return self._llms[workspace_id, task]

    def faces_for(self, workspace_id: str):
        repo = self.repo_for(workspace_id)
        with self._parts:
            if workspace_id not in self._faces:
                faces = make_faces(self.settings, self.tuning)
                faces.load(repo.list_enrollments())
                self._faces[workspace_id] = faces
            return self._faces[workspace_id]

    def forget_faces(self, workspace_id: str) -> None:
        with self._parts:
            self._faces.pop(workspace_id, None)

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = make_embedder(self.settings)
        return self._embedder

    @property
    def person_detector(self):
        if self._detector is None:
            self._detector = make_detector(self.settings, self.tuning, {"person"})
        return self._detector

    def workspace_ids(self) -> list[str]:
        with self.db.tx() as s:
            return list(s.scalars(select(m.Workspace.id).order_by(m.Workspace.created_at)))


def create_app(settings: Settings | None = None, llm=None, manager=None, providers: dict | None = None,
               tuning=None) -> FastAPI:
    settings = settings or get_settings()
    ctx = AppContext(settings, llm=llm, manager=manager, providers=providers, tuning=tuning)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for ws in ctx.workspace_ids():
            for cam in ctx.repo_for(ws).list_cameras():
                if cam.enabled and cam.kind in LIVE_KINDS - {"webcam"}:
                    ctx.manager.start(cam.id, ws)
        yield
        ctx.manager.shutdown()

    app = FastAPI(title=BRAND.name, lifespan=lifespan)
    app.state.ctx = ctx
    app.add_middleware(SecurityHeaders, settings=settings)

    @app.get("/healthz")
    def healthz():
        info = cv2.getBuildInformation()
        return {"ok": True, "opencv": cv2.__version__, "kleidicv": "KleidiCV" in info or "kleidicv" in info.lower()}

    for router in (account, cameras, watches, alerts, search, people, blobs, live):
        router.mount(app, ctx)

    dist = Path(settings.web_dist) if settings.web_dist else WEB_DIST
    if dist.is_dir():
        app.mount("/", SpaFiles(directory=dist, html=True), name="web")
    return app


class SpaFiles(StaticFiles):
    """The built web app: the public pages as prerendered, and any other path that is not an API, socket, sign in or
    media route (/app and below, /cameras, /join, ...) opens the app's own page."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path.split("/", 1)[0] in NOT_APP:
                raise
            shell = SHELL if (Path(self.directory) / SHELL).is_file() else "index.html"
            return await super().get_response(shell, scope)
