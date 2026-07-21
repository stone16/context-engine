"""Closed M0 Runtime capability declaration and generic refusal audit."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

__all__ = [
    "RuntimeCapability",
]


class RuntimeCapability(StrEnum):
    """Closed capabilities understood by the current Runtime contract."""

    MATERIALIZED_ACQUIRE = "materialized_acquire"
    CONTINUE = "continue"
    OPEN_CITATION = "open_citation"
    FEDERATED_DISCOVERY = "federated_discovery"
    SOURCE_NATIVE_AUTHORIZATION = "source_native_authorization"


@dataclass(frozen=True, slots=True)
class RuntimeCapabilityDeclaration:
    """Server-owned declaration; it is never supplied by a resolve caller."""

    available: frozenset[RuntimeCapability]

    def __post_init__(self) -> None:
        if type(self.available) is not frozenset or any(
            type(capability) is not RuntimeCapability for capability in self.available
        ):
            raise TypeError(
                "available capabilities must be a frozenset of RuntimeCapability"
            )


M0_RUNTIME_CAPABILITY_DECLARATION = RuntimeCapabilityDeclaration(
    available=frozenset({RuntimeCapability.MATERIALIZED_ACQUIRE})
)


class RuntimeRefusalCategory(StrEnum):
    """Restricted internal refusal categories without target detail."""

    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"


class UnsupportedCapability(Exception):
    """Internal typed cause raised before any content dependency is called."""

    category = RuntimeRefusalCategory.UNSUPPORTED_CAPABILITY

    def __init__(self) -> None:
        super().__init__()


@dataclass(frozen=True, slots=True)
class UnsupportedCapabilityAuditReceipt:
    """Safe restricted audit carrier with no capability or opaque-input detail."""

    category: RuntimeRefusalCategory = RuntimeRefusalCategory.UNSUPPORTED_CAPABILITY
    denied_detail_count: Literal[0] = 0

    def __post_init__(self) -> None:
        if self.category is not RuntimeRefusalCategory.UNSUPPORTED_CAPABILITY:
            raise ValueError("unsupported capability audit category must remain closed")
        if self.denied_detail_count != 0:
            raise ValueError(
                "unsupported capability audit must retain no denied detail"
            )


class RuntimeCapabilityGate:
    """Non-substitutable preflight over the tracked M0 declaration."""

    __slots__ = ()

    def require_available(self, capability: RuntimeCapability) -> None:
        if type(capability) is not RuntimeCapability:
            raise TypeError("capability must be RuntimeCapability")
        if capability not in M0_RUNTIME_CAPABILITY_DECLARATION.available:
            raise UnsupportedCapability


def _required_capability_for_request(
    request: object,
    *,
    acquire_capability: RuntimeCapability,
) -> RuntimeCapability:
    """Map one exact closed request to its server-owned required capability."""

    from engine.runtime.contracts import Acquire, Continue, OpenCitation

    if type(acquire_capability) is not RuntimeCapability:
        raise TypeError("acquire_capability must be RuntimeCapability")
    if type(request) is Acquire:
        return acquire_capability
    if type(request) is Continue:
        return RuntimeCapability.CONTINUE
    if type(request) is OpenCitation:
        return RuntimeCapability.OPEN_CITATION
    raise TypeError("request must be one closed Runtime request variant")
