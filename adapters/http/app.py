"""Minimal FastAPI composition root."""

from typing import Final

from fastapi import FastAPI

from engine import BUILD_IDENTIFIER
from engine.runtime import Runtime
from engine.runtime.construction import required_kernel_dependencies

HEALTH_RESPONSE: Final = {
    "status": "ready",
    "service": "context-engine-api",
    "version": BUILD_IDENTIFIER,
    "runtime_delivery": "NOT_ACTIVE",
}


def create_app() -> FastAPI:
    """Construct the API and fail before serving if kernel wiring is incomplete."""

    Runtime(required_kernel_dependencies())
    app = FastAPI(title="ContextEngine", version=BUILD_IDENTIFIER)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return HEALTH_RESPONSE.copy()

    return app


app = create_app()
