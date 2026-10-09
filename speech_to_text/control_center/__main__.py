"""Run the local developer control center."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Run the speech-to-text local control center")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (loopback by default)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--dev", action="store_true",
                        help="Reload Python code and refresh the browser when control center assets change")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
        parser.error("the control center supports loopback or explicit 0.0.0.0 binding")
    import uvicorn
    app_factory = "speech_to_text.control_center.app:create_app"
    run_options = {
        "factory": True,
        "host": args.host,
        "port": args.port,
        "log_level": "info",
    }
    if args.dev:
        app_factory = "speech_to_text.control_center.app:create_dev_app"
        run_options.update(
            reload=True,
            reload_dirs=[str(Path(__file__).resolve().parents[1])],
        )
    uvicorn.run(app_factory, **run_options)


if __name__ == "__main__":
    main()
