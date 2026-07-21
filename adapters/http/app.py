"""FastAPI composition root and bounded authenticated-invocation seam."""

from collections.abc import Callable
from datetime import UTC, datetime
from json import loads
from typing import Annotated, Final, Literal, cast
from uuid import UUID, uuid4

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
    BudgetUsageWire,
    ContextPackageWire,
    CoverageWire,
    InvalidRequestWire,
    ResolvedWire,
    ServiceUnavailableWire,
)
from adapters.http.membership_authority import (
    MembershipAuthority,
    RejectingMembershipAuthority,
)
from adapters.http.organization_authority import (
    OrganizationAuthority,
    OrganizationVerificationRejected,
    RejectingOrganizationAuthority,
)
from adapters.http.scope_authority import (
    MissingTrustedScopeAuthority,
    ScopeAuthority,
    ScopeAuthorityIdentity,
    ScopeAuthorityUnavailable,
)
from adapters.http.transport import (
    HTTP_TRANSPORT_PROFILE_V1,
    HttpTransportProfile,
    ResolveBodyLimitMiddleware,
    enforce_json_nesting,
)
from engine import BUILD_IDENTIFIER
from engine.persistence.membership_context import (
    MembershipAuthorityUnavailable,
    MembershipIdentity,
    MembershipNotCurrent,
)
from engine.runtime import AuthenticatedInvocation, Runtime
from engine.runtime.actor import MembershipRejectionAuditReceipt
from engine.runtime.budget import PackageBudgetRequest
from engine.runtime.construction import required_kernel_dependencies
from engine.runtime.contracts import Acquire, ContextNeed, RequestNarrowing, Resolved
from engine.runtime.delivery import _construct_direct_delivery_context
from engine.runtime.invocation import (
    _construct_authenticated_http_invocation,
)
from engine.runtime.scope_authority import InvalidTrustedScopeSnapshot

HEALTH_RESPONSE: Final = {
    "status": "ready",
    "service": "context-engine-api",
    "version": BUILD_IDENTIFIER,
    "runtime_delivery": "NOT_ACTIVE",
}
AUTHENTICATION_FAILED_RESPONSE: Final = {"code": "authentication_failed"}
INVALID_REQUEST_RESPONSE: Final = {"code": "invalid_request"}
SERVICE_UNAVAILABLE_RESPONSE: Final = {"code": "service_unavailable"}
RESOLVE_PATH: Final = "/v1/context:resolve"


class TransportAuthenticationFailed(Exception):
    """Authentication failed without exposing credential or identity detail."""


class TrustedAuthorityUnavailable(Exception):
    """A required trusted authority failed without exposing identity detail."""


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


