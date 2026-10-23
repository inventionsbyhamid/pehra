"""Builds the parts the settings name. Swap a model or a service by changing a setting."""
from watchful.config import Settings
from watchful.registry import build
from watchful.tuning import Tuning


def load_all() -> None:
    """Import every module that registers parts."""
    import watchful.agent.llm  # noqa: F401
    import watchful.notify  # noqa: F401
    import watchful.store.blobs  # noqa: F401
    import watchful.store.db  # noqa: F401
    import watchful.vision.detect  # noqa: F401
    import watchful.vision.embed  # noqa: F401
    import watchful.vision.faces  # noqa: F401
    import watchful.vision.pose  # noqa: F401


def make_detector(settings: Settings, tuning: Tuning, classes: set[str] | None = None):
    load_all()
    return build("detector", settings.detector, classes=classes, cfg=tuning.detect, settings=settings)


def make_pose(settings: Settings, tuning: Tuning):
    load_all()
    return build("pose", settings.pose_model, cfg=tuning.pose, settings=settings)


def make_faces(settings: Settings, tuning: Tuning):
    load_all()
    return build("faces", settings.face_model, cfg=tuning.faces, settings=settings)


def make_embedder(settings: Settings):
    load_all()
    return build("embedder", settings.embedder, settings=settings)


def make_llm(settings: Settings, repo, task: str = "verify"):
    from watchful.agent.llm import LimitedLLM
    load_all()
    model = getattr(settings, f"ai_model_{task}", None)
    return LimitedLLM(build("llm", settings.ai_provider, settings=settings, model=model), repo,
                      settings.ai_daily_call_limit)


def make_blobs(settings: Settings):
    load_all()
    return build("blobs", settings.storage, data_dir=settings.data_dir)


def make_repo(settings: Settings):
    load_all()
    return build("repo", settings.db, path=settings.data_dir / "watchful.db")


def make_notifier(settings: Settings):
    load_all()
    return build("notify", settings.notify, topic_arn=settings.sns_topic_arn, region=settings.aws_region)
