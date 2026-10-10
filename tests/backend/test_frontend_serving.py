from fastapi.testclient import TestClient

from speech_to_text.backend.app import create_app


class NoopRuntime:
    def shutdown(self):
        pass


def test_health_and_frontend_assets_do_not_initialize_model(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    (frontend / "assets").mkdir()
    (frontend / "assets" / "app.js").write_text("bundle", encoding="utf-8")
    monkeypatch.setenv("SPEECH_TO_TEXT_FRONTEND_DIR", str(frontend))
    app = create_app(runtime=NoopRuntime())

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/").text == "<div id='root'></div>"
        assert client.get("/assets/app.js").text == "bundle"
        assert client.get("/profiles").text == "<div id='root'></div>"
        assert client.get("/missing.js").status_code == 404
        assert client.get("/api/v1/not-a-route").status_code == 404
