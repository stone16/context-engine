"""Explicit local-only operator authentication composition."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from engine.control import (
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    VerifiedControlOperatorIdentity,
)
from engine.learning import (
    ReleaseOperatorAuthenticationRejected,
    ReleaseOperatorAuthority,
    VerifiedReleaseOperatorIdentity,
    release_authority_digest,
)

CONTROL_OPERATOR_SECRET_ENV = "CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET"
RELEASE_OPERATOR_SECRET_ENV = "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET"
OPERATOR_ORGANIZATION_ENV = "CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID"
CONTROL_OPERATOR_OPERATIONS_ENV = "CONTEXT_ENGINE_CONTROL_OPERATOR_OPERATIONS"
DOGFOOD_SECRET_ENV = "CONTEXT_ENGINE_DOGFOOD_SECRET"
WORKER_SECRET_ENV = "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX"
RELEASE_OPERATOR_SECRET_FINGERPRINT_ENV = (
    "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET_SHA256"
)
DOGFOOD_SECRET_FINGERPRINT_ENV = "CONTEXT_ENGINE_DOGFOOD_SECRET_SHA256"
LOCAL_CONTROL_OPERATOR_ENVIRONMENT_VARIABLES = frozenset(
    {
        CONTROL_OPERATOR_SECRET_ENV,
        OPERATOR_ORGANIZATION_ENV,
        CONTROL_OPERATOR_OPERATIONS_ENV,
    }
)
OPERATOR_ENVIRONMENT_VARIABLES = frozenset(
    {
        CONTROL_OPERATOR_SECRET_ENV,
        RELEASE_OPERATOR_SECRET_ENV,
        OPERATOR_ORGANIZATION_ENV,
        CONTROL_OPERATOR_OPERATIONS_ENV,
        DOGFOOD_SECRET_ENV,
        WORKER_SECRET_ENV,
    }
)
LOCAL_OPERATOR_TTL = timedelta(minutes=15)
LOCAL_RELEASE_GRANT_TTL = timedelta(days=30)
LOCAL_CONTROL_OPERATOR_REF = "operator:local-control:v1"
LOCAL_CONTROL_BINDING_REF = "binding:local-control:v1"
LOCAL_CONTROL_AUTHORITY_REF = "authority:local-control:v1"
LOCAL_RELEASE_OPERATOR_REF = "operator:local-release:v1"
LOCAL_RELEASE_BINDING_REF = "binding:local-release:v1"
LOCAL_RELEASE_AUTHORITY_REF = "authority:local-release:v1"


class LocalOperatorConfigurationUnavailable(ValueError):
    """The local operator composition is absent, partial, or unsafe."""

    def __init__(self) -> None:
        super().__init__("operator authentication rejected")


def local_secret_fingerprint(value: str) -> str:
    """Fingerprint one local secret for collision checks without delegating it."""

    if type(value) is not str or not value:
        raise LocalOperatorConfigurationUnavailable
    return hashlib.sha256(value.lower().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LocalControlOperatorConfiguration:
    """The routine Control identity without any release publication credential."""

    organization_id: UUID
    control_secret: bytes = field(repr=False)
    control_operations: frozenset[ControlOperation] = field(repr=False)

    @classmethod
    def load(
        cls,
        environment: Mapping[str, str],
    ) -> LocalControlOperatorConfiguration | None:
        configured = LOCAL_CONTROL_OPERATOR_ENVIRONMENT_VARIABLES.intersection(
            environment
        )
        if not configured:
            return None
        if configured != LOCAL_CONTROL_OPERATOR_ENVIRONMENT_VARIABLES:
            raise LocalOperatorConfigurationUnavailable
        try:
            raw_operations = environment[CONTROL_OPERATOR_OPERATIONS_ENV].split(",")
            if any(not value or value != value.strip() for value in raw_operations):
                raise ValueError
            operations = frozenset(ControlOperation(value) for value in raw_operations)
            if len(operations) != len(raw_operations):
                raise ValueError
            configuration = cls(
                organization_id=UUID(environment[OPERATOR_ORGANIZATION_ENV]),
                control_secret=_secret(environment[CONTROL_OPERATOR_SECRET_ENV]),
                control_operations=operations,
            )
            dogfood_secret = environment.get(DOGFOOD_SECRET_ENV)
            if dogfood_secret is not None and hmac.compare_digest(
                configuration.control_secret,
                _secret(dogfood_secret),
            ):
                raise LocalOperatorConfigurationUnavailable
            broader_names = OPERATOR_ENVIRONMENT_VARIABLES - (
                LOCAL_CONTROL_OPERATOR_ENVIRONMENT_VARIABLES | {DOGFOOD_SECRET_ENV}
            )
            if environment.keys() & broader_names:
                LocalOperatorConfiguration.load(environment)
            return configuration
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise LocalOperatorConfigurationUnavailable from None

    def authority(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> ControlOperatorAuthority:
        active_clock = clock or (lambda: datetime.now(UTC))
        return ControlOperatorAuthority(
            LocalControlOperatorAuthenticator(self, clock=active_clock),
            call_ttl=LOCAL_OPERATOR_TTL,
            clock=active_clock,
        )

    def __repr__(self) -> str:
        return "LocalControlOperatorConfiguration(<redacted>)"


def _secret(value: object) -> bytes:
    if (
        type(value) is not str
        or len(value.encode("utf-8")) < 32
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise LocalOperatorConfigurationUnavailable
    return value.encode("utf-8")


def _worker_secret(value: object) -> bytes:
    if type(value) is not str or len(value) != 64:
        raise LocalOperatorConfigurationUnavailable
    try:
        decoded = bytes.fromhex(value)
    except ValueError:
        raise LocalOperatorConfigurationUnavailable from None
    if len(decoded) != 32:
        raise LocalOperatorConfigurationUnavailable
    return decoded


@dataclass(frozen=True, slots=True)
class LocalOperatorConfiguration:
    """One fixed local Control identity and one separate release identity."""

    organization_id: UUID
    control_secret: bytes = field(repr=False)
    release_secret: bytes = field(repr=False)
    control_operations: frozenset[ControlOperation] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise LocalOperatorConfigurationUnavailable
        for value in (self.control_secret, self.release_secret):
            if type(value) is not bytes or len(value) < 32:
                raise LocalOperatorConfigurationUnavailable
        if hmac.compare_digest(self.control_secret, self.release_secret):
            raise LocalOperatorConfigurationUnavailable
        if (
            type(self.control_operations) is not frozenset
            or not self.control_operations
            or any(
                type(operation) is not ControlOperation
                for operation in self.control_operations
            )
        ):
            raise LocalOperatorConfigurationUnavailable

    @classmethod
    def load(
        cls,
        environment: Mapping[str, str],
    ) -> LocalOperatorConfiguration | None:
        configured = OPERATOR_ENVIRONMENT_VARIABLES.intersection(environment)
        if not configured:
            return None
        if configured != OPERATOR_ENVIRONMENT_VARIABLES:
            raise LocalOperatorConfigurationUnavailable
        try:
            raw_operations = environment[CONTROL_OPERATOR_OPERATIONS_ENV].split(",")
            if any(not value or value != value.strip() for value in raw_operations):
                raise ValueError
            operations = frozenset(ControlOperation(value) for value in raw_operations)
            if len(operations) != len(raw_operations):
                raise ValueError
            configuration = cls(
                organization_id=UUID(environment[OPERATOR_ORGANIZATION_ENV]),
                control_secret=_secret(environment[CONTROL_OPERATOR_SECRET_ENV]),
                release_secret=_secret(environment[RELEASE_OPERATOR_SECRET_ENV]),
                control_operations=operations,
            )
            configured_secrets = (
                configuration.control_secret,
                configuration.release_secret,
                _secret(environment[DOGFOOD_SECRET_ENV]),
                _worker_secret(environment[WORKER_SECRET_ENV]),
            )
            for index, secret in enumerate(configured_secrets):
                if any(
                    hmac.compare_digest(secret, other)
                    for other in configured_secrets[index + 1 :]
                ):
                    raise LocalOperatorConfigurationUnavailable
            return configuration
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise LocalOperatorConfigurationUnavailable from None

    def authorities(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> LocalOperatorAuthorities:
        active_clock = clock or (lambda: datetime.now(UTC))
        return LocalOperatorAuthorities(
            control=ControlOperatorAuthority(
                LocalControlOperatorAuthenticator(self, clock=active_clock),
                call_ttl=LOCAL_OPERATOR_TTL,
                clock=active_clock,
            ),
            release=ReleaseOperatorAuthority(
                LocalReleaseOperatorAuthenticator(self, clock=active_clock),
                call_ttl=LOCAL_OPERATOR_TTL,
                clock=active_clock,
            ),
        )

    def __repr__(self) -> str:
        return "LocalOperatorConfiguration(<redacted>)"


@dataclass(frozen=True, slots=True)
class LocalOperatorAuthorities:
    """Separately scoped authorities constructed only after explicit opt-in."""

    control: ControlOperatorAuthority
    release: ReleaseOperatorAuthority


class LocalControlOperatorAuthenticator:
    """Constant-time verifier for one fixed local Control identity."""

    __slots__ = ("_configuration", "_clock")

    def __init__(
        self,
        configuration: LocalOperatorConfiguration | LocalControlOperatorConfiguration,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        if type(configuration) not in {
            LocalOperatorConfiguration,
            LocalControlOperatorConfiguration,
        }:
            raise TypeError("operator authentication rejected")
        if not callable(clock):
            raise TypeError("operator authentication rejected")
        self._configuration = configuration
        self._clock = clock

    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if type(opaque_credential) is not str:
            raise ControlOperatorAuthenticationRejected
        try:
            supplied = opaque_credential.encode("utf-8")
        except UnicodeEncodeError:
            raise ControlOperatorAuthenticationRejected from None
        if not hmac.compare_digest(
            supplied,
            self._configuration.control_secret,
        ):
            raise ControlOperatorAuthenticationRejected
        now = self._clock()
        return VerifiedControlOperatorIdentity(
            organization_id=self._configuration.organization_id,
            operator_ref=LOCAL_CONTROL_OPERATOR_REF,
            authentication_binding_ref=LOCAL_CONTROL_BINDING_REF,
            authority_ref=LOCAL_CONTROL_AUTHORITY_REF,
            allowed_operations=self._configuration.control_operations,
            valid_from=now,
            expires_at=now + LOCAL_OPERATOR_TTL,
        )

    def __repr__(self) -> str:
        return "LocalControlOperatorAuthenticator(<redacted>)"


class LocalReleaseOperatorAuthenticator:
    """Constant-time verifier for a separate fixed local release identity."""

    __slots__ = ("_configuration", "_clock")

    def __init__(
        self,
        configuration: LocalOperatorConfiguration,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        if type(configuration) is not LocalOperatorConfiguration:
            raise TypeError("operator authentication rejected")
        if not callable(clock):
            raise TypeError("operator authentication rejected")
        self._configuration = configuration
        self._clock = clock

    def authenticate(self, opaque_credential: str) -> VerifiedReleaseOperatorIdentity:
        if type(opaque_credential) is not str:
            raise ReleaseOperatorAuthenticationRejected
        try:
            supplied = opaque_credential.encode("utf-8")
        except UnicodeEncodeError:
            raise ReleaseOperatorAuthenticationRejected from None
        if not hmac.compare_digest(
            supplied,
            self._configuration.release_secret,
        ):
            raise ReleaseOperatorAuthenticationRejected
        now = self._clock()
        authority_digest = release_authority_digest(
            organization_id=self._configuration.organization_id,
            operator_ref=LOCAL_RELEASE_OPERATOR_REF,
            authentication_binding_ref=LOCAL_RELEASE_BINDING_REF,
            authority_ref=LOCAL_RELEASE_AUTHORITY_REF,
        )
        return VerifiedReleaseOperatorIdentity(
            organization_id=self._configuration.organization_id,
            operator_ref=LOCAL_RELEASE_OPERATOR_REF,
            authentication_binding_ref=LOCAL_RELEASE_BINDING_REF,
            authority_ref=LOCAL_RELEASE_AUTHORITY_REF,
            authority_digest=authority_digest,
            valid_from=now,
            expires_at=now + LOCAL_OPERATOR_TTL,
        )

    def __repr__(self) -> str:
        return "LocalReleaseOperatorAuthenticator(<redacted>)"
