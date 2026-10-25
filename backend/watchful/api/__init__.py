"""The HTTP API, split into routers (api/app.py). `watchful.api:create_app` stays the app factory uvicorn starts."""


def __getattr__(name: str):
    if name == "create_app":
        from watchful.api.app import create_app
        return create_app
    raise AttributeError(name)
