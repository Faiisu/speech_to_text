"""FastAPI application factory and process lifecycle."""

from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

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

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    frontend_dir = Path(
        os.environ.get("SPEECH_TO_TEXT_FRONTEND_DIR", "/app/frontend")
    ).resolve()
    index_file = frontend_dir / "index.html"
    if index_file.is_file():

        @app.get("/", include_in_schema=False)
        def frontend_index():
            return FileResponse(index_file)

        @app.get("/{asset_path:path}", include_in_schema=False)
        def frontend_assets(asset_path: str):
            if asset_path == "healthz" or asset_path.startswith("api/"):
                raise HTTPException(status_code=404)
            candidate = (frontend_dir / asset_path).resolve()
            if candidate.is_relative_to(frontend_dir) and candidate.is_file():
                return FileResponse(candidate)
            if "." not in Path(asset_path).name:
                return FileResponse(index_file)
            raise HTTPException(status_code=404)

    return app


app = create_app()
