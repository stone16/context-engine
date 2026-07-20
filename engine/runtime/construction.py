"""Fail-closed construction for the future sealed Runtime.

This module proves mandatory dependency wiring only. It deliberately exposes no
``resolve`` method or authorization behavior before the owning M0 issues land.
"""

from dataclasses import dataclass
from enum import StrEnum


class KernelDependency(StrEnum):
    """Closed set of security-kernel dependency identities."""

    POLICY = "policy"
    AUDIT = "audit"
    BUDGET = "budget"
    PROVENANCE = "provenance"


class RuntimeConfigurationError(RuntimeError):
    """Raised when the sealed Runtime composition is incomplete or invalid."""


@dataclass(frozen=True, slots=True)
class KernelDependencies:
    """Explicit mandatory inputs to Runtime construction."""

    policy: KernelDependency
    audit: KernelDependency
    budget: KernelDependency
    provenance: KernelDependency

class Runtime:
    """Construction seam for a sealed Runtime whose delivery API is not active."""

    def __init__(self, dependencies: KernelDependencies) -> None:
        if type(dependencies) is not KernelDependencies:
            raise RuntimeConfigurationError(
                "runtime dependencies must be KernelDependencies"
            )
        expected = (
            ("policy", KernelDependency.POLICY),
            ("audit", KernelDependency.AUDIT),
            ("budget", KernelDependency.BUDGET),
            ("provenance", KernelDependency.PROVENANCE),
        )
        for field_name, expected_dependency in expected:
            if getattr(dependencies, field_name) is not expected_dependency:
                raise RuntimeConfigurationError(
                    f"mandatory kernel dependency is missing or invalid: {field_name}"
                )
        self._dependencies = dependencies


def required_kernel_dependencies() -> KernelDependencies:
    """Return the only allowed skeleton composition; no disable flag exists."""

    return KernelDependencies(
        policy=KernelDependency.POLICY,
        audit=KernelDependency.AUDIT,
        budget=KernelDependency.BUDGET,
        provenance=KernelDependency.PROVENANCE,
    )
