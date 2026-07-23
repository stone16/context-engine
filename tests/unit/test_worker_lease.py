import base64
import hashlib
import hmac
import json
import pickle
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

import engine.supply as supply
from engine.supply import (
    FILE_IMPORT_WORKER_LEASE_OPERATION,
    WORKER_LEASE_OPERATION,
    WorkerLeaseClaims,
    WorkerLeaseCodec,
    WorkerLeaseKeyring,
    WorkerLeaseRejectionCategory,
    WorkerLeaseToken,
    WorkNotAvailable,
    generate_worker_lease_nonce,
    worker_lease_digest,
    worker_lease_nonce_digest,
)

ORGANIZATION_ID = UUID("0198ce9a-6cd1-7dc2-bff8-5aec4a3c48b1")
JOB_ID = UUID("0198ce9a-cf63-7170-ae97-aeea72c0af73")
NOW = datetime(2026, 7, 22, 10, 30, tzinfo=UTC)
KEY_VERSION = 7
SIGNING_KEY = bytes(range(32))
SERVICE_PRINCIPAL_ID = UUID("0198d158-5541-7f8b-a553-460c98ecfb67")


def _claims(**overrides: object) -> WorkerLeaseClaims:
    values: dict[str, object] = {
        "signing_key_version": KEY_VERSION,
        "organization_id": ORGANIZATION_ID,
        "job_id": JOB_ID,
        "service_principal_id": SERVICE_PRINCIPAL_ID,
        "workload": "supply.noop",
        "worker_audience": "context-engine:supply-worker",
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
        "nonce": bytes(range(32, 64)),
    }
    values.update(overrides)
    return WorkerLeaseClaims(**values)  # type: ignore[arg-type]


def _codec(*, version: int = KEY_VERSION) -> WorkerLeaseCodec:
    return WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=version, keys={version: SIGNING_KEY})
    )


