"""FastAPI application factory and process lifecycle."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from speech_to_text.backend.api.router import router
from speech_to_text.backend.dependencies import BackendRuntime


def create_app(*, runtime=None):
    backend_runtime = runtime or BackendRuntime()

    @asynccontextmanager
    async def lifespan(app):
        app.state.backend_runtime = backend_runtime
        yield
        backend_runtime.shutdown()

    app = FastAPI(title="Speech to Text API", version="1", lifespan=lifespan)
    app.state.backend_runtime = backend_runtime
    app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
