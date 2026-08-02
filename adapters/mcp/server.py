"""Acquire-only MCP stdio translation into the public HTTP resolve contract."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol, cast
from uuid import uuid4

import mcp.types as mcp_types
from mcp.server import Server, ServerRequestContext
from pydantic import TypeAdapter, ValidationError

from adapters.http.contracts import (
    AcquireWire,
    ResolutionOutcomeWire,
    resolution_outcome_public_document,
)

MCP_TOOL_NAME: Final = "context_resolve"
MCP_SERVER_NAME: Final = "context-engine-mcp"
_OUTCOME_ADAPTER: Final[TypeAdapter[ResolutionOutcomeWire]] = TypeAdapter(
    ResolutionOutcomeWire
)
_OUTCOME_SCHEMA: Final = {"type": "object", **_OUTCOME_ADAPTER.json_schema()}


class ResolveCaller(Protocol):
    """Public HTTP Acquire caller; it exposes no Runtime implementation seam."""

    def resolve_acquire_document(
        self,
        *,
        acquire: dict[str, object],
        request_id: str,
    ) -> dict[str, object]: ...


def _new_request_id() -> str:
    return f"mcp-{uuid4().hex}"


def _generic_tool_error() -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(
                type="text",
                text="Context resolve is unavailable.",
            )
        ],
        is_error=True,
    )


def create_mcp_server(
    caller: ResolveCaller,
    *,
    request_id_factory: Callable[[], str] = _new_request_id,
) -> Server[object]:
    """Create one state-free, Acquire-only MCP protocol translator."""

    async def list_tools(
        context: ServerRequestContext[object],
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        del context, params
        return mcp_types.ListToolsResult(
            tools=[
                mcp_types.Tool(
                    name=MCP_TOOL_NAME,
                    title="Resolve authorized context",
                    description=(
                        "Resolve one untrusted context need through ContextEngine's "
                        "existing authenticated HTTP Runtime seam."
                    ),
                    input_schema=AcquireWire.model_json_schema(),
                    output_schema=_OUTCOME_SCHEMA,
                )
            ]
        )

    async def call_tool(
        context: ServerRequestContext[object],
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        del context
        if params.name != MCP_TOOL_NAME:
            return _generic_tool_error()
        try:
            acquire = AcquireWire.model_validate(params.arguments)
            acquire_document = cast(
                dict[str, object],
                acquire.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
            raw_outcome = caller.resolve_acquire_document(
                acquire=acquire_document,
                request_id=request_id_factory(),
            )
            outcome = _OUTCOME_ADAPTER.validate_python(raw_outcome)
            public_document = resolution_outcome_public_document(outcome)
        except (ValidationError, ValueError, TypeError, RuntimeError):
            return _generic_tool_error()
        return mcp_types.CallToolResult(
            content=[],
            structured_content=public_document,
        )

    return Server(
        MCP_SERVER_NAME,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