def _verification_arguments(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "expected_organization_id": ORGANIZATION_ID,
        "expected_job_id": JOB_ID,
        "expected_service_principal_id": SERVICE_PRINCIPAL_ID,
        "expected_workload": "supply.noop",
        "expected_operation": WORKER_LEASE_OPERATION,
        "expected_worker_audience": "context-engine:supply-worker",
        "now": NOW,
    }
    values.update(overrides)
    return values


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _signed_token(header: bytes, payload: bytes) -> WorkerLeaseToken:
    encoded_header = _b64url(header)
    encoded_payload = _b64url(payload)
    signing_input = f"{encoded_header}.{encoded_payload}".encode()
    signature = hmac.digest(SIGNING_KEY, signing_input, "sha256")
    return WorkerLeaseToken(
        f"{encoded_header}.{encoded_payload}.{_b64url(signature)}"
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _decoded_token(
    token: WorkerLeaseToken,
) -> tuple[dict[str, object], dict[str, object]]:
    header, payload, _signature = token.serialize().split(".")
    decoded_header = json.loads(base64.urlsafe_b64decode(header + "=="))
    decoded_payload = json.loads(base64.urlsafe_b64decode(payload + "=="))
    return decoded_header, decoded_payload


def test_valid_lease_verifies_for_its_exact_job_and_worker_audience() -> None:
    keyring = WorkerLeaseKeyring(
        active_version=KEY_VERSION,
        keys={KEY_VERSION: SIGNING_KEY},
    )
    codec = WorkerLeaseCodec(keyring)

    token = codec.mint(
        WorkerLeaseClaims(
            signing_key_version=KEY_VERSION,
            organization_id=ORGANIZATION_ID,
            job_id=JOB_ID,
            service_principal_id=SERVICE_PRINCIPAL_ID,
            workload="supply.noop",
            worker_audience="context-engine:supply-worker",
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            nonce=bytes(range(32, 64)),
        )
    )

    claims = codec.verify(
        token,
        expected_organization_id=ORGANIZATION_ID,
        expected_job_id=JOB_ID,
        expected_service_principal_id=SERVICE_PRINCIPAL_ID,
        expected_workload="supply.noop",
        expected_operation="noop.complete",
        expected_worker_audience="context-engine:supply-worker",
        now=NOW,
    )

    assert claims.organization_id == ORGANIZATION_ID
    assert claims.job_id == JOB_ID
    assert claims.service_principal_id == SERVICE_PRINCIPAL_ID
    assert claims.workload == "supply.noop"
    assert claims.worker_audience == "context-engine:supply-worker"
    assert claims.operation == "noop.complete"
    assert claims.actor_kind == "service"
    assert claims.issued_at == NOW
    assert claims.expires_at == NOW + timedelta(minutes=5)


@pytest.mark.security_evidence(id="PROP-WORKER-LEASE-007", layer="property")
@pytest.mark.parametrize(
    "override",
    [
        {"expected_organization_id": UUID("0198d18d-e4ad-7f81-b2fa-ae31a998c74e")},
        {"expected_job_id": UUID("0198d18d-f6ce-755c-bcd9-24db2b87269d")},
        {
            "expected_service_principal_id": UUID(
                "0198d18e-2e9e-7857-8df8-5fe40b40a288"
            )
        },
        {"expected_workload": "supply.other"},
        {"expected_operation": "noop.prepare"},
        {"expected_worker_audience": "context-engine:other-worker"},
        {"now": NOW - timedelta(seconds=1)},
        {"now": NOW + timedelta(minutes=5)},
    ],
)
def test_wrong_binding_not_yet_valid_and_expired_are_generic(
    override: dict[str, object],
) -> None:
    codec = _codec()
    token = codec.mint(_claims())

    with pytest.raises(WorkNotAvailable, match="^work not available$") as rejected:
        codec.verify(token, **_verification_arguments(**override))  # type: ignore[arg-type]

    receipt = rejected.value.audit_receipt
    assert receipt.category is WorkerLeaseRejectionCategory.WORK_NOT_AVAILABLE
    assert receipt.lease_digest == hashlib.sha256(
        token.serialize().encode("ascii")
    ).hexdigest()
    assert {item.name for item in fields(receipt)} == {"category", "lease_digest"}


def test_unknown_signing_key_version_is_generic() -> None:
    token = _codec(version=8).mint(_claims(signing_key_version=8))

    with pytest.raises(WorkNotAvailable, match=r"^work not available$"):
        _codec().verify(token, **_verification_arguments())  # type: ignore[arg-type]


def _flip_one_ascii_bit(value: str) -> str:
    for index, character in enumerate(value):
        replacement = chr(ord(character) ^ 1)
        if replacement.isascii() and (replacement.isalnum() or replacement in "-_"):
            return f"{value[:index]}{replacement}{value[index + 1:]}"
    raise AssertionError("fixture has no base64url character with a safe one-bit peer")


@pytest.mark.parametrize("segment_index", [0, 1, 2])
def test_one_bit_header_payload_or_signature_tamper_is_generic(
    segment_index: int,
) -> None:
    codec = _codec()
    token = codec.mint(_claims())
    segments = token.serialize().split(".")
    segments[segment_index] = _flip_one_ascii_bit(segments[segment_index])
    tampered = WorkerLeaseToken(".".join(segments))

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        codec.verify(tampered, **_verification_arguments())  # type: ignore[arg-type]


def test_token_uses_one_exact_protected_header_and_fixed_claim_set() -> None:
    token = _codec().mint(_claims())

    header, payload = _decoded_token(token)

    assert header == {
        "alg": "HS256",
        "dom": "context-engine.worker-lease",
        "kid": 7,
        "typ": "CE-WorkerLease",
        "v": 1,
    }
    assert payload == {
        "actor_kind": "service",
        "expires_at": "2026-07-22T10:35:00Z",
        "issued_at": "2026-07-22T10:30:00Z",
        "job_id": str(JOB_ID),
        "nonce": "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8",
        "operation": "noop.complete",
        "organization_id": str(ORGANIZATION_ID),
        "service_principal_id": str(SERVICE_PRINCIPAL_ID),
        "signing_key_version": 7,
        "worker_audience": "context-engine:supply-worker",
        "workload": "supply.noop",
    }


def test_file_import_lease_uses_a_distinct_version_and_exact_source_binding() -> None:
    codec = _codec()
    token = codec.mint(
        _claims(
            workload="supply.file-import",
            operation=FILE_IMPORT_WORKER_LEASE_OPERATION,
            source_ref="source:handbook",
            lease_generation=1,
        )
    )

    header, payload = _decoded_token(token)

    assert header["v"] == 3
    assert payload["operation"] == "file.import"
    assert payload["source_ref"] == "source:handbook"
    assert payload["lease_generation"] == 1
    assert codec.verify(
        token,
        **_verification_arguments(
            expected_workload="supply.file-import",
            expected_operation=FILE_IMPORT_WORKER_LEASE_OPERATION,
            expected_source_ref="source:handbook",
        ),  # type: ignore[arg-type]
    ).source_ref == "source:handbook"


def test_file_import_lease_rejects_a_wrong_source_generically() -> None:
    codec = _codec()
    token = codec.mint(
        _claims(
            workload="supply.file-import",
            operation=FILE_IMPORT_WORKER_LEASE_OPERATION,
            source_ref="source:handbook",
            lease_generation=1,
        )
    )

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        codec.verify(
            token,
            **_verification_arguments(
                expected_workload="supply.file-import",
                expected_operation=FILE_IMPORT_WORKER_LEASE_OPERATION,
                expected_source_ref="source:other",
            ),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "opaque_value",
    [
        "only-one-segment",
        "two.segments",
        "too.many.token.segments",
        "not+base64url.payload.signature",
        "padded=.payload.signature",
    ],
)
def test_malformed_token_is_generic(opaque_value: str) -> None:
    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _codec().verify(
            WorkerLeaseToken(opaque_value),
            **_verification_arguments(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("document_kind", ["header", "payload"])
def test_validly_signed_duplicate_json_keys_are_rejected(document_kind: str) -> None:
    token = _codec().mint(_claims())
    header, payload = _decoded_token(token)
    duplicate_header = (
        _canonical(header)[:-1]
        + b',"kid":7}'
    )
    duplicate_payload = (
        _canonical(payload)[:-1]
        + b',"job_id":"0198ce9a-cf63-7170-ae97-aeea72c0af73"}'
    )
    signed = _signed_token(
        duplicate_header if document_kind == "header" else _canonical(header),
        duplicate_payload if document_kind == "payload" else _canonical(payload),
    )

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _codec().verify(signed, **_verification_arguments())  # type: ignore[arg-type]


@pytest.mark.parametrize("document_kind", ["header", "payload"])
def test_validly_signed_noncanonical_json_is_rejected(document_kind: str) -> None:
    token = _codec().mint(_claims())
    header, payload = _decoded_token(token)
    noncanonical_header = json.dumps(header, sort_keys=False).encode()
    noncanonical_payload = json.dumps(payload, sort_keys=False).encode()
    signed = _signed_token(
        noncanonical_header if document_kind == "header" else _canonical(header),
        noncanonical_payload if document_kind == "payload" else _canonical(payload),
    )

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _codec().verify(signed, **_verification_arguments())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "mutated_value"),
    [
        ("actor_kind", "user"),
        ("operation", "noop.prepare"),
        ("organization_id", "0198d18d-e4ad-7f81-b2fa-ae31a998c74e"),
        ("job_id", "0198d18d-f6ce-755c-bcd9-24db2b87269d"),
        ("service_principal_id", "0198d18e-2e9e-7857-8df8-5fe40b40a288"),
        ("workload", "supply.other"),
        ("worker_audience", "context-engine:other-worker"),
        ("signing_key_version", 8),
        ("issued_at", "2026-07-22T10:31:00Z"),
        ("expires_at", "2026-07-22T10:30:00Z"),
    ],
)
def test_validly_resigned_authority_claim_mutation_is_rejected(
    field_name: str,
    mutated_value: object,
) -> None:
    token = _codec().mint(_claims())
    header, payload = _decoded_token(token)
    payload[field_name] = mutated_value
    signed = _signed_token(_canonical(header), _canonical(payload))

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _codec().verify(signed, **_verification_arguments())  # type: ignore[arg-type]


def test_header_and_payload_signing_key_versions_must_match() -> None:
    token = _codec().mint(_claims())
    header, payload = _decoded_token(token)
    payload["signing_key_version"] = 8
    signed = _signed_token(_canonical(header), _canonical(payload))

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _codec().verify(signed, **_verification_arguments())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("document_kind", "field_name"),
    [("header", "v"), ("header", "kid"), ("payload", "signing_key_version")],
)
def test_json_boolean_is_never_accepted_as_an_integer_version(
    document_kind: str,
    field_name: str,
) -> None:
    token = _codec().mint(_claims())
    header, payload = _decoded_token(token)
    document = header if document_kind == "header" else payload
    document[field_name] = True
    signed = _signed_token(_canonical(header), _canonical(payload))

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        _codec().verify(signed, **_verification_arguments())  # type: ignore[arg-type]


