"""Finite PackageBudget contracts owned by ContextRuntime."""

from dataclasses import dataclass, fields


def _require_positive_exact_integer(field_name: str, value: object) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive exact integer")


@dataclass(frozen=True, slots=True)
class PackageBudget:
    """Effective finite ceiling enforced across all package budget dimensions."""

    max_tokens: int
    max_provider_calls: int
    max_cost_microunits: int
    max_elapsed_ms: int

    def __post_init__(self) -> None:
        for field in fields(self):
            _require_positive_exact_integer(field.name, getattr(self, field.name))


@dataclass(frozen=True, slots=True)
class PackageBudgetRequest:
    """A present caller ceiling with at least one requested dimension."""

    max_tokens: int | None = None
    max_provider_calls: int | None = None
    max_cost_microunits: int | None = None
    max_elapsed_ms: int | None = None

    def __post_init__(self) -> None:
        has_requested_dimension = False
        for field in fields(self):
            value = getattr(self, field.name)
            if value is not None:
                has_requested_dimension = True
                _require_positive_exact_integer(field.name, value)
        if not has_requested_dimension:
            raise ValueError("at least one budget dimension must be provided")


def effective_package_budget(
    server_ceiling: PackageBudget,
    requested_ceiling: PackageBudgetRequest | None,
) -> PackageBudget:
    """Intersect a finite server ceiling with an optional smaller caller cap."""

    if type(server_ceiling) is not PackageBudget:
        raise TypeError("server_ceiling must be PackageBudget")
    if (
        requested_ceiling is not None
        and type(requested_ceiling) is not PackageBudgetRequest
    ):
        raise TypeError("requested_ceiling must be PackageBudgetRequest or None")

    if requested_ceiling is None:
        return server_ceiling

    def narrowed(server_value: int, requested_value: int | None) -> int:
        return (
            server_value
            if requested_value is None
            else min(server_value, requested_value)
        )

    return PackageBudget(
        max_tokens=narrowed(server_ceiling.max_tokens, requested_ceiling.max_tokens),
        max_provider_calls=narrowed(
            server_ceiling.max_provider_calls,
            requested_ceiling.max_provider_calls,
        ),
        max_cost_microunits=narrowed(
            server_ceiling.max_cost_microunits,
            requested_ceiling.max_cost_microunits,
        ),
        max_elapsed_ms=narrowed(
            server_ceiling.max_elapsed_ms,
            requested_ceiling.max_elapsed_ms,
        ),
    )
