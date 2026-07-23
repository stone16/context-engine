from __future__ import annotations

import pytest

from engine.persistence.configuration import LEARNING_ROLE, RELEASE_DEFINER_ROLE
from scripts.provision_database_roles import _contract_from_environment


def _provisioning_environment() -> dict[str, str]:
    return {
        "POSTGRES_DB": "context_engine",
        "POSTGRES_USER": "context_engine_bootstrap",
        "POSTGRES_PASSWORD": "a" * 64,
        "CONTEXT_ENGINE_POSTGRES_PORT": "5432",
        "CONTEXT_ENGINE_MIGRATOR_ROLE": "context_engine_migrator",
        "CONTEXT_ENGINE_CONTROL_ROLE": "context_engine_control",
        "CONTEXT_ENGINE_CONTROL_PASSWORD": "b" * 64,
        "CONTEXT_ENGINE_IDENTITY_ROLE": "context_engine_identity",
        "CONTEXT_ENGINE_IDENTITY_PASSWORD": "e" * 64,
        "CONTEXT_ENGINE_LEARNING_ROLE": LEARNING_ROLE,
        "CONTEXT_ENGINE_LEARNING_PASSWORD": "c" * 64,
        "CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE": "context_engine_security_operator",
        "CONTEXT_ENGINE_SECURITY_OPERATOR_PASSWORD": "d" * 64,
    }


def test_provisioning_contract_requires_exact_learning_authority() -> None:
    contract = _contract_from_environment(_provisioning_environment())

    assert contract.learning_role == LEARNING_ROLE
    assert contract.learning_password == "c" * 64
    assert contract.release_definer_role == RELEASE_DEFINER_ROLE


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "CONTEXT_ENGINE_LEARNING_ROLE",
            "context_engine_runtime",
            "invalid learning role",
        ),
        (
            "CONTEXT_ENGINE_LEARNING_PASSWORD",
            "not-generated",
            "learning_password must be a generated 64-hex secret",
        ),
    ],
)
def test_provisioning_contract_rejects_unsafe_learning_configuration(
    name: str,
    value: str,
    message: str,
) -> None:
    environment = _provisioning_environment()
    environment[name] = value

    with pytest.raises(ValueError, match=message):
        _contract_from_environment(environment)