def test_extra_or_missing_header_and_claim_fields_are_rejected() -> None:
    token = _codec().mint(_claims())
    header, payload = _decoded_token(token)
    extra_header = dict(header, extra="forbidden")
    missing_payload = dict(payload)
    del missing_payload["workload"]

    for signed in (
        _signed_token(_canonical(extra_header), _canonical(payload)),
        _signed_token(_canonical(header), _canonical(missing_payload)),
    ):
        with pytest.raises(WorkNotAvailable, match="^work not available$"):
            _codec().verify(signed, **_verification_arguments())  # type: ignore[arg-type]


def test_claims_and_token_are_immutable_opaque_and_not_serializable() -> None:
    claims = _claims()
    token = _codec().mint(claims)
    raw_token = token.serialize()
    secret_markers = (
        str(ORGANIZATION_ID),
        str(JOB_ID),
        str(SERVICE_PRINCIPAL_ID),
        "supply.noop",
        "context-engine:supply-worker",
        raw_token,
    )

    for display in (repr(claims), str(claims), repr(token), str(token)):
        assert all(marker not in display for marker in secret_markers)
    with pytest.raises(FrozenInstanceError):
        claims.workload = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        token._value = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        pickle.dumps(claims)
    with pytest.raises(TypeError):
        pickle.dumps(token)


