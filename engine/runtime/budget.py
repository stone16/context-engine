"""Finite PackageBudget contracts and request-scoped usage metering."""

from __future__ import annotations

from dataclasses import dataclass, fields
from threading import Lock


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
class BudgetUsage:
    """Exact resources consumed while assembling one package."""

    tokens: int
    provider_calls: int
    cost_microunits: int
    elapsed_ms: int

    def __post_init__(self) -> None:
        for usage_field in fields(self):
            value = getattr(self, usage_field.name)
            if type(value) is not int or value < 0:
                raise ValueError(
                    f"{usage_field.name} must be a non-negative exact integer"
                )


_EMPTY_USAGE = BudgetUsage(
    tokens=0,
    provider_calls=0,
    cost_microunits=0,
    elapsed_ms=0,
)


class PackageBudgetExceeded(RuntimeError):
    """A bounded operation cannot fit in the remaining PackageBudget."""


class _PackageBudgetReservation:
    __slots__ = ("maximum", "meter", "active")

    def __init__(
        self,
        meter: PackageBudgetMeter,
        maximum: BudgetUsage,
    ) -> None:
        self.meter = meter
        self.maximum = maximum
        self.active = True


class PackageBudgetMeter:
    """Atomically reserve and charge usage against one effective PackageBudget."""

    __slots__ = ("_budget", "_lock", "_reserved", "_usage")

    def __init__(
        self,
        budget: PackageBudget,
        *,
        initial_usage: BudgetUsage = _EMPTY_USAGE,
    ) -> None:
        if type(budget) is not PackageBudget:
            raise TypeError("PackageBudgetMeter requires PackageBudget")
        if type(initial_usage) is not BudgetUsage:
            raise TypeError("initial_usage must be BudgetUsage")
        self._budget = budget
        self._lock = Lock()
        self._reserved = _EMPTY_USAGE
        self._usage = initial_usage
        if not self._fits(initial_usage):
            raise ValueError("initial usage exceeds PackageBudget")

    @property
    def budget(self) -> PackageBudget:
        return self._budget

    @property
    def usage(self) -> BudgetUsage:
        with self._lock:
            return self._usage

    def _fits(self, usage: BudgetUsage) -> bool:
        return (
            usage.tokens <= self._budget.max_tokens
            and usage.provider_calls <= self._budget.max_provider_calls
            and usage.cost_microunits <= self._budget.max_cost_microunits
            and usage.elapsed_ms <= self._budget.max_elapsed_ms
        )

    @staticmethod
    def _sum(left: BudgetUsage, right: BudgetUsage) -> BudgetUsage:
        return BudgetUsage(
            tokens=left.tokens + right.tokens,
            provider_calls=left.provider_calls + right.provider_calls,
            cost_microunits=(left.cost_microunits + right.cost_microunits),
            elapsed_ms=left.elapsed_ms + right.elapsed_ms,
        )

    @staticmethod
    def _subtract(left: BudgetUsage, right: BudgetUsage) -> BudgetUsage:
        return BudgetUsage(
            tokens=left.tokens - right.tokens,
            provider_calls=left.provider_calls - right.provider_calls,
            cost_microunits=(left.cost_microunits - right.cost_microunits),
            elapsed_ms=left.elapsed_ms - right.elapsed_ms,
        )

    def _reserve(self, maximum: BudgetUsage) -> _PackageBudgetReservation:
        if type(maximum) is not BudgetUsage:
            raise TypeError("PackageBudget reservation requires BudgetUsage")
        with self._lock:
            proposed = self._sum(self._sum(self._usage, self._reserved), maximum)
            if not self._fits(proposed):
                raise PackageBudgetExceeded
            self._reserved = self._sum(self._reserved, maximum)
        return _PackageBudgetReservation(self, maximum)

    def _commit(
        self,
        reservation: _PackageBudgetReservation,
        actual: BudgetUsage,
    ) -> None:
        if (
            type(reservation) is not _PackageBudgetReservation
            or reservation.meter is not self
        ):
            raise ValueError("PackageBudget reservation is not active")
        if type(actual) is not BudgetUsage:
            raise TypeError("PackageBudget charge requires BudgetUsage")
        if any(
            getattr(actual, field.name) > getattr(reservation.maximum, field.name)
            for field in fields(BudgetUsage)
        ):
            raise ValueError("PackageBudget charge exceeds its reservation")
        with self._lock:
            if not reservation.active:
                raise ValueError("PackageBudget reservation is not active")
            self._reserved = self._subtract(self._reserved, reservation.maximum)
            self._usage = self._sum(self._usage, actual)
            reservation.active = False

    def _cancel(self, reservation: _PackageBudgetReservation) -> None:
        if (
            type(reservation) is not _PackageBudgetReservation
            or reservation.meter is not self
        ):
            raise ValueError("PackageBudget reservation is not active")
        with self._lock:
            if not reservation.active:
                raise ValueError("PackageBudget reservation is not active")
            self._reserved = self._subtract(self._reserved, reservation.maximum)
            reservation.active = False


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
