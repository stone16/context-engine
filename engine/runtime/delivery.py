"""Nominal trusted delivery facts constructed only at trusted ingress."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DirectDeliveryConstructionProvenance(StrEnum):
    """Closed provenance for the first direct-delivery tracer."""

    AUTHENTICATED_DIRECT_INGRESS = "authenticated_direct_ingress"


@dataclass(frozen=True, slots=True, init=False)
class TrustedDeliveryContext:
    """Server-authored direct-delivery facts unavailable to request bodies."""

    purpose: str
    authenticated_application_ref: str
    delivery_binding_ref: str
    established_at: datetime
    construction_provenance: DirectDeliveryConstructionProvenance

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
        DirectDeliveryConstructionProvenance.AUTHENTICATED_DIRECT_INGRESS,
    )
    return context
