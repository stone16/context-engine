"""Distinct signed read-only ContextAccessTicket and bounded test Provider seam."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, NoReturn, Protocol
from uuid import UUID

from engine.runtime._ticket_signing import (
    _EXPECTED_DECODING_ERRORS,
    TicketSigningKeyring,
    _generate_nonce,
    _mint_signed_ticket,
    _nonce_document,
    _parse_nonce,
    _parse_timestamp,
    _parse_uuid,
    _require_identifier,
    _require_opaque_ticket_value,
    _require_positive_bigint,
    _require_utc,
    _require_uuid,
    _timestamp,
    _verify_signed_ticket,
)
from engine.runtime.construction import PolicyEpochGate
from engine.runtime.policy_epoch import PolicyEpochAuthorityUnavailable
from engine.runtime.ticket_identity import (
    TicketExecutionIdentity,
    _require_active_ticket_execution_identity,
)
from engine.runtime.ticket_rejection import TicketNotAvailable

CONTEXT_ACCESS_OPERATION: Final = "synthetic.provider.read"
CONTEXT_READ_AUDIENCE_PREFIX: Final = "context-read:"
_DOMAIN: Final = "context-engine.context-access-ticket"
_TOKEN_TYPE: Final = "CE-ContextAccessTicket"
_DEFAULT_TTL_SECONDS: Final = 60
_MAX_TTL_SECONDS: Final = 300
_CLAIM_FIELDS: Final = frozenset(
    {
        "actor_principal_ref",
        "agent_version_ref",
        "audience",
        "expires_at",
        "issued_at",
        "nonce",
        "operation",
        "organization_id",
        "policy_epoch",
        "provider_ref",
        "purpose",
        "signing_key_version",
        "subject_membership_id",
        "subject_membership_version",
        "subject_user_id",
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True, slots=True, init=False)
class ContextAccessTicket:
    """Opaque signed read ticket; not interchangeable with ActionTicket."""

    _value: str = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "ContextAccessTicket can only be constructed by its issuer or "
            "validated deserializer"
        )

    @staticmethod
    def deserialize(
        value: str,
        *,
        keyring: TicketSigningKeyring,
    ) -> ContextAccessTicket:
        """Validate the read protocol namespace before creating its nominal type."""

        if type(keyring) is not TicketSigningKeyring:
            raise TypeError("keyring must be TicketSigningKeyring")
        try:
            document = _verify_signed_ticket(
                value,
                keyring,
                domain=_DOMAIN,
                token_type=_TOKEN_TYPE,
                claim_fields=_CLAIM_FIELDS,
            )
            _claims_from_document(document)
        except _EXPECTED_DECODING_ERRORS:
            raise TicketNotAvailable from None
        return _construct_context_access_ticket(value)

    def __str__(self) -> str:
        return "<ContextAccessTicket redacted>"

    def serialize(self) -> str:
        return self._value

    def __reduce__(self) -> NoReturn:
        raise TypeError("ContextAccessTicket is not serializable")


@dataclass(frozen=True, slots=True)
class _ContextAccessClaims:
    signing_key_version: int = field(repr=False)
    organization_id: UUID = field(repr=False)
    subject_user_id: UUID = field(repr=False)
    subject_membership_id: UUID = field(repr=False)
    subject_membership_version: int = field(repr=False)
    actor_principal_ref: str = field(repr=False)
    agent_version_ref: str = field(repr=False)
    purpose: str = field(repr=False)
    policy_epoch: int = field(repr=False)
    provider_ref: str = field(repr=False)
    audience: str = field(repr=False)
    issued_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)
    nonce: bytes = field(repr=False)

    def __reduce__(self) -> NoReturn:
        raise TypeError("ContextAccessTicket claims are not serializable")


def _claims_document(claims: _ContextAccessClaims) -> dict[str, object]:
    return {
        "actor_principal_ref": claims.actor_principal_ref,
        "agent_version_ref": claims.agent_version_ref,
        "audience": claims.audience,
        "expires_at": _timestamp(claims.expires_at),
        "issued_at": _timestamp(claims.issued_at),
        "nonce": _nonce_document(claims.nonce),
        "operation": CONTEXT_ACCESS_OPERATION,
        "organization_id": str(claims.organization_id),
        "policy_epoch": claims.policy_epoch,
        "provider_ref": claims.provider_ref,
        "purpose": claims.purpose,
        "signing_key_version": claims.signing_key_version,
        "subject_membership_id": str(claims.subject_membership_id),
        "subject_membership_version": claims.subject_membership_version,
        "subject_user_id": str(claims.subject_user_id),
    }


def _claims_from_document(document: Mapping[str, object]) -> _ContextAccessClaims:
    if document["operation"] != CONTEXT_ACCESS_OPERATION:
        raise ValueError
    claims = _ContextAccessClaims(
        signing_key_version=_require_positive_bigint(
            "signing key version", document["signing_key_version"]
        ),
        organization_id=_parse_uuid(document["organization_id"]),
        subject_user_id=_parse_uuid(document["subject_user_id"]),
        subject_membership_id=_parse_uuid(document["subject_membership_id"]),
        subject_membership_version=_require_positive_bigint(
            "subject Membership version", document["subject_membership_version"]
        ),
        actor_principal_ref=_require_identifier(
            "actor Principal", document["actor_principal_ref"], maximum_length=256
        ),
        agent_version_ref=_require_identifier(
            "Agent version", document["agent_version_ref"], maximum_length=256
        ),
        purpose=_require_identifier(
            "purpose", document["purpose"], maximum_length=256
        ),
        policy_epoch=_require_positive_bigint(
            "Policy Epoch", document["policy_epoch"]
        ),
        provider_ref=_require_identifier(
            "Provider", document["provider_ref"], maximum_length=128
        ),
        audience=_require_identifier(
            "read audience", document["audience"], maximum_length=256
        ),
        issued_at=_parse_timestamp(document["issued_at"]),
        expires_at=_parse_timestamp(document["expires_at"]),
        nonce=_parse_nonce(document["nonce"]),
    )
    if not timedelta(0) < claims.expires_at - claims.issued_at <= timedelta(
        seconds=_MAX_TTL_SECONDS
    ):
        raise ValueError
    return claims


def _construct_context_access_ticket(value: str) -> ContextAccessTicket:
    ticket = object.__new__(ContextAccessTicket)
    object.__setattr__(
        ticket,
        "_value",
        _require_opaque_ticket_value("ContextAccessTicket", value),
    )
    return ticket


def _read_audience(provider_ref: str) -> str:
    provider = _require_identifier("Provider", provider_ref, maximum_length=128)
    return f"{CONTEXT_READ_AUDIENCE_PREFIX}{provider}"


class ContextAccessTicketIssuer:
    """Server-side issuer bound to one synthetic read Provider audience."""

    def __init__(
        self,
        *,
        keyring: TicketSigningKeyring,
        organization_id: UUID,
        provider_ref: str,
        clock: Callable[[], datetime] = _utc_now,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        nonce_factory: Callable[[], bytes] = _generate_nonce,
    ) -> None:
        if type(keyring) is not TicketSigningKeyring:
            raise TypeError("keyring must be TicketSigningKeyring")
        if not callable(clock) or not callable(nonce_factory):
            raise TypeError("ticket issuer clock and nonce factory must be callable")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL_SECONDS:
            raise ValueError("ticket TTL must be a bounded positive integer")
        self._keyring = keyring
        self._organization_id = _require_uuid(
            "configured Organization", organization_id
        )
        self._provider_ref = _require_identifier(
            "Provider", provider_ref, maximum_length=128
        )
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._nonce_factory = nonce_factory

    def issue(self, identity: TicketExecutionIdentity) -> ContextAccessTicket:
        _require_active_ticket_execution_identity(identity)
        if identity.organization_id != self._organization_id:
            raise ValueError("ticket identity does not match configured Organization")
        issued_at = _require_utc("ticket issuance time", self._clock())
        claims = _ContextAccessClaims(
            signing_key_version=self._keyring.active_version,
            organization_id=identity.organization_id,
            subject_user_id=identity.subject_user_id,
            subject_membership_id=identity.subject_membership_id,
            subject_membership_version=identity.subject_membership_version,
            actor_principal_ref=identity.actor_principal_ref,
            agent_version_ref=identity.agent_version_ref,
            purpose=identity.purpose,
            policy_epoch=identity.policy_epoch,
            provider_ref=self._provider_ref,
            audience=_read_audience(self._provider_ref),
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=self._ttl_seconds),
            nonce=self._nonce_factory(),
        )
        return _construct_context_access_ticket(
            _mint_signed_ticket(
                self._keyring,
                domain=_DOMAIN,
                token_type=_TOKEN_TYPE,
                claims=_claims_document(claims),
            )
        )


class SyntheticReadProvider(Protocol):
    """Issue #18-only read effect; not the production ContextProvider seam."""

    def read(self, *, organization_id: UUID, provider_ref: str) -> None: ...


