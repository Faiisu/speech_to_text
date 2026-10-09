"""FastAPI application factory for the local control center."""

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .registry import FeatureContribution, FeatureRegistry
from ..features.feature_01_control import create_router

STATIC_DIR = Path(__file__).with_name("static")


def default_registry(**feature_options):
    return FeatureRegistry((FeatureContribution(
        id="feature-01-model-deployment",
        name="Model deployment & audio-to-text",
        summary="Load a model, send a WAV clip, or inspect live microphone flows.",
        spec=".scratch/new-speech-to-text/spec.md#feature-01",
        page_module="/assets/features/feature-01/page.js",
        page_template="/assets/features/feature-01/page.html",
        page_stylesheet="/assets/features/feature-01/page.css",
        router_factory=lambda: create_router(**feature_options),
        implementation_status="implemented",
        verification_status="injected-passed; browser-passed; hardware-pending",
    ),))


def _static_version():
    digest = hashlib.blake2b(digest_size=12)
    for path in sorted(STATIC_DIR.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest.update(path.relative_to(STATIC_DIR).as_posix().encode())
            digest.update(f"{stat.st_mtime_ns}:{stat.st_size}".encode())
    return digest.hexdigest()


def create_app(*, registry=None, feature_options=None, dev=False):
    registry = registry or default_registry(**(feature_options or {}))
    owned = {}

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            for feature_state in owned.values():
                feature_state.close_all()

    app = FastAPI(title="Speech-to-Text Control Center", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])
    app.state.feature_registry = registry
    app.state.feature_state = owned
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        if dev:
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            reload_script = """<script>
              (() => {
                let version;
                const check = async () => {
                  try {
                    const response = await fetch('/__dev/static-version', { cache: 'no-store' });
                    if (response.ok) {
                      const next = (await response.json()).version;
                      if (version !== undefined && version !== next) location.reload();
                      version = next;
                    }
                  } catch (_) {}
                  setTimeout(check, 700);
                };
                check();
              })();
            </script>"""
            return HTMLResponse(html.replace("</body>", reload_script + "</body>"))
        return FileResponse(STATIC_DIR / "index.html")

    if dev:
        @app.get("/__dev/static-version", include_in_schema=False)
        def static_version():
            response = JSONResponse({"version": _static_version()})
            response.headers["Cache-Control"] = "no-store"
            return response

    @app.get("/docs/features/{feature_id}", include_in_schema=False)
    def feature_contract(feature_id: str):
        feature = registry.get(feature_id)
        if feature is None:
            raise HTTPException(404, detail="Feature is not registered")
        root = STATIC_DIR.parents[2]
        contract_path = (root / feature.spec.partition("#")[0]).resolve()
        if root.resolve() not in contract_path.parents or not contract_path.is_file():
            raise HTTPException(404, detail="Feature contract was not found")
        return FileResponse(contract_path, media_type="text/markdown")

    @app.get("/api/features")
    def features():
        return [{"id": item.id, "name": item.name, "summary": item.summary, "spec": item.spec,
                 "page_module": item.page_module, "page_template": item.page_template,
                 "page_stylesheet": item.page_stylesheet,
                 "contract_url": f"/docs/features/{item.id}" + (f"#{item.spec.partition('#')[2]}" if "#" in item.spec else ""),
                 "implementation_status": item.implementation_status,
                 "verification_status": item.verification_status} for item in registry.list()]

    @app.get("/api/system")
    def system():
        active = []
        errors = []
        model_handles = 0
        for feature_id, state in owned.items():
            model_handles += len(state.models)
            for group in state.groups.values():
                if getattr(group, "_model_process", None) is not None and group._model_process.is_alive():
                    model_handles += 1
                else:
                    model_handles += sum(session.process.is_alive() for session in getattr(group, "sessions", ()))
            active.extend(state.session_summaries(feature_id))
            errors.extend(state.recent_errors)
        return {"service": "ready", "features": len(registry.list()), "loaded_models": model_handles,
                "active_sessions": active, "recent_errors": errors[-10:]}

    for contribution in registry.list():
        router = contribution.router_factory()
        if hasattr(router, "state"):
            owned[contribution.id] = router.state
        app.include_router(router, prefix=f"/api/features/{contribution.id}", tags=[contribution.id])

    return app


def create_dev_app():
    """Create the control center with development-only browser refresh enabled."""
    return create_app(dev=True)
