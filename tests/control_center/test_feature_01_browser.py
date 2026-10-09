"""Headless browser integration checks with simulated external boundaries."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import platform
import shutil
import subprocess
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "speech_to_text/control_center/static"


def _browser_executable():
    configured = os.environ.get("CHROME_BIN") or os.environ.get("CHROMIUM_BIN")
    if configured and Path(configured).is_file():
        return configured
    for executable in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(executable)
        if found:
            return found
    candidates = {
        "Darwin": [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ],
        "Windows": [
            str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"),
            str(Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe"),
        ],
        "Linux": ["/usr/bin/google-chrome", "/usr/bin/chromium", "/snap/bin/chromium"],
    }
    return next((path for path in candidates.get(platform.system(), []) if Path(path).is_file()), None)


CHROME = _browser_executable()
pytestmark = pytest.mark.skipif(CHROME is None, reason="Google Chrome or Chromium is not installed (set CHROME_BIN or CHROMIUM_BIN)")


class FeaturePageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        scenario = self.path.split("?scenario=", 1)[-1] if "?scenario=" in self.path else ""
        if self.path == "/" or self.path.startswith("/?scenario="):
            page = (STATIC / "index.html").read_text()
            page = page.replace(
                '<script type="module" src="/assets/control-center.js"></script>',
                f'<script>{self._browser_boundary_script(scenario)}</script>'
                '<script type="module" src="/assets/control-center.js"></script>',
            )
            self._send(200, "text/html; charset=utf-8", page)
            return
        if self.path.startswith("/assets/"):
            relative = self.path.removeprefix("/assets/").split("?", 1)[0]
            asset = (STATIC / relative).resolve()
            if not asset.is_relative_to(STATIC.resolve()) or not asset.is_file():
                self._send(404, "text/plain", "not found")
                return
            content_type = "text/javascript; charset=utf-8" if asset.suffix == ".js" else "text/css; charset=utf-8" if asset.suffix == ".css" else "text/html; charset=utf-8"
            self._send(200, content_type, asset.read_text())
            return
        self._send(404, "text/plain", "not found")

    def _browser_boundary_script(self, scenario):
        return f"""
          const scenario = {scenario!r};
          document.documentElement.dataset.proof = 'simulated injected boundaries';
          window.__calls = [];
          const nativeFetch = window.fetch.bind(window);
          const featureId = 'feature-01-model-deployment';
          const model = {{key: 'turbo', display_name: 'Turbo', installed: true, runtimes: [
            {{key: 'ctranslate2', compatible: true, ready: true, precision_options: ['int8'], reason: null}}
          ]}};
          const json = (body, status = 200) => Promise.resolve({{ok: status >= 200 && status < 300, status, json: async () => body}});
          window.fetch = async (url, options = {{}}) => {{
            const parsedUrl = new URL(url, location.href);
            const path = parsedUrl.pathname;
            const method = options.method || 'GET';
            if (parsedUrl.origin === 'http://127.0.0.1:18767') {{
              window.__calls.push({{path, method, bridge: true}});
              if (path === '/api/devices') return json({{devices: [{{name: 'MacBook Air Microphone', default: true}}]}});
              if (path === '/api/sessions' && method === 'POST') return json({{source_id: 'simulated-source'}});
              if (path === '/api/sessions/simulated-source' && method === 'DELETE') return json({{state: 'stopped'}});
            }}
            if (!path.startsWith('/api/')) return nativeFetch(url, options);
            window.__calls.push({{path, method}});
            if (scenario === 'disconnected') throw new TypeError('Failed to fetch');
            if (path === '/api/features') return json([{{id: featureId, name: 'Feature 01', summary: 'Simulated browser integration',
              contract_url: '/docs/feature-01', verification_status: 'simulated',
              page_module: '/assets/features/feature-01/page.js', page_template: '/assets/features/feature-01/page.html',
              page_stylesheet: '/assets/features/feature-01/page.css'}}]);
            if (path === '/api/system') return json({{active_sessions: [], loaded_models: 0, recent_errors: []}});
            if (path === `/api/features/${{featureId}}/catalog`) {{
              return scenario === 'catalog-error' ? json({{detail: 'Injected catalog failure'}}, 503) : json({{models: [model]}});
            }}
            if (path === `/api/features/${{featureId}}/devices`) return json({{devices: []}});
            if (path === `/api/features/${{featureId}}/capture-capabilities`) return json({{host_bridge: scenario === 'host-bridge',
              host_bridge_url: 'http://127.0.0.1:18767/api'}});
            if (path === `/api/features/${{featureId}}`) return json({{models: scenario === 'clip' ? [{{id: 'loaded-handle', model: 'turbo', runtime: 'ctranslate2', precision: 'int8', state: 'ready'}}] : []}});
            if (path === `/api/features/${{featureId}}/models` && method === 'POST') {{
              return scenario === 'load-error' ? json({{detail: 'Injected model load failure'}}, 503) : json({{handle_id: 'simulated-handle', model: 'turbo', runtime: 'ctranslate2', precision: 'int8', state: 'ready'}});
            }}
            if (path === `/api/features/${{featureId}}/clips` && method === 'POST') return json({{transcript: 'สวัสดีครับ', elapsed_seconds: 1.25,
              measurements: [{{sequence: 0, rtf: 0.24, audio_seconds: 5, inference_seconds: 1.2, status: 'completed'}}],
              configuration: {{model: 'turbo', runtime: 'ctranslate2', language: 'th'}}}});
            if (path === `/api/features/${{featureId}}/microphones` && method === 'POST') return json({{source_id: 'simulated-source'}});
            if (path === `/api/features/${{featureId}}/sessions/simulated-source/stop` && method === 'POST') return json({{status: 'stopping'}});
            throw new Error(`Unexpected request: ${{method}} ${{path}}`);
          }};
          window.EventSource = class {{
            static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
            readyState = EventSource.CONNECTING; onmessage = null; onerror = null; closed = false;
            constructor(url) {{ this.url = url; this.readyState = EventSource.OPEN; window.__eventSource = this; }}
            deliver(type, sequence, payload) {{
              this.onmessage?.({{data: JSON.stringify({{type, sequence, event_sequence: sequence, source_id: 'simulated-source', ...payload}})}});
            }}
            close() {{ this.closed = true; this.readyState = EventSource.CLOSED; }}
          }};
          window.__runScenario = async () => {{
            const wait = async (predicate) => {{
              for (let attempt = 0; attempt < 200; attempt++) {{ if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 10)); }}
              throw new Error('Timed out waiting for rendered browser state');
            }};
            const result = (passed, detail) => {{ document.body.dataset.result = passed ? 'PASS' : 'FAIL'; document.body.dataset.detail = detail; }};
            try {{
              if (scenario === 'disconnected') {{
                await wait(() => document.getElementById('connection-state').textContent === 'Service unavailable');
                result(document.getElementById('overview-service').textContent === 'Offline' &&
                  document.getElementById('feature-nav').textContent.includes('Feature registry unavailable') &&
                  document.getElementById('feature-page').textContent.includes('Failed to fetch'), 'disconnected service rendered offline state and registry error');
                return;
              }}
              await wait(() => document.getElementById('feature-nav').textContent.includes('Feature 01'));
              await wait(() => document.getElementById('load-model'));
              if (scenario === 'catalog-error') {{
                await wait(() => document.getElementById('event-list').textContent.includes('Injected catalog failure'));
                const checks = [document.getElementById('event-list').textContent.includes('Catalog failed'),
                  document.getElementById('model-state').textContent === 'No model loaded',
                  document.documentElement.dataset.proof === 'simulated injected boundaries'];
                result(checks.every(Boolean), `catalog rendered checks: ${{JSON.stringify(checks)}}`);
                return;
              }}
              if (scenario !== 'clip') {{
                await wait(() => document.getElementById('model-select')?.value === 'turbo');
                await wait(() => document.getElementById('load-model').disabled === false);
                document.getElementById('load-model').click();
                if (scenario === 'load-error') {{
                  await wait(() => document.getElementById('model-state').textContent.includes('Injected model load failure'));
                  result(document.getElementById('model-state').classList.contains('error-copy') &&
                    document.getElementById('event-list').textContent.includes('Model load failed') &&
                    document.getElementById('start-mic').disabled, 'model load failure is rendered and microphone action stays unavailable');
                  return;
                }}
              }}
              await wait(() => document.getElementById('model-state').textContent === 'turbo · ctranslate2 · ready');
              if (scenario === 'clip') {{
                const file = new File([new Uint8Array([1, 2, 3])], 'injected.wav', {{type: 'audio/wav'}});
                Object.defineProperty(document.getElementById('clip-file'), 'files', {{value: [file]}});
                document.getElementById('clip-form').dispatchEvent(new Event('submit', {{bubbles: true, cancelable: true}}));
                await wait(() => document.getElementById('transcript-output').textContent === 'สวัสดีครับ');
                result(document.getElementById('run-time').textContent === '1.25 s' &&
                  document.getElementById('result-meta').textContent.includes('turbo · ctranslate2 · th') &&
                  document.getElementById('result-meta').textContent.includes('RTF: #0 RTF 0.240 · 5.00s audio · 1.20s inference') &&
                  document.getElementById('model-select').value === 'turbo' &&
                  document.getElementById('runtime-select').value === 'ctranslate2' &&
                  !window.__calls.some(call => call.path.endsWith('/models') && call.method === 'POST'), 'existing loaded-model adoption and WAV transcript are visible');
                return;
              }}
              document.getElementById('mic-tab').click();
              document.getElementById('start-mic').click();
              await wait(() => document.getElementById('stop-mic').classList.contains('hidden') === false);
              document.getElementById('stop-mic').click();
              await wait(() => window.__calls.some(call => call.path.endsWith('/sessions/simulated-source') && call.method === 'DELETE') ||
                window.__calls.some(call => call.path.endsWith('/sessions/simulated-source/stop')));
              const stream = window.__eventSource;
              stream.deliver('measurement', 0, {{sequence: 0, rtf: 0.5, audio_seconds: 2, inference_seconds: 1, status: 'completed'}});
              stream.deliver('transcript', 1, {{text: 'simulated Thai transcript'}});
              stream.deliver('error', 2, {{message: 'simulated chunk warning'}});
              stream.deliver('completed', 3, {{status: 'stopped'}});
              await wait(() => document.getElementById('transcript-output').textContent.includes('simulated Thai transcript') &&
                document.getElementById('event-list').textContent.includes('completed'));
              const log = [...document.querySelectorAll('#event-list .event-type')].map(node => node.textContent);
              const measurementIndex = log.indexOf('measurement');
              const transcriptIndex = log.indexOf('transcript'); const errorIndex = log.indexOf('error'); const completedIndex = log.indexOf('completed');
              const measurementVisible = document.getElementById('event-list').textContent.includes('RTF 0.500 · 2.00s audio · 1.00s inference');
              const stopRoutedCorrectly = scenario === 'host-bridge'
                ? window.__calls.some(call => call.bridge && call.path.endsWith('/sessions/simulated-source') && call.method === 'DELETE') &&
                  !window.__calls.some(call => !call.bridge && call.path.endsWith('/sessions/simulated-source/stop')) &&
                  document.getElementById('start-process').disabled
                : window.__calls.some(call => !call.bridge && call.path.endsWith('/sessions/simulated-source/stop') && call.method === 'POST');
              result(stopRoutedCorrectly &&
                measurementVisible && measurementIndex >= 0 && transcriptIndex >= 0 && completedIndex >= 0 && completedIndex < errorIndex && errorIndex < transcriptIndex &&
                document.getElementById('start-mic').classList.contains('hidden') === false &&
                document.getElementById('stop-mic').classList.contains('hidden') &&
                document.getElementById('input-state').textContent === 'Waiting for audio' && stream.closed &&
                document.documentElement.dataset.proof === 'simulated injected boundaries', 'simulated microphone transcript, error, completion, and stop lifecycle rendered in order');
            }} catch (error) {{ result(false, `${{error.stack || error.message}}; calls=${{JSON.stringify(window.__calls)}}; mic=${{document.getElementById('start-mic')?.className}}/${{document.getElementById('stop-mic')?.className}}`); }}
          }};
          document.addEventListener('DOMContentLoaded', () => window.__runScenario());
        """

    def _send(self, status, content_type, body):
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


def run_browser(scenario):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FeaturePageHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run([
            CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
            "--dump-dom", "--virtual-time-budget=5000", "--run-all-compositor-stages-before-draw",
            f"http://127.0.0.1:{server.server_port}/?scenario={scenario}",
        ], capture_output=True, text=True, timeout=45)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.returncode == 0, result.stderr
    assert 'data-result="PASS"' in result.stdout, result.stdout.split('data-detail="', 1)[-1].split('"', 1)[0]


def test_browser_adopts_existing_model_and_transcribes_clip():
    run_browser("clip")


@pytest.mark.parametrize("scenario", ["catalog-error", "load-error"])
def test_browser_renders_catalog_and_model_load_errors(scenario):
    run_browser(scenario)


def test_browser_renders_simulated_microphone_events_and_stop_lifecycle():
    run_browser("microphone")


def test_browser_routes_mac_docker_microphone_lifecycle_through_host_bridge():
    run_browser("host-bridge")


def test_browser_renders_disconnected_service_state():
    run_browser("disconnected")