def test_keyring_has_no_default_copies_input_and_never_exposes_secret() -> None:
    keys = {KEY_VERSION: SIGNING_KEY}
    keyring = WorkerLeaseKeyring(active_version=KEY_VERSION, keys=keys)
    keys.clear()

    token = WorkerLeaseCodec(keyring).mint(_claims())

    assert keyring.active_version == KEY_VERSION
    assert "00010203" not in repr(keyring)
    assert str(SIGNING_KEY) not in repr(keyring)
    assert WorkerLeaseCodec(keyring).verify(
        token, **_verification_arguments()  # type: ignore[arg-type]
    ) == _claims()
    with pytest.raises(TypeError):
        WorkerLeaseKeyring()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        pickle.dumps(keyring)


@pytest.mark.parametrize("version", [True, "7", 0, -1, 1 << 63])
def test_key_version_requires_positive_signed_64_bit_integer(version: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        WorkerLeaseKeyring(
            active_version=version,  # type: ignore[arg-type]
            keys={version: SIGNING_KEY},  # type: ignore[dict-item]
        )
    with pytest.raises((TypeError, ValueError)):
        _claims(signing_key_version=version)


@pytest.mark.parametrize(
    "keys",
    [
        {},
        {8: SIGNING_KEY},
        {KEY_VERSION: b"short"},
        {KEY_VERSION: bytearray(SIGNING_KEY)},
    ],
)
def test_keyring_rejects_missing_active_or_invalid_secrets(
    keys: dict[int, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        WorkerLeaseKeyring(
            active_version=KEY_VERSION,
            keys=keys,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"organization_id": str(ORGANIZATION_ID)},
        {"job_id": str(JOB_ID)},
        {"service_principal_id": str(SERVICE_PRINCIPAL_ID)},
        {"workload": ""},
        {"workload": "not canonical"},
        {"workload": "x" * 129},
        {"worker_audience": ""},
        {"worker_audience": "x" * 256},
        {"issued_at": datetime(2026, 7, 22, 10, 30)},
        {"issued_at": NOW + timedelta(microseconds=1)},
        {"expires_at": NOW},
        {"expires_at": NOW + timedelta(minutes=5, microseconds=1)},
        {"nonce": b"too-short"},
        {"nonce": bytearray(range(32))},
    ],
)
def test_claims_reject_noncanonical_types_identifiers_time_and_nonce(
    overrides: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _claims(**overrides)


def test_nonce_generation_and_digests_are_fixed_and_nonrevealing() -> None:
    first = generate_worker_lease_nonce()
    second = generate_worker_lease_nonce()
    token = _codec().mint(_claims(nonce=first))

    assert len(first) == 32
    assert len(second) == 32
    assert first != second
    assert worker_lease_nonce_digest(first) == hashlib.sha256(first).hexdigest()
    assert worker_lease_digest(token) == hashlib.sha256(
        token.serialize().encode("utf-8")
    ).hexdigest()
    assert first.hex() not in worker_lease_nonce_digest(first)


def test_bounded_noop_carrier_does_not_publish_an_incomplete_service_actor() -> None:
    assert "ServiceActor" not in supply.__all__
    assert not hasattr(supply, "ServiceActor")
