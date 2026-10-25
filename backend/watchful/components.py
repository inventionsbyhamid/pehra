"""Builds the parts the settings name. Swap a model or a service by changing a setting."""
import threading

from watchful.config import Settings
from watchful.registry import build
from watchful.tuning import Tuning


def load_all() -> None:
    """Import every module that registers parts."""
    import watchful.agent.llm  # noqa: F401
    import watchful.metrics  # noqa: F401
    import watchful.notify  # noqa: F401
    import watchful.store.blobs  # noqa: F401
    import watchful.store.s3  # noqa: F401
    import watchful.store.sql  # noqa: F401
    import watchful.vision.detect  # noqa: F401
    import watchful.vision.embed  # noqa: F401
    import watchful.vision.faces  # noqa: F401
    import watchful.vision.ocr  # noqa: F401
    import watchful.vision.plates  # noqa: F401
    import watchful.vision.pose  # noqa: F401


def make_detector(settings: Settings, tuning: Tuning, classes: set[str] | None = None, name: str | None = None):
    load_all()
    return build("detector", name or settings.detector, classes=classes, cfg=tuning.detect, settings=settings)


def make_pose(settings: Settings, tuning: Tuning):
    load_all()
    return build("pose", settings.pose_model, cfg=tuning.pose, settings=settings)


def make_faces(settings: Settings, tuning: Tuning):
    load_all()
    return build("faces", settings.face_model, cfg=tuning.faces, settings=settings)


_embedders: dict[tuple, object] = {}


def make_embedder(settings: Settings):
    """One embedder per model per process; the image and text towers are large."""
    load_all()
    key = (settings.embedder, str(settings.models_dir))
    if key not in _embedders:
        _embedders[key] = build("embedder", settings.embedder, settings=settings)
    return _embedders[key]


def make_llm(settings: Settings, repo, task: str = "verify"):
    from watchful.agent.llm import LimitedLLM
    load_all()
    model = getattr(settings, f"ai_model_{task}", None)
    return LimitedLLM(build("llm", settings.ai_provider, settings=settings, model=model), repo,
                      settings.ai_daily_call_limit)


def make_blobs(settings: Settings, tuning: Tuning | None = None):
    from watchful.store.blobs import MediaSigner
    from watchful.tuning import load_tuning
    load_all()
    ttl_s = (tuning or load_tuning(settings.tuning_file)).auth.media_link_s
    return build("blobs", settings.storage, data_dir=settings.data_dir, settings=settings,
                 signer=MediaSigner(settings.session_secret, ttl_s), url_ttl_s=ttl_s)


_engines: dict[str, object] = {}
_engines_lock = threading.Lock()


def make_engine(settings: Settings):
    """One engine per database per process, its schema brought up to date by Alembic on first use."""
    from watchful.db.legacy import adopt_legacy
    from watchful.db.session import make_engine as engine_for, migrate
    url = settings.database
    with _engines_lock:
        if url not in _engines:
            adopt_legacy(url, settings.data_key or settings.session_secret)
            engine = engine_for(url)
            migrate(engine)
            _engines[url] = engine
        return _engines[url]


def make_db(settings: Settings):
    from watchful.db.platform import Database
    return Database(make_engine(settings))


def make_auth_providers(settings: Settings) -> dict:
    """Sign in providers by name, in the order the settings list them."""
    import watchful.auth.google  # noqa: F401
    return {name: build("auth", name, settings=settings) for name in settings.auth_providers}


def make_repo(settings: Settings, workspace_id: str | None = None):
    """The repository of one society. Until sign in brings real societies, the demo's single society is used."""
    from watchful.store.db import DEFAULT_WORKSPACE
    from watchful.store.sql import ensure_workspace
    load_all()
    engine = make_engine(settings)
    if workspace_id is None or workspace_id == DEFAULT_WORKSPACE:
        workspace_id = DEFAULT_WORKSPACE
        ensure_workspace(engine, DEFAULT_WORKSPACE)
    return build("repo", settings.db, engine=engine, workspace_id=workspace_id, settings=settings)


def make_metrics(settings: Settings):
    load_all()
    return build("metrics", settings.metrics, settings=settings)


def make_notifier(settings: Settings):
    load_all()
    return build("notify", settings.notify, topic_arn=settings.sns_topic_arn, region=settings.aws_region)


def make_ocr(settings: Settings, tuning: Tuning):
    load_all()
    return build("ocr", settings.ocr, settings=settings, tuning=tuning)


_plate_readers: dict[tuple, object] = {}


def make_plate_reader(settings: Settings, tuning: Tuning):
    """One plate reader per models and tuning per process."""
    from watchful.vision.plates import PlateReader
    load_all()
    key = (settings.plate_detector, settings.ocr, str(settings.models_dir), settings.dnn_engine,
           tuning.plates.model_dump_json())
    if key not in _plate_readers:
        detector = build("plate_detector", settings.plate_detector, cfg=tuning.plates, settings=settings)
        _plate_readers[key] = PlateReader(detector, make_ocr(settings, tuning), tuning.plates)
    return _plate_readers[key]
