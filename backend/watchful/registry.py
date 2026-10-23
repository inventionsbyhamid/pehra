"""Every swappable part (detector, pose, faces, embedder, AI, storage, database, notifier, metrics)
is registered here by kind and name; settings pick the name."""
from typing import Callable

_FACTORIES: dict[str, dict[str, Callable]] = {}


def register(kind: str, *names: str):
    def add(factory: Callable) -> Callable:
        for name in names:
            _FACTORIES.setdefault(kind, {})[name] = factory
        return factory
    return add


def available(kind: str) -> list[str]:
    return sorted(_FACTORIES.get(kind, {}))


def build(kind: str, name: str, **kwargs):
    factory = _FACTORIES.get(kind, {}).get(name)
    if factory is None:
        raise ValueError(f"no {kind} called {name}; choose one of: {', '.join(available(kind))}")
    return factory(name=name, **kwargs)
