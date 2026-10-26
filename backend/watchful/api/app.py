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

from watchful.api import (account, alerts, billing, blobs, cameras, demo, incidents, live, logs, people, search,
                          telegram, watches)
from watchful.api.bodylimit import BodyLimit
from watchful.api.common import LIVE_KINDS
from watchful.api.headers import SecurityHeaders
from watchful.auth.deps import AuthContext
from watchful.brand import BRAND
from watchful.components import make_blobs, make_detector, make_embedder, make_faces, make_llm, make_repo
from watchful.config import Settings, get_settings
from watchful.db import models as m
from watchful.jobs import worker_alive
from watchful.logs.ledger import ensure_overstay_watch
from watchful.logs.readers import PLATES
from watchful.store.sql import ensure_default_watches

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
NOT_APP = ("api", "ws", "auth", "healthz", "blobs", "telegram")  # paths the web app never answers
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
        self._face_nets = None
        self._face_lock = threading.RLock()
        self._embedder = None
        self._detector = None
        self._parts = threading.Lock()
        if manager is None:
            from watchful.workers import WorkerManager
            manager = WorkerManager(settings, self.tuning, None, self.blobs, repo_for=self.repo_for,
                                    societies=self.workspace_ids)
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
        """The society's face index: its own enrolled people on the app's one pair of face networks, which every
        society shares (one request at a time), so memory does not grow with the number of societies."""
        repo = self.repo_for(workspace_id)
        with self._parts:
            if workspace_id not in self._faces:
                if self._face_nets is None:
                    self._face_nets = make_faces(self.settings, self.tuning)
                self._faces[workspace_id] = OneAtATime(self._face_nets.share(repo.list_enrollments(), workspace_id),
                                                       self._face_lock)
            return self._faces[workspace_id]

    def face_index(self):
        """A face index with nobody enrolled on the app's one pair of face networks, behind the same lock (the
        Telegram preview's still maker loads each society's people into it)."""
        with self._parts:
            if self._face_nets is None:
                self._face_nets = make_faces(self.settings, self.tuning)
            return OneAtATime(self._face_nets.share([]), self._face_lock)

    def forget_faces(self, workspace_id: str) -> None:
        with self._parts:
            self._faces.pop(workspace_id, None)

    def forget_society(self, workspace_id: str) -> None:
        """A deleted society's parts go from memory: its repository, AI clients and face index (the demo reset)."""
        with self._parts:
            self._repos.pop(workspace_id, None)
            self._faces.pop(workspace_id, None)
            for key in [k for k in self._llms if k[0] == workspace_id]:
                self._llms.pop(key)
        llms = getattr(self.manager, "_llms", None)
        if isinstance(llms, dict):
            llms.pop(workspace_id, None)

    def remembered(self) -> set[str]:
        """The societies this app keeps parts of in memory."""
        with self._parts:
            return set(self._repos) | set(self._faces) | {k[0] for k in self._llms}

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


class OneAtATime:
    """An object whose methods run one call at a time across everyone sharing `lock` (face networks change their
    input size for each picture)."""

    def __init__(self, inner, lock):
        self._inner, self._lock = inner, lock

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            with self._lock:
                out = attr(*args, **kwargs)
            return OneAtATime(out, self._lock) if name == "share" else out  # another index on the same networks
        return call


def create_app(settings: Settings | None = None, llm=None, manager=None, providers: dict | None = None,
               tuning=None, jobs: bool | None = None) -> FastAPI:
    """`jobs`: start the app's own job worker process; by default when RUN_JOBS_IN_APP and the app runs its own
    camera manager (tests that pass a manager get none unless they ask)."""
    settings = settings or get_settings()
    ctx = AppContext(settings, llm=llm, manager=manager, providers=providers, tuning=tuning)
    if jobs is None:
        jobs = settings.run_jobs_in_app and manager is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.plans.start()  # cameras beyond a society's plan are switched off before any starts
        app.state.sandboxes.start()  # and the cameras of demo sandboxes that ended
        for ws in ctx.workspace_ids():
            ensure_default_watches(ctx.repo_for(ws))  # societies made before them, or by a tool, once
            cams = ctx.repo_for(ws).list_cameras()
            if any(PLATES in c.readers for c in cams):  # plate reading from before the overstay was a watch
                ensure_overstay_watch(ctx.repo_for(ws), ctx.tuning)
            for cam in cams:
                if cam.enabled and cam.kind in LIVE_KINDS:  # a camera that sends its own pictures too, so it can
                    ctx.manager.start(cam.id, ws)  # go offline when its device does not come back
        if jobs:
            from watchful.worker import InAppWorker
            app.state.jobs = InAppWorker(settings, ctx.tuning)
            app.state.jobs.start()
        yield
        app.state.plans.stop()
        app.state.sandboxes.stop()
        if app.state.jobs is not None:
            app.state.jobs.stopping()
        ctx.manager.shutdown()
        if app.state.jobs is not None:
            app.state.jobs.join(30)

    docs = settings.api_docs  # the API's own pages, for development only
    app = FastAPI(title=BRAND.name, lifespan=lifespan, docs_url="/docs" if docs else None, redoc_url=None,
                  openapi_url="/openapi.json" if docs else None)
    app.state.ctx = ctx
    app.state.jobs = None
    app.add_middleware(BodyLimit, settings=settings, tuning=ctx.tuning, sessions=ctx.sessions)
    app.add_middleware(SecurityHeaders, settings=settings)  # outside, so a refused body's answer has them too

    @app.get("/healthz")
    def healthz():
        info = cv2.getBuildInformation()
        return {"ok": True, "opencv": cv2.__version__, "kleidicv": "KleidiCV" in info or "kleidicv" in info.lower(),
                "worker_beat_fresh": worker_alive(settings.data_dir, ctx.tuning.jobs.worker_alive_s)}

    for router in (account, cameras, watches, alerts, incidents, telegram, search, people, logs, blobs, live, billing,
                   demo):
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
