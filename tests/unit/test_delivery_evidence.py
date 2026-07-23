from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from engine.runtime.delivery import _construct_private_delivery_context
from engine.runtime.delivery_evidence import (
    DELIVERY_EVIDENCE_RETENTION_CLASS,
    DeliveryEvidenceNotAvailable,
    DeliveryEvidenceProfile,
    PrivateDeliveryEvidenceIssue,
    PrivateDeliveryEvidenceIssuer,
    PrivateDeliveryEvidenceRedemption,
    PrivateDeliveryEvidenceRetention,
    RedeemedPrivateDeliveryEvidence,
    private_delivery_audience_digest,
)

NOW = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
USER_ID = UUID("d3d9893f-82d2-4890-8cb2-4c7e57a56f16")
MEMBERSHIP_ID = UUID("9c9e9f4c-a5ec-4417-9408-0346e1c6c998")


def _issue(
    *, expires_at: datetime = NOW + timedelta(minutes=2)
) -> PrivateDeliveryEvidenceIssue:
    return PrivateDeliveryEvidenceIssue(
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        membership_id=MEMBERSHIP_ID,
        membership_version=7,
        authenticated_service_ref="service:bot-delivery",
        authentication_binding_ref="binding:bot-delivery",
        request_id="resolve-private-42",
        destination_ref="private-chat:42",
        consumer_ref="consumer:bot-delivery",
        purpose="context.answer",
        policy_epoch=11,
        issued_at=NOW,
        expires_at=expires_at,
    )


class RecordingIssuerPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def issue_private(self, **values: object) -> bool:
        self.calls.append(values)
        return True


class RecordingRetentionPort:
    def __init__(self) -> None:
        self.organization_ids: list[UUID] = []

    def delete_expired_private(self, organization_id: UUID) -> int:
        self.organization_ids.append(organization_id)
        return 2


def test_private_evidence_issuer_returns_bearer_but_persists_only_digest() -> None:
    port = RecordingIssuerPort()
    issuer = PrivateDeliveryEvidenceIssuer(
        port,
        profile=DeliveryEvidenceProfile(
            profile_ref="private-delivery-evidence-v1",
            maximum_ttl=timedelta(minutes=5),
        ),
        reference_factory=lambda: "der_" + "a" * 64,
        resolution_ref_factory=lambda: "dlr_" + "b" * 32,
    )

    issued = issuer.issue_private(_issue())

    assert issued.evidence_ref == "der_" + "a" * 64
    assert issued.logical_resolution_ref == "dlr_" + "b" * 32
    assert issued.profile_ref == "private-delivery-evidence-v1"
    assert port.calls == [
        {
            "request": _issue(),
            "evidence_digest": bytes.fromhex(
                "4e06bf749f6fbb96104a6a5d26456aaeb40e568b51b9b0ca67d9a03757fa4df6"
            ),
            "audience_digest": private_delivery_audience_digest(_issue()),
            "logical_resolution_ref": "dlr_" + "b" * 32,
        }
    ]
    assert issued.evidence_ref not in repr(port.calls[0]["evidence_digest"])


def test_issuer_rejects_lifetime_outside_supplied_versioned_profile() -> None:
    issuer = PrivateDeliveryEvidenceIssuer(
        RecordingIssuerPort(),
        profile=DeliveryEvidenceProfile(
            profile_ref="private-delivery-evidence-v1",
            maximum_ttl=timedelta(seconds=30),
            retention_class=DELIVERY_EVIDENCE_RETENTION_CLASS,
        ),
    )

    with pytest.raises(DeliveryEvidenceNotAvailable):
        issuer.issue_private(_issue())


def test_retention_deletes_only_expired_private_evidence_in_one_organization() -> None:
    port = RecordingRetentionPort()

    deleted = PrivateDeliveryEvidenceRetention(port).delete_expired(ORGANIZATION_ID)

    assert deleted == 2
    assert port.organization_ids == [ORGANIZATION_ID]


@pytest.mark.security_evidence(id="PROP-DELIVERY-EVIDENCE-063", layer="property")
def test_delivery_evidence_values_redact_all_trusted_facts_from_repr() -> None:
    port = RecordingIssuerPort()
    issuer = PrivateDeliveryEvidenceIssuer(
        port,
        profile=DeliveryEvidenceProfile(
            profile_ref="private-delivery-evidence-v1",
            maximum_ttl=timedelta(minutes=5),
        ),
        reference_factory=lambda: "der_" + "c" * 64,
        resolution_ref_factory=lambda: "dlr_" + "d" * 32,
    )

    issued = issuer.issue_private(_issue())
    audience_digest = private_delivery_audience_digest(_issue())
    redemption = PrivateDeliveryEvidenceRedemption(
        evidence_ref=issued.evidence_ref,
        evidence_digest=bytes.fromhex(
            "45eccb5602e8cea7d5179cd1395dbb298be51b5c9d8c8a74ce19d9d97ad44a02"
        ),
        authenticated_service_ref="service:bot-delivery",
        authentication_binding_ref="binding:bot-delivery",
        request_id="resolve-private-42",
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        membership_id=MEMBERSHIP_ID,
        membership_version=7,
        destination_ref="private-chat:42",
        consumer_ref="consumer:bot-delivery",
        delivery_kind="private",
        audience_digest=audience_digest,
        purpose="context.answer",
        policy_epoch=11,
        redeemed_at=NOW + timedelta(seconds=1),
    )
    redeemed = RedeemedPrivateDeliveryEvidence(
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        membership_id=MEMBERSHIP_ID,
        membership_version=7,
        authenticated_service_ref="service:bot-delivery",
        authentication_binding_ref="binding:bot-delivery",
        request_id="resolve-private-42",
        destination_ref="private-chat:42",
        consumer_ref="consumer:bot-delivery",
        delivery_kind="private",
        purpose="context.answer",
        audience_digest=audience_digest,
        policy_epoch=11,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
        logical_resolution_ref=issued.logical_resolution_ref,
        profile_ref=issued.profile_ref,
    )
    delivery_context = _construct_private_delivery_context(
        purpose="context.answer",
        authenticated_application_ref="service:bot-delivery",
        delivery_binding_ref="binding:bot-delivery",
        established_at=NOW + timedelta(seconds=1),
        destination_ref="private-chat:42",
        consumer_ref="consumer:bot-delivery",
        audience_digest=audience_digest,
        logical_resolution_ref=issued.logical_resolution_ref,
        delivery_profile_ref=issued.profile_ref,
    )
    displays = (
        repr(_issue()),
        repr(issued),
        repr(redemption),
        repr(redeemed),
        repr(delivery_context),
    )
    protected_values = (
        str(ORGANIZATION_ID),
        str(USER_ID),
        str(MEMBERSHIP_ID),
        "service:bot-delivery",
        "binding:bot-delivery",
        "resolve-private-42",
        "private-chat:42",
        "consumer:bot-delivery",
        "context.answer",
        issued.evidence_ref,
        issued.logical_resolution_ref,
    )
    for display in displays:
        for protected_value in protected_values:
            assert protected_value not in display
