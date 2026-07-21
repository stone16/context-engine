from dataclasses import FrozenInstanceError, fields, replace
from itertools import product

import pytest

from engine.runtime.budget import (
    PackageBudget,
    PackageBudgetRequest,
    effective_package_budget,
)

SERVER_CEILING = PackageBudget(
    max_tokens=1_000,
    max_provider_calls=8,
    max_cost_microunits=25_000,
    max_elapsed_ms=2_500,
)


def test_omitted_request_inherits_the_finite_server_ceiling() -> None:
    assert effective_package_budget(SERVER_CEILING, None) == SERVER_CEILING


def test_explicit_empty_request_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="^at least one budget dimension must be provided$",
    ):
        PackageBudgetRequest()


def test_partial_request_inherits_omitted_dimensions_and_narrows_present_ones() -> None:
    requested = PackageBudgetRequest(
        max_tokens=600,
        max_elapsed_ms=1_500,
    )

    assert effective_package_budget(SERVER_CEILING, requested) == PackageBudget(
        max_tokens=600,
        max_provider_calls=8,
        max_cost_microunits=25_000,
        max_elapsed_ms=1_500,
    )


def test_requested_ceilings_never_increase_any_server_dimension() -> None:
    request_values = (
        (None, 1, SERVER_CEILING.max_tokens, 10_000),
        (None, 1, SERVER_CEILING.max_provider_calls, 100),
        (None, 1, SERVER_CEILING.max_cost_microunits, 100_000),
        (None, 1, SERVER_CEILING.max_elapsed_ms, 10_000),
    )

    for values in product(*request_values):
        if all(value is None for value in values):
            continue
        requested = PackageBudgetRequest(*values)
        effective = effective_package_budget(SERVER_CEILING, requested)

        for field in fields(PackageBudget):
            server_value = getattr(SERVER_CEILING, field.name)
            requested_value = getattr(requested, field.name)
            expected = (
                server_value
                if requested_value is None
                else min(server_value, requested_value)
            )
            assert getattr(effective, field.name) == expected
            assert getattr(effective, field.name) <= server_value


@pytest.mark.parametrize(
    "field_name",
    [
        "max_tokens",
        "max_provider_calls",
        "max_cost_microunits",
        "max_elapsed_ms",
    ],
)
@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.0, "1", None])
def test_server_ceiling_rejects_non_positive_or_non_exact_integers(
    field_name: str, invalid: object
) -> None:
    with pytest.raises(
        ValueError,
        match=rf"^{field_name} must be a positive exact integer$",
    ):
        replace(SERVER_CEILING, **{field_name: invalid})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field_name",
    [
        "max_tokens",
        "max_provider_calls",
        "max_cost_microunits",
        "max_elapsed_ms",
    ],
)
@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.0, "1"])
def test_request_rejects_present_non_positive_or_non_exact_integers(
    field_name: str, invalid: object
) -> None:
    with pytest.raises(
        ValueError,
        match=rf"^{field_name} must be a positive exact integer$",
    ):
        replace(
            PackageBudgetRequest(max_tokens=1),
            **{field_name: invalid},  # type: ignore[arg-type]
        )


def test_budget_values_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        SERVER_CEILING.max_tokens = 2  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        PackageBudgetRequest(max_tokens=1).max_tokens = 2  # type: ignore[misc]
