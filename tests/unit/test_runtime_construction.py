from dataclasses import replace

import pytest

from engine.runtime import (
    KernelDependencies,
    Runtime,
    RuntimeConfigurationError,
)
from engine.runtime.construction import required_kernel_dependencies


@pytest.mark.parametrize("missing", ["policy", "audit", "budget", "provenance"])
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
        policy=dependencies.audit,
        audit=dependencies.audit,
        budget=dependencies.budget,
        provenance=dependencies.provenance,
    )

    with pytest.raises(RuntimeConfigurationError, match="invalid: policy"):
        Runtime(invalid_dependencies)
