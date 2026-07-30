from dataclasses import replace
from typing import cast

import pytest

from engine.runtime import (
    KernelDependencies,
    Runtime,
    RuntimeConfigurationError,
)
from engine.runtime.construction import required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex, prohibited_empty_path_content_io


class _IndexWithoutDiscoveryPreparation:
    def discover(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("an incomplete index must never be called")


@pytest.mark.parametrize(
    "missing", ["policy", "policy_epoch", "audit", "budget", "provenance", "egress"]
)
def test_runtime_rejects_each_missing_kernel_dependency(missing: str) -> None:
    dependencies = required_kernel_dependencies()
    invalid_dependencies = replace(dependencies, **{missing: None})  # type: ignore[arg-type]

    with pytest.raises(
        RuntimeConfigurationError,
        match=f"mandatory kernel dependency is missing or invalid: {missing}",
    ):
        Runtime(invalid_dependencies)


def test_runtime_rejects_a_dependency_in_the_wrong_slot() -> None:
    dependencies = required_kernel_dependencies()
    invalid_dependencies = KernelDependencies(
        policy=dependencies.audit,  # type: ignore[arg-type]
        policy_epoch=dependencies.policy_epoch,
        audit=dependencies.audit,
        budget=dependencies.budget,
        provenance=dependencies.provenance,
        egress=dependencies.egress,
    )

    with pytest.raises(RuntimeConfigurationError, match="invalid: policy"):
        Runtime(invalid_dependencies)


def test_runtime_rejects_a_subclass_that_overrides_validation() -> None:
    class BypassDependencies(KernelDependencies):
        def validate(self) -> None:
            pass

    dependencies = BypassDependencies(
        policy=None,  # type: ignore[arg-type]
        policy_epoch=None,  # type: ignore[arg-type]
        audit=None,  # type: ignore[arg-type]
        budget=None,  # type: ignore[arg-type]
        provenance=None,  # type: ignore[arg-type]
        egress=None,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeConfigurationError, match="must be KernelDependencies"):
        Runtime(dependencies)


def test_runtime_rejects_a_duck_typed_validation_bypass() -> None:
    class BypassDependencies:
        def validate(self) -> None:
            pass

    with pytest.raises(RuntimeConfigurationError, match="must be KernelDependencies"):
        Runtime(BypassDependencies())  # type: ignore[arg-type]


def test_runtime_rejects_candidate_index_without_discovery_preparation() -> None:
    with pytest.raises(
        RuntimeConfigurationError,
        match="candidate_index is incomplete",
    ):
        Runtime(
            required_kernel_dependencies(),
            candidate_index=cast(
                CandidateIndex,
                _IndexWithoutDiscoveryPreparation(),
            ),
        )


def test_runtime_rejects_composed_index_without_discovery_preparation() -> None:
    incomplete = cast(CandidateIndex, _IndexWithoutDiscoveryPreparation())

    with pytest.raises(
        RuntimeConfigurationError,
        match="candidate_index is incomplete",
    ):
        Runtime(
            required_kernel_dependencies(),
            content_io=replace(
                prohibited_empty_path_content_io(),
                index=incomplete,
            ),
        )
