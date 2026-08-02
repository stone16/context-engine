from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from applications.control import local_control_operator_authority
from applications.operator_authentication import (
    CONTROL_OPERATOR_OPERATIONS_ENV,
    CONTROL_OPERATOR_SECRET_ENV,
    DOGFOOD_SECRET_ENV,
    OPERATOR_ORGANIZATION_ENV,
    RELEASE_OPERATOR_SECRET_ENV,
    WORKER_SECRET_ENV,
    LocalControlOperatorAuthenticator,
    LocalControlOperatorConfiguration,
    LocalOperatorAuthorities,
    LocalOperatorConfiguration,
    LocalOperatorConfigurationUnavailable,
    LocalReleaseOperatorAuthenticator,
)
from engine.control import (
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    VerifiedControlOperatorIdentity,
)
from engine.control.authority import _validate_and_consume_control_call
from engine.learning import (
    ReleaseOperatorAuthenticationRejected,
    VerifiedReleaseOperatorIdentity,
)

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 7, 27, 13, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
CONTROL_SECRET = "control-operator-secret-at-least-thirty-two-bytes"
RELEASE_SECRET = "release-operator-secret-at-least-thirty-two-bytes"
DOGFOOD_SECRET = "dogfood-secret-with-at-least-thirty-two-bytes"
WORKER_SECRET = "7" * 64
WORKER_KEY_MATERIAL = "w" * 32


def environment() -> dict[str, str]:
    return {
        OPERATOR_ORGANIZATION_ENV: str(ORGANIZATION_ID),
        CONTROL_OPERATOR_SECRET_ENV: CONTROL_SECRET,
        RELEASE_OPERATOR_SECRET_ENV: RELEASE_SECRET,
        CONTROL_OPERATOR_OPERATIONS_ENV: (
            "register_source,activate_file_change_feed,read_source_progress"
        ),
        DOGFOOD_SECRET_ENV: DOGFOOD_SECRET,
        WORKER_SECRET_ENV: WORKER_SECRET,
    }


def _configuration() -> LocalOperatorConfiguration:
    configuration = LocalOperatorConfiguration.load(environment())
    assert configuration is not None
    return configuration


@pytest.mark.security_evidence(
    id="RUNTIME-LOCAL-OPERATOR-ABSENT-110",
    layer="runtime",
)
def test_operator_configuration_is_absent_by_default_and_partial_values_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in environment():
        monkeypatch.delenv(name, raising=False)
    assert LocalOperatorConfiguration.load({}) is None
    assert local_control_operator_authority() is None

    for missing_name in environment():
        partial = environment()
        del partial[missing_name]
        with pytest.raises(
            LocalOperatorConfigurationUnavailable,
            match="operator authentication rejected",
        ) as failure:
            LocalOperatorConfiguration.load(partial)
        rendered = str(failure.value)
        assert str(ORGANIZATION_ID) not in rendered
        assert "register_source" not in rendered
        assert "allowed" not in rendered
        assert CONTROL_SECRET not in rendered
        assert RELEASE_SECRET not in rendered

    assert CONTROL_SECRET not in repr(_configuration())
    assert RELEASE_SECRET not in repr(_configuration())


def test_routine_control_configuration_does_not_require_release_secrets() -> None:
    projected = {
        name: value
        for name, value in environment().items()
        if name
        in {
            OPERATOR_ORGANIZATION_ENV,
            CONTROL_OPERATOR_SECRET_ENV,
            CONTROL_OPERATOR_OPERATIONS_ENV,
        }
    }

    configuration = LocalControlOperatorConfiguration.load(projected)

    assert configuration is not None
    assert configuration.organization_id == ORGANIZATION_ID
    assert RELEASE_OPERATOR_SECRET_ENV not in projected
    assert DOGFOOD_SECRET_ENV not in projected
    assert WORKER_SECRET_ENV not in projected


def test_routine_control_configuration_rejects_dogfood_secret_reuse() -> None:
    projected = {
        name: value
        for name, value in environment().items()
        if name
        in {
            OPERATOR_ORGANIZATION_ENV,
            CONTROL_OPERATOR_SECRET_ENV,
            CONTROL_OPERATOR_OPERATIONS_ENV,
            DOGFOOD_SECRET_ENV,
        }
    }
    projected[DOGFOOD_SECRET_ENV] = CONTROL_SECRET

    with pytest.raises(LocalOperatorConfigurationUnavailable):
        LocalControlOperatorConfiguration.load(projected)


def test_control_operations_are_an_exact_enumerated_set() -> None:
    configuration = _configuration()
    assert configuration.control_operations == frozenset(
        {
            ControlOperation.REGISTER_SOURCE,
            ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
            ControlOperation.READ_SOURCE_PROGRESS,
        }
    )

    for invalid in (
        "register_source,register_source",
        "register_source,",
        "register_source, read_source",
        "register_source,not_an_operation",
    ):
        source = environment()
        source[CONTROL_OPERATOR_OPERATIONS_ENV] = invalid
        with pytest.raises(LocalOperatorConfigurationUnavailable):
            LocalOperatorConfiguration.load(source)

    collision_cases = (
        {DOGFOOD_SECRET_ENV: CONTROL_SECRET},
        {RELEASE_OPERATOR_SECRET_ENV: WORKER_KEY_MATERIAL},
    )
    for collision in collision_cases:
        source = environment() | collision
        with pytest.raises(LocalOperatorConfigurationUnavailable):
            LocalOperatorConfiguration.load(source)