DIRECT_ACQUIRE_PURPOSE: Final = "context.answer"


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
    organization_authority: OrganizationAuthority | None = None,
    membership_authority: MembershipAuthority | None = None,
    scope_authority: ScopeAuthority | None = None,
    runtime: Runtime | None = None,
    invocation_observer: Callable[[AuthenticatedInvocation], None] | None = None,
    resolution_observer: Callable[[Resolved], None] | None = None,
    membership_rejection_observer: (
        Callable[[MembershipRejectionAuditReceipt], None] | None
    ) = None,
    clock: Callable[[], datetime] = _utc_now,
    request_id_factory: Callable[[], str] = _new_request_id,
    transport_profile: HttpTransportProfile = HTTP_TRANSPORT_PROFILE_V1,
) -> FastAPI:
    """Construct API; the module-level composition remains reject-all."""

    selected_runtime = runtime or Runtime(
        required_kernel_dependencies(),
        clock=clock,
    )
    if type(selected_runtime) is not Runtime:
        raise TypeError("runtime must be the sealed Runtime composition")
    selected_authenticator = authenticator or RejectingAuthenticator()
    selected_organization_authority = (
        organization_authority or RejectingOrganizationAuthority()
    )
    selected_membership_authority = (
        membership_authority or RejectingMembershipAuthority()
    )
    selected_scope_authority = scope_authority or MissingTrustedScopeAuthority()
    bearer = HTTPBearer(
        scheme_name="ContextEngineBearer",
        bearerFormat="opaque",
        auto_error=False,
    )
    app = FastAPI(title="ContextEngine", version=BUILD_IDENTIFIER)
    app.add_middleware(
        ResolveBodyLimitMiddleware,
        profile=transport_profile,
        resolve_path=RESOLVE_PATH,
        invalid_response=INVALID_REQUEST_RESPONSE,
    )

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

    @app.exception_handler(TrustedAuthorityUnavailable)
    async def trusted_authority_unavailable(
        request: Request,
        error: TrustedAuthorityUnavailable,
    ) -> JSONResponse:
        del request, error
        return JSONResponse(SERVICE_UNAVAILABLE_RESPONSE, status_code=503)

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
            document = loads(
                await request.body(),
                object_pairs_hook=_reject_duplicate_json_object_keys,
                parse_constant=_reject_non_finite_json_number,
            )
            enforce_json_nesting(
                document,
                maximum_depth=transport_profile.max_json_nesting_depth,
            )
        except (ValueError, UnicodeDecodeError, RecursionError):
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
        RESOLVE_PATH,
        status_code=200,
        response_model=ResolvedWire,
        response_model_by_alias=True,
        dependencies=[Depends(require_closed_json_transport)],
        responses={
            400: {
                "model": InvalidRequestWire,
                "description": (
                    "The request transport syntax, media type, or active "
                    "resource profile is invalid."
                ),
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
            503: {
                "model": ServiceUnavailableWire,
                "description": "A required trusted authority is unavailable.",
            },
        },
    )
    def resolve_context(
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
                max_length=transport_profile.max_correlation_id_characters,
                pattern=r".*\S.*",
            ),
        ] = None,
    ) -> JSONResponse:
        """Map one authenticated Acquire to the single sealed Runtime entry."""

        request_id = context_request_id or request_id_factory()
        received_at = clock()
        try:
            organization_verification = (
                selected_organization_authority.verify_existing(
                    authentication,
                    request_id=request_id,
                    verified_at=received_at,
                )
            )
            membership_identity = MembershipIdentity(
                organization_id=UUID(authentication.organization_ref),
                user_id=UUID(authentication.user_ref),
                membership_id=UUID(authentication.membership_ref),
                membership_version=authentication.membership_version,
                principal_ref=authentication.principal_ref,
                request_id=request_id,
                authentication_binding_ref=(
                    authentication.authentication_binding_ref
                ),
                checked_at=received_at,
            )
        except (OrganizationVerificationRejected, TypeError, ValueError):
            raise TransportAuthenticationFailed from None
        try:
            with selected_membership_authority.current_user_actor(
                membership_identity
            ) as current_membership_verification:
                try:
                    scope_identity = ScopeAuthorityIdentity(
                        organization_id=(
                            current_membership_verification.organization_id
                        ),
                        user_id=current_membership_verification.user_id,
                        membership_id=(
                            current_membership_verification.membership_id
                        ),
                        membership_version=(
                            current_membership_verification.membership_version
                        ),
                        principal_ref=current_membership_verification.principal_ref,
                        agent_version_ref=authentication.agent_version_ref,
                        purpose=DIRECT_ACQUIRE_PURPOSE,
                        request_id=current_membership_verification.request_id,
                        authentication_binding_ref=(
                            current_membership_verification.authentication_binding_ref
                        ),
                        checked_at=current_membership_verification.checked_at,
                    )
                except (TypeError, ValueError):
                    raise TransportAuthenticationFailed from None
                with selected_scope_authority.current_scope(
                    scope_identity
                ) as scope_snapshot:
                    try:
                        invocation = _construct_authenticated_http_invocation(
                            request_id=request_id,
                            authenticated_organization_ref=(
                                authentication.organization_ref
                            ),
                            organization_verification=organization_verification,
                            user_ref=authentication.user_ref,
                            principal_ref=authentication.principal_ref,
                            membership_ref=authentication.membership_ref,
                            membership_version=authentication.membership_version,
                            current_membership_verification=(
                                current_membership_verification
                            ),
                            agent_version_ref=authentication.agent_version_ref,
                            authenticated_application_ref=(
                                authentication.authenticated_application_ref
                            ),
                            authentication_binding_ref=(
                                authentication.authentication_binding_ref
                            ),
                            trusted_purpose=DIRECT_ACQUIRE_PURPOSE,
                            received_at=received_at,
                            trusted_scope_snapshot=scope_snapshot,
                        )
                    except InvalidTrustedScopeSnapshot:
                        raise TrustedAuthorityUnavailable from None
                    except (TypeError, ValueError):
                        raise TransportAuthenticationFailed from None
                    if invocation_observer is not None:
                        invocation_observer(invocation)
                    delivery_context = _construct_direct_delivery_context(
                        purpose=DIRECT_ACQUIRE_PURPOSE,
                        authenticated_application_ref=(
                            authentication.authenticated_application_ref
                        ),
                        delivery_binding_ref=(
                            authentication.authentication_binding_ref
                        ),
                        established_at=invocation.received_at,
                    )
                    request = _acquire_from_wire(body)
                    outcome = selected_runtime.resolve(
                        invocation,
                        delivery_context,
                        request,
                    )
                    response = _resolved_to_wire(outcome)
                    if resolution_observer is not None:
                        resolution_observer(outcome)
                    return JSONResponse(
                        response.model_dump(mode="json", by_alias=True),
                        status_code=200,
                        headers={
                            "Cache-Control": "no-store",
                            "X-Context-Request-Id": invocation.request_id,
                        },
                    )
        except MembershipNotCurrent as error:
            if type(error) is not MembershipNotCurrent:
                raise TrustedAuthorityUnavailable from None
            if membership_rejection_observer is not None:
                membership_rejection_observer(error.audit_receipt)
            raise TransportAuthenticationFailed from None
        except MembershipAuthorityUnavailable:
            raise TrustedAuthorityUnavailable from None
        except ScopeAuthorityUnavailable:
            raise TrustedAuthorityUnavailable from None
        except InvalidTrustedScopeSnapshot:
            raise TrustedAuthorityUnavailable from None

    return app


