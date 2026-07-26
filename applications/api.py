"""Engine API process entry point."""

import argparse
import os
from collections.abc import Sequence
from typing import Any

import uvicorn

from adapters.http.dogfood import (
    DOGFOOD_COMPOSITION_ENV,
    DOGFOOD_COMPOSITION_VALUE,
)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ContextEngine API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)
    if (
        os.environ.get(DOGFOOD_COMPOSITION_ENV) == DOGFOOD_COMPOSITION_VALUE
        and args.host not in {"127.0.0.1", "::1"}
    ):
        parser.error("dogfood composition accepts only an explicit loopback host")
    served_app: Any = "adapters.http.app:app"
    if os.environ.get(DOGFOOD_COMPOSITION_ENV) is not None:
        from adapters.http.dogfood import create_served_app

        served_app = create_served_app(os.environ, host=args.host)
    uvicorn.run(
        served_app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
