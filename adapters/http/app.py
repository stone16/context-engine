"""FastAPI composition root and bounded authenticated-invocation seam."""

from collections.abc import Callable
from datetime import UTC, datetime
from json import loads
from typing import Annotated, Final
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request, Response, Security
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from adapters.http.authentication import (
    AuthenticationRejected,
    Authenticator,
    InvalidAuthenticationContext,
    RejectingAuthenticator,
    VerifiedAuthenticationContext,
)
from adapters.http.contracts import (
    AcquireWire,
    AuthenticationFailureWire,
    InvalidRequestWire,
)
from engine import BUILD_IDENTIFIER
from engine.runtime import AuthenticatedInvocation, Runtime
from engine.runtime.construction import required_kernel_dependencies
from engine.runtime.invocation import _construct_authenticated_http_invocation

HEALTH_RESPONSE: Final = {
    "status": "ready",
    "service": "context-engine-api",
    "version": BUILD_IDENTIFIER,
    "runtime_delivery": "NOT_ACTIVE",
}
AUTHENTICATION_FAILED_RESPONSE: Final = {"code": "authentication_failed"}
INVALID_REQUEST_RESPONSE: Final = {"code": "invalid_request"}


class TransportAuthenticationFailed(Exception):
    """Authentication failed without exposing credential or identity detail."""


class InvalidRequestMediaType(Exception):
    """Resolve received a body outside its sole JSON media type."""


class InvalidJsonTransport(Exception):
    """Resolve received malformed JSON or a duplicate object key."""


class DuplicateJsonObjectKey(ValueError):
    """Strict JSON decoding found an ambiguous object member."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_request_id() -> str:
    return str(uuid4())


def _unreachable_invocation_observer(
    invocation: AuthenticatedInvocation,
) -> None:
    raise RuntimeError("authenticated invocation observer is not configured")


def _reject_duplicate_json_object_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonObjectKey
        result[key] = value
    return result


def _reject_non_finite_json_number(value: str) -> object:
    raise InvalidJsonTransport


def create_app(
    *,
    authenticator: Authenticator | None = None,
    invocation_observer: Callable[[AuthenticatedInvocation], None] | None = None,
    clock: Callable[[], datetime] = _utc_now,
    request_id_factory: Callable[[], str] = _new_request_id,
) -> FastAPI:
    """Construct API; Runtime delivery and production authentication stay inactive."""

    Runtime(required_kernel_dependencies())
    if authenticator is not None and invocation_observer is None:
        raise ValueError("an injected authenticator requires an invocation observer")
    selected_authenticator = authenticator or RejectingAuthenticator()
    selected_observer = invocation_observer or _unreachable_invocation_observer
    bearer = HTTPBearer(
        scheme_name="ContextEngineBearer",
        bearerFormat="opaque",
        auto_error=False,
    )
    app = FastAPI(title="ContextEngine", version=BUILD_IDENTIFIER)

    @app.exception_handler(TransportAuthenticationFailed)
    async def authentication_failed(
        request: Request,
        error: TransportAuthenticationFailed,
    ) -> JSONResponse:
        return JSONResponse(
            AUTHENTICATION_FAILED_RESPONSE,
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(InvalidRequestMediaType)
    @app.exception_handler(InvalidJsonTransport)
    async def invalid_media_type(
        request: Request,
        error: InvalidRequestMediaType | InvalidJsonTransport,
    ) -> JSONResponse:
        return JSONResponse(INVALID_REQUEST_RESPONSE, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        status_code = (
            400
            if any(detail.get("type") == "json_invalid" for detail in error.errors())
            else 422
        )
        return JSONResponse(INVALID_REQUEST_RESPONSE, status_code=status_code)

    @app.exception_handler(StarletteHTTPException)
    async def normalize_json_parse_failure(
        request: Request,
        error: StarletteHTTPException,
    ) -> Response:
        if error.status_code == 400:
            return JSONResponse(INVALID_REQUEST_RESPONSE, status_code=400)
        return await http_exception_handler(request, error)

    async def require_closed_json_transport(request: Request) -> None:
        content_type_values = request.headers.getlist("content-type")
        request_id_values = request.headers.getlist("x-context-request-id")
        if len(content_type_values) != 1 or len(request_id_values) > 1:
            raise InvalidRequestMediaType
        media_type = content_type_values[0].partition(";")[0].strip().casefold()
        if media_type != "application/json":
            raise InvalidRequestMediaType
        try:
            loads(
                await request.body(),
                object_pairs_hook=_reject_duplicate_json_object_keys,
                parse_constant=_reject_non_finite_json_number,
            )
        except (ValueError, UnicodeDecodeError):
            raise InvalidJsonTransport from None

    def verified_authentication(
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(bearer),
        ],
    ) -> VerifiedAuthenticationContext:
        if (
            len(request.headers.getlist("authorization")) != 1
            or credentials is None
            or credentials.scheme.casefold() != "bearer"
        ):
            raise TransportAuthenticationFailed
        try:
            context = selected_authenticator.authenticate(credentials.credentials)
        except (AuthenticationRejected, InvalidAuthenticationContext):
            raise TransportAuthenticationFailed from None
        if type(context) is not VerifiedAuthenticationContext:
            raise TransportAuthenticationFailed
        return context

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return HEALTH_RESPONSE.copy()

    @app.post(
        "/v1/context:resolve",
        status_code=204,
        response_class=Response,
        dependencies=[Depends(require_closed_json_transport)],
        responses={
            400: {
                "model": InvalidRequestWire,
                "description": "The JSON transport syntax or media type is invalid.",
            },
            401: {
                "model": AuthenticationFailureWire,
                "description": "Authentication failed.",
                "headers": {
                    "WWW-Authenticate": {
                        "description": "The required transport authentication scheme.",
                        "schema": {"type": "string"},
                    }
                },
            },
            422: {
                "model": InvalidRequestWire,
                "description": "The closed request schema rejected the body.",
            },
        },
    )
    def inspect_authenticated_invocation(
        body: AcquireWire,
        authentication: Annotated[
            VerifiedAuthenticationContext,
            Depends(verified_authentication),
        ],
        context_request_id: Annotated[
            str | None,
            Header(
                alias="X-Context-Request-Id",
                min_length=1,
                pattern=r".*\S.*",
            ),
        ] = None,
    ) -> Response:
        """Expose only a test observer before Runtime delivery is activated."""

        request_id = context_request_id or request_id_factory()
        invocation = _construct_authenticated_http_invocation(
            request_id=request_id,
            organization_ref=authentication.organization_ref,
            principal_ref=authentication.principal_ref,
            membership_ref=authentication.membership_ref,
            agent_version_ref=authentication.agent_version_ref,
            authenticated_application_ref=(
                authentication.authenticated_application_ref
            ),
            authentication_binding_ref=authentication.authentication_binding_ref,
            received_at=clock(),
        )
        selected_observer(invocation)
        return Response(status_code=204)

    return app


app = create_app()