@pytest.mark.security_evidence(
    id="RUNTIME-LOCAL-OPERATOR-CROSS-PLANE-110",
    layer="runtime",
)
def test_control_and_release_credentials_are_rejected_across_planes() -> None:
    configuration = _configuration()
    control = LocalControlOperatorAuthenticator(configuration, clock=lambda: NOW)
    release = LocalReleaseOperatorAuthenticator(configuration, clock=lambda: NOW)

    control_identity = control.authenticate(CONTROL_SECRET)
    release_identity = release.authenticate(RELEASE_SECRET)
    assert type(control_identity) is VerifiedControlOperatorIdentity
    assert type(release_identity) is VerifiedReleaseOperatorIdentity
    assert control_identity.operator_ref != release_identity.operator_ref

    with pytest.raises(
        ControlOperatorAuthenticationRejected,
        match="control operator authentication rejected",
    ):
        control.authenticate(RELEASE_SECRET)
    with pytest.raises(
        ReleaseOperatorAuthenticationRejected,
        match="release operator authentication rejected",
    ):
        release.authenticate(CONTROL_SECRET)

    rendered = repr(control) + repr(release)
    for secret in (CONTROL_SECRET, RELEASE_SECRET):
        assert secret not in rendered


@pytest.mark.security_evidence(
    id="RUNTIME-LOCAL-OPERATOR-EXTERNAL-110",
    layer="runtime",
)
def test_dogfood_and_worker_credentials_are_rejected_by_both_planes() -> None:
    configuration = _configuration()
    control = LocalControlOperatorAuthenticator(configuration, clock=lambda: NOW)
    release = LocalReleaseOperatorAuthenticator(configuration, clock=lambda: NOW)

    for credential in (DOGFOOD_SECRET, WORKER_SECRET):
        with pytest.raises(ControlOperatorAuthenticationRejected):
            control.authenticate(credential)
        with pytest.raises(ReleaseOperatorAuthenticationRejected):
            release.authenticate(credential)

    rendered = repr(control) + repr(release)
    assert DOGFOOD_SECRET not in rendered
    assert WORKER_SECRET not in rendered

    for external_secret in (DOGFOOD_SECRET, WORKER_KEY_MATERIAL):
        for operator_name in (
            CONTROL_OPERATOR_SECRET_ENV,
            RELEASE_OPERATOR_SECRET_ENV,
        ):
            source = environment()
            source[operator_name] = external_secret
            with pytest.raises(
                LocalOperatorConfigurationUnavailable,
                match="operator authentication rejected",
            ):
                LocalOperatorConfiguration.load(source)


@pytest.mark.security_evidence(
    id="RUNTIME-LOCAL-OPERATOR-SCOPE-110",
    layer="runtime",
)
def test_authority_grants_one_allowed_operation_per_context_lifetime() -> None:
    authorities = _configuration().authorities(clock=lambda: NOW)
    assert type(authorities) is LocalOperatorAuthorities

    with authorities.control.authorize(
        opaque_credential=CONTROL_SECRET,
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-local-source",
    ) as first:
        assert first.operation is ControlOperation.REGISTER_SOURCE
        assert first.expires_at == NOW + timedelta(minutes=15)
        _validate_and_consume_control_call(
            first,
            authority=authorities.control,
            expected_operation=ControlOperation.REGISTER_SOURCE,
            checked_at=NOW,
        )
        with authorities.control.authorize(
            opaque_credential=CONTROL_SECRET,
            operation=ControlOperation.READ_SOURCE_PROGRESS,
            request_id="read-local-source-progress",
        ) as second:
            assert second.operation is ControlOperation.READ_SOURCE_PROGRESS
            assert second is not first

    disallowed = authorities.control.authorize(
        opaque_credential=CONTROL_SECRET,
        operation=ControlOperation.OFFBOARD_FILE_SOURCE,
        request_id="not-allowed",
    )
    with pytest.raises(
        ControlOperatorAuthenticationRejected,
        match="control operator authentication rejected",
    ), disallowed:
        raise AssertionError("disallowed operation entered authority context")

    with authorities.control.authorize(
        opaque_credential=CONTROL_SECRET,
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="closed-context",
    ) as closed_call:
        pass
    with pytest.raises(ControlOperatorAuthenticationRejected):
        _validate_and_consume_control_call(
            closed_call,
            authority=authorities.control,
            expected_operation=ControlOperation.REGISTER_SOURCE,
            checked_at=NOW,
        )


def test_general_http_composition_cannot_reach_local_operator_authentication() -> None:
    prohibited_module_names = {
        "applications.control",
        "applications.operator_authentication",
    }
    pending = [
        *sorted(
            path
            for path in (ROOT / "adapters" / "http").rglob("*.py")
            if path.name != "dogfood.py"
        ),
    ]
    visited: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
                imported.update(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )
        assert prohibited_module_names.isdisjoint(imported), path
        for module_name in imported:
            module_path = ROOT.joinpath(*module_name.split("."))
            for candidate in (
                module_path.with_suffix(".py"),
                module_path / "__init__.py",
            ):
                if candidate.is_file() and candidate not in visited:
                    pending.append(candidate)


def test_dogfood_http_is_the_only_local_operator_authentication_composition() -> None:
    api_path = ROOT / "applications" / "api.py"
    api_tree = ast.parse(
        api_path.read_text(encoding="utf-8"),
        filename=api_path,
    )
    api_imports = {
        node.module
        for node in ast.walk(api_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    path = ROOT / "adapters" / "http" / "dogfood.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "adapters.http.dogfood" in api_imports
    assert "applications.operator_authentication" not in api_imports
    assert "applications.control" not in api_imports
    assert "applications.operator_authentication" in imported
    assert "applications.control" not in imported
