"""Spawn-per-session local stdio MCP adapter entry point."""

from __future__ import annotations

import sys
from collections.abc import Sequence

import anyio
from mcp.server.stdio import stdio_server

from adapters.http.dogfood_client import (
    DogfoodEvaluationUnavailable,
    DogfoodHttpConfiguration,
    DogfoodResolveClient,
)
from adapters.mcp.server import create_mcp_server


async def _run() -> None:
    configuration = DogfoodHttpConfiguration.load()
    server = create_mcp_server(DogfoodResolveClient(configuration))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main(argv: Sequence[str] | None = None) -> None:
    """Run exactly one stdio session and retain no state after it ends."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments:
        raise SystemExit("context-engine-mcp: arguments are unavailable")
    try:
        anyio.run(_run)
    except DogfoodEvaluationUnavailable:
        print("context-engine-mcp: configuration unavailable", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