@dataclass(frozen=True, slots=True)
class SyntheticReadReceipt:
    effect_count: Literal[1] = 1


class ContextAccessTicketReadHandler:
    """Structurally read-only handler for one configured synthetic Provider."""

    def __init__(
        self,
        *,
        keyring: TicketSigningKeyring,
        organization_id: UUID,
        provider_ref: str,
        provider: SyntheticReadProvider,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if type(keyring) is not TicketSigningKeyring:
            raise TypeError("keyring must be TicketSigningKeyring")
        if not callable(getattr(provider, "read", None)) or not callable(clock):
            raise TypeError("read handler requires a Provider and clock")
        self._keyring = keyring
        self._organization_id = _require_uuid(
            "configured Organization", organization_id
        )
        self._provider_ref = _require_identifier(
            "Provider", provider_ref, maximum_length=128
        )
        self._provider = provider
        self._clock = clock

    def read(
        self,
        *,
        ticket: ContextAccessTicket,
        identity: TicketExecutionIdentity,
    ) -> SyntheticReadReceipt:
        if type(ticket) is not ContextAccessTicket:
            raise TicketNotAvailable
        try:
            _require_active_ticket_execution_identity(identity)
            checked_at = _require_utc("ticket validation time", self._clock())
            document = _verify_signed_ticket(
                ticket._value,
                self._keyring,
                domain=_DOMAIN,
                token_type=_TOKEN_TYPE,
                claim_fields=_CLAIM_FIELDS,
            )
            claims = _claims_from_document(document)
            if (
                claims.organization_id != identity.organization_id
                or claims.organization_id != self._organization_id
                or claims.subject_user_id != identity.subject_user_id
                or claims.subject_membership_id != identity.subject_membership_id
                or claims.subject_membership_version
                != identity.subject_membership_version
                or claims.actor_principal_ref != identity.actor_principal_ref
                or claims.agent_version_ref != identity.agent_version_ref
                or claims.purpose != identity.purpose
                or claims.policy_epoch != identity.policy_epoch
                or claims.provider_ref != self._provider_ref
                or claims.audience != _read_audience(self._provider_ref)
                or checked_at < claims.issued_at
                or checked_at >= claims.expires_at
            ):
                raise ValueError
            if not PolicyEpochGate().is_current(
                identity.policy_epoch_verification
            ):
                raise ValueError
        except (*_EXPECTED_DECODING_ERRORS, PolicyEpochAuthorityUnavailable):
            raise TicketNotAvailable from None
        self._provider.read(
            organization_id=identity.organization_id,
            provider_ref=self._provider_ref,
        )
        return SyntheticReadReceipt()
