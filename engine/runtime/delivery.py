"""Nominal trusted delivery facts constructed only at trusted ingress."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class DeliveryConstructionProvenance(StrEnum):
    """Closed provenance for authenticated direct and redeemed private delivery."""

    AUTHENTICATED_DIRECT_INGRESS = "authenticated_direct_ingress"
    REDEEMED_PRIVATE_DELIVERY_EVIDENCE = "redeemed_private_delivery_evidence"


@dataclass(frozen=True, slots=True, init=False)
class TrustedDeliveryContext:
    """Server-authored trusted delivery facts unavailable to request bodies."""

    purpose: str = field(repr=False)
    authenticated_application_ref: str = field(repr=False)
    delivery_binding_ref: str = field(repr=False)
    established_at: datetime = field(repr=False)
    construction_provenance: DeliveryConstructionProvenance = field(repr=False)
    destination_ref: str | None = field(repr=False)
    consumer_ref: str | None = field(repr=False)
    audience_digest: str | None = field(repr=False)
    logical_resolution_ref: str | None = field(repr=False)
    delivery_profile_ref: str | None = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "TrustedDeliveryContext can only be constructed by trusted ingress"
        )


def _require_trusted_ref(field_name: str, value: object) -> None:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"trusted delivery {field_name} must be non-empty")


def _construct_direct_delivery_context(
    *,
    purpose: str,
    authenticated_application_ref: str,
    delivery_binding_ref: str,
    established_at: datetime,
) -> TrustedDeliveryContext:
    """Build direct-delivery facts from authenticated application route policy."""

    _require_trusted_ref("purpose", purpose)
    _require_trusted_ref(
        "authenticated_application_ref",
        authenticated_application_ref,
    )
    _require_trusted_ref("delivery_binding_ref", delivery_binding_ref)
    if (
        type(established_at) is not datetime
        or established_at.tzinfo is None
        or established_at.utcoffset() is None
    ):
        raise ValueError("trusted delivery established_at must be timezone-aware")

    context = object.__new__(TrustedDeliveryContext)
    object.__setattr__(context, "purpose", purpose)
    object.__setattr__(
        context,
        "authenticated_application_ref",
        authenticated_application_ref,
    )
    object.__setattr__(context, "delivery_binding_ref", delivery_binding_ref)
    object.__setattr__(context, "established_at", established_at)
    object.__setattr__(
        context,
        "construction_provenance",
        DeliveryConstructionProvenance.AUTHENTICATED_DIRECT_INGRESS,
    )
    object.__setattr__(context, "destination_ref", None)
    object.__setattr__(context, "consumer_ref", None)
    object.__setattr__(context, "audience_digest", None)
    object.__setattr__(context, "logical_resolution_ref", None)
    object.__setattr__(context, "delivery_profile_ref", None)
    return context


def _construct_private_delivery_context(
    *,
    purpose: str,
    authenticated_application_ref: str,
    delivery_binding_ref: str,
    established_at: datetime,
    destination_ref: str,
    consumer_ref: str,
    audience_digest: str,
    logical_resolution_ref: str,
    delivery_profile_ref: str,
) -> TrustedDeliveryContext:
    """Build private-delivery facts only after exact evidence redemption."""

    context = _construct_direct_delivery_context(
        purpose=purpose,
        authenticated_application_ref=authenticated_application_ref,
        delivery_binding_ref=delivery_binding_ref,
        established_at=established_at,
    )
    for field_name, value in (
        ("destination_ref", destination_ref),
        ("consumer_ref", consumer_ref),
        ("logical_resolution_ref", logical_resolution_ref),
        ("delivery_profile_ref", delivery_profile_ref),
    ):
        _require_trusted_ref(field_name, value)
    if (
        type(audience_digest) is not str
        or len(audience_digest) != 64
        or any(value not in "0123456789abcdef" for value in audience_digest)
    ):
        raise ValueError("trusted delivery audience_digest must be SHA-256")
    object.__setattr__(
        context,
        "construction_provenance",
        DeliveryConstructionProvenance.REDEEMED_PRIVATE_DELIVERY_EVIDENCE,
    )
    object.__setattr__(context, "destination_ref", destination_ref)
    object.__setattr__(context, "consumer_ref", consumer_ref)
    object.__setattr__(context, "audience_digest", audience_digest)
    object.__setattr__(context, "logical_resolution_ref", logical_resolution_ref)
    object.__setattr__(context, "delivery_profile_ref", delivery_profile_ref)
    return context