def _acquire_from_wire(body: AcquireWire) -> Acquire:
    package_budget = None
    if body.packageBudget is not None:
        package_budget = PackageBudgetRequest(
            max_tokens=body.packageBudget.maxTokens,
            max_provider_calls=body.packageBudget.maxProviderCalls,
            max_cost_microunits=body.packageBudget.maxCostMicrounits,
            max_elapsed_ms=body.packageBudget.maxElapsedMs,
        )
    narrowing = None
    if body.requestNarrowing is not None:
        narrowing = RequestNarrowing(
            source_refs=body.requestNarrowing.sourceRefs,
            resource_refs=body.requestNarrowing.resourceRefs,
        )
    return Acquire(
        need=ContextNeed(query=body.need.query),
        package_budget=package_budget,
        narrowing=narrowing,
    )


def _resolved_to_wire(outcome: Resolved) -> ResolvedWire:
    package = outcome.package
    return ResolvedWire(
        kind=outcome.kind,
        package=ContextPackageWire(
            organizationRef=package.organization_ref,
            purpose=package.purpose,
            ttlSeconds=package.ttl_seconds,
            asOf=package.as_of,
            expiresAt=package.expires_at,
            decisionRef=package.decision_ref,
            blocks=package.blocks,
            evidence=package.evidence,
            gaps=package.gaps,
            budgetUsage=BudgetUsageWire(
                tokens=cast(Literal[0], package.budget_usage.tokens),
                providerCalls=cast(
                    Literal[0], package.budget_usage.provider_calls
                ),
                costMicrounits=cast(
                    Literal[0], package.budget_usage.cost_microunits
                ),
                elapsedMs=cast(Literal[0], package.budget_usage.elapsed_ms),
            ),
            coverage=CoverageWire(
                status=cast(Literal["empty"], package.coverage.status),
                reason=cast(
                    Literal["no_authorized_evidence"],
                    package.coverage.reason,
                ),
            ),
        ),
    )


app = create_app()
