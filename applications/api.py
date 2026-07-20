"""Engine API process entry point."""

import argparse
from collections.abc import Sequence

import uvicorn


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ContextEngine API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)
    uvicorn.run(
        "adapters.http.app:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
