"""Explicit local-only dogfood composition; absent configuration is reject-all."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from fastapi import FastAPI

from adapters.embeddings import DeterministicEmbeddingTwin
from adapters.http.authentication import (
    DogfoodAuthenticator,
    VerifiedAuthenticationContext,
)
from adapters.http.organization_authority import DogfoodOrganizationAuthority
from adapters.http.scope_authority import DogfoodFileScopeAuthority
from adapters.pgvector import PostgreSQLVectorCandidateIndex
from applications.file_root_configuration import (
    WORKER_FILE_ROOTS_ENV,
    file_roots,
)
from applications.operator_authentication import (
    CONTROL_OPERATOR_SECRET_ENV,
    LocalOperatorConfiguration,
    LocalOperatorConfigurationUnavailable,
)
from engine.control import MinimalUiControlGate
from engine.persistence import (
    DatabaseConfigurationError,
    DatabasePurpose,
    PostgreSQLMembershipAuthority,
    create_database_engine,
    load_database_configuration,
)
from engine.persistence.membership_context import (
    MembershipAuthorityUnavailable,
    MembershipIdentity,
    MembershipNotCurrent,
)
from engine.runtime import Runtime
from engine.runtime.citation import PRIVATE_FILE_CITATION_OPEN_PROFILE
from engine.runtime.construction import required_kernel_dependencies
from engine.runtime.package_digest import QueryDigestKeyring
from engine.runtime.release_lineage import (
    DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1,
    DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1,
    ActiveReleaseUnavailable,
)

DOGFOOD_COMPOSITION_ENV = "CONTEXT_ENGINE_API_COMPOSITION"
DOGFOOD_COMPOSITION_VALUE = "dogfood-local-v1"
DOGFOOD_SECRET_ENV = "CONTEXT_ENGINE_DOGFOOD_SECRET"
DOGFOOD_ORGANIZATION_ENV = "CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID"
DOGFOOD_USER_ENV = "CONTEXT_ENGINE_DOGFOOD_USER_ID"
DOGFOOD_MEMBERSHIP_ENV = "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID"
DOGFOOD_MEMBERSHIP_VERSION_ENV = "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION"
DOGFOOD_PRINCIPAL_ENV = "CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF"
DOGFOOD_AGENT_ENV = "CONTEXT_ENGINE_DOGFOOD_AGENT_VERSION_REF"
DOGFOOD_APPLICATION_ENV = "CONTEXT_ENGINE_DOGFOOD_APPLICATION_REF"
DOGFOOD_BINDING_ENV = "CONTEXT_ENGINE_DOGFOOD_AUTHENTICATION_BINDING_REF"
DOGFOOD_EMBEDDING_PROVIDER_ENV = "CONTEXT_ENGINE_DOGFOOD_EMBEDDING_PROVIDER"
DOGFOOD_EMBEDDING_PROVIDER_VALUE = "deterministic-twin-v1"
DOGFOOD_FILE_IMPORT_RECEIVER_ENV = "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID"

_QUERY_DIGEST_DERIVATION_DOMAIN = b"context-engine.dogfood.query-digest.v1\x00"
_UI_PREVIEW_DERIVATION_DOMAIN = b"context-engine.dogfood.ui-preview.v1\x00"


class DogfoodConfigurationUnavailable(ValueError):
    """Local composition is incomplete, ambiguous, or attempts a wider carrier."""


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if (
        value is None
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise DogfoodConfigurationUnavailable(
            "dogfood API configuration is unavailable"
        )
    return value


@dataclass(frozen=True, slots=True)
class DogfoodConfiguration:
    """One immutable local identity and network-free retrieval composition."""

    secret: str = field(repr=False)
    organization_id: UUID
    user_id: UUID
    membership_id: UUID
    membership_version: int
    principal_ref: str
    agent_version_ref: str
    application_ref: str
    authentication_binding_ref: str
    embedding_provider: str

    def __post_init__(self) -> None:
        if (
            type(self.secret) is not str
            or len(self.secret.encode("utf-8")) < 32
            or self.secret != self.secret.strip()
            or any(character.isspace() for character in self.secret)
        ):
            raise DogfoodConfigurationUnavailable(
                "dogfood API configuration is unavailable"
            )
        for field_name in ("organization_id", "user_id", "membership_id"):
            if type(getattr(self, field_name)) is not UUID:
                raise DogfoodConfigurationUnavailable(
                    "dogfood API configuration is unavailable"
                )
        if type(
            self.membership_version
        ) is not int or not 1 <= self.membership_version < (1 << 63):
            raise DogfoodConfigurationUnavailable(
                "dogfood API configuration is unavailable"
            )
        for field_name in (
            "principal_ref",
            "agent_version_ref",
            "application_ref",
            "authentication_binding_ref",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value.isspace():
                raise DogfoodConfigurationUnavailable(
                    "dogfood API configuration is unavailable"
                )
        if self.embedding_provider != DOGFOOD_EMBEDDING_PROVIDER_VALUE:
            raise DogfoodConfigurationUnavailable(
                "external dogfood query embedding is NOT_ACTIVE"
            )

    @classmethod
    def load(cls, environment: Mapping[str, str]) -> DogfoodConfiguration:
        try:
            membership_version_text = _required(
                environment,
                DOGFOOD_MEMBERSHIP_VERSION_ENV,
            )
            if not membership_version_text.isdecimal():
                raise ValueError
            return cls(
                secret=_required(environment, DOGFOOD_SECRET_ENV),
                organization_id=UUID(_required(environment, DOGFOOD_ORGANIZATION_ENV)),
                user_id=UUID(_required(environment, DOGFOOD_USER_ENV)),
                membership_id=UUID(_required(environment, DOGFOOD_MEMBERSHIP_ENV)),
                membership_version=int(membership_version_text),
                principal_ref=_required(environment, DOGFOOD_PRINCIPAL_ENV),
                agent_version_ref=_required(environment, DOGFOOD_AGENT_ENV),
                application_ref=_required(environment, DOGFOOD_APPLICATION_ENV),
                authentication_binding_ref=_required(
                    environment,
                    DOGFOOD_BINDING_ENV,
                ),
                embedding_provider=_required(
                    environment,
                    DOGFOOD_EMBEDDING_PROVIDER_ENV,
                ),
            )
        except (TypeError, ValueError):
            raise DogfoodConfigurationUnavailable(
                "dogfood API configuration is unavailable"
            ) from None

    def authentication(self) -> VerifiedAuthenticationContext:
        return VerifiedAuthenticationContext(
            organization_ref=str(self.organization_id),
            user_ref=str(self.user_id),
            principal_ref=self.principal_ref,
            membership_ref=str(self.membership_id),
            membership_version=self.membership_version,
            agent_version_ref=self.agent_version_ref,
            authenticated_application_ref=self.application_ref,
            authentication_binding_ref=self.authentication_binding_ref,
        )

    def query_digest_keyring(self) -> QueryDigestKeyring:
        return QueryDigestKeyring(
            active_version=1,
            keys={
                1: sha256(
                    _QUERY_DIGEST_DERIVATION_DOMAIN + self.secret.encode("utf-8")
                ).digest()
            },
        )


def create_dogfood_app(
    configuration: DogfoodConfiguration,
    environment: Mapping[str, str],
    *,
    host: str,
) -> FastAPI:
    """Compose the exact local carrier; every dependency remains sealed."""

    if type(configuration) is not DogfoodConfiguration:
        raise TypeError("dogfood API configuration is required")
    if host not in {"127.0.0.1", "::1"}:
        raise DogfoodConfigurationUnavailable(
            "dogfood API configuration is unavailable"
        )
    database_configuration = load_database_configuration(
        DatabasePurpose.API_RUNTIME,
        environment,
    )
    runtime_engine = create_database_engine(database_configuration)
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=PostgreSQLVectorCandidateIndex(DeterministicEmbeddingTwin()),
        citation_profile=PRIVATE_FILE_CITATION_OPEN_PROFILE,
        query_digest_keyring=configuration.query_digest_keyring(),
    )
    membership_authority = PostgreSQLMembershipAuthority(runtime_engine)
    try:
        with membership_authority.current_user_actor(
            MembershipIdentity(
                organization_id=configuration.organization_id,
                user_id=configuration.user_id,
                membership_id=configuration.membership_id,
                membership_version=configuration.membership_version,
                principal_ref=configuration.principal_ref,
                request_id="dogfood-composition-activation",
                authentication_binding_ref=(configuration.authentication_binding_ref),
                checked_at=datetime.now(UTC),
            )
        ) as current_user_actor:
            release = current_user_actor.active_runtime_release
            if (
                release is None
                or release.organization_id != configuration.organization_id
                or release.index_profile_ref != DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1
                or release.index_profile_digest
                != DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1
                or not release.active_revision_refs
            ):
                raise DogfoodConfigurationUnavailable(
                    "dogfood API configuration is unavailable"
                )
    except DogfoodConfigurationUnavailable:
        runtime_engine.dispose()
        raise
    except (
        ActiveReleaseUnavailable,
        MembershipAuthorityUnavailable,
        MembershipNotCurrent,
    ):
        runtime_engine.dispose()
        raise DogfoodConfigurationUnavailable(
            "dogfood API configuration is unavailable"
        ) from None

    from adapters.http.app import (
        DIRECT_ACQUIRE_PURPOSE,
        DIRECT_CITATION_PURPOSE,
        _construct_runtime_delivery_activation,
        create_app,
    )
    from adapters.http.ui_api import PostgreSQLUiApi

    roots = None
    control_engine = None
    control_authority = None
    control_gate = None

    def ui_clock() -> datetime:
        return datetime.now(UTC)

    try:
        operator_configuration = (
            LocalOperatorConfiguration.load(environment)
            if environment.get(CONTROL_OPERATOR_SECRET_ENV) is not None
            else None
        )
        receiver_id = None
        if operator_configuration is not None:
            control_engine = create_database_engine(
                load_database_configuration(
                    DatabasePurpose.CONTROL_PLANE,
                    environment,
                )
            )
            if environment.get(WORKER_FILE_ROOTS_ENV) is not None:
                roots = file_roots(environment)
            raw_receiver = environment.get(DOGFOOD_FILE_IMPORT_RECEIVER_ENV)
            receiver_id = None if raw_receiver is None else UUID(raw_receiver)
            control_authority = operator_configuration.authorities(
                clock=ui_clock
            ).control
            control_gate = MinimalUiControlGate(control_authority, clock=ui_clock)
    except (
        DatabaseConfigurationError,
        LocalOperatorConfigurationUnavailable,
        TypeError,
        ValueError,
    ):
        runtime_engine.dispose()
        if control_engine is not None:
            control_engine.dispose()
        if roots is not None:
            roots.close()
        raise DogfoodConfigurationUnavailable(
            "dogfood UI configuration is unavailable"
        ) from None

    app = create_app(
        authenticator=DogfoodAuthenticator(
            secret=configuration.secret,
            authentication=configuration.authentication(),
        ),
        organization_authority=DogfoodOrganizationAuthority(
            configuration.organization_id
        ),
        membership_authority=membership_authority,
        scope_authority=DogfoodFileScopeAuthority(
            organization_id=configuration.organization_id,
            principal_ref=configuration.principal_ref,
            agent_version_ref=configuration.agent_version_ref,
            purposes=frozenset(
                {DIRECT_ACQUIRE_PURPOSE, DIRECT_CITATION_PURPOSE}
            ),
        ),
        runtime=runtime,
        runtime_delivery_activation=_construct_runtime_delivery_activation(),
        ui_bearer_token=configuration.secret,
        ui_control_authority=control_authority,
        ui_api=PostgreSQLUiApi(
            membership_authority,
            control_engine,
            feedback_engine=runtime_engine,
            preview_key=sha256(
                _UI_PREVIEW_DERIVATION_DOMAIN + configuration.secret.encode("utf-8")
            ).digest(),
            control_gate=control_gate,
            roots=roots,
            file_import_service_principal_id=receiver_id,
            clock=ui_clock,
        ),
    )
    app.add_event_handler("shutdown", runtime_engine.dispose)
    if control_engine is not None:
        app.add_event_handler("shutdown", control_engine.dispose)
    if roots is not None:
        app.add_event_handler("shutdown", roots.close)
    return app


def create_served_app(
    environment: Mapping[str, str] | None = None,
    *,
    host: str | None = None,
) -> FastAPI:
    """Select reject-all by absence; reject partial or unsupported opt-in."""

    from adapters.http.app import create_app

    source = os.environ if environment is None else environment
    composition = source.get(DOGFOOD_COMPOSITION_ENV)
    if composition is None:
        return create_app()
    if composition != DOGFOOD_COMPOSITION_VALUE:
        raise DogfoodConfigurationUnavailable(
            "API composition configuration is unavailable"
        )
    if host is None:
        raise DogfoodConfigurationUnavailable(
            "dogfood API configuration is unavailable"
        )
    return create_dogfood_app(
        DogfoodConfiguration.load(source),
        source,
        host=host,
    )
