"""Run the local developer control center."""

import argparse


def main():
    parser = argparse.ArgumentParser(description="Run the speech-to-text local control center")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (loopback by default)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
        parser.error("the control center supports loopback or explicit 0.0.0.0 binding")
    import uvicorn
    uvicorn.run("speech_to_text.control_center.app:create_app", factory=True,
                host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
