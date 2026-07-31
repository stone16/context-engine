"""Privacy governance for tracked synthetic data and public subset promotion."""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, NoReturn, cast

from engine.learning.golden import load_golden_set, validate_golden_document_schema

PUBLIC_SUBSET_GOVERNANCE_VERSION: Final = (
    "context-engine-public-subset-governance-v1"
)
_PLACEHOLDER_PREFIXES: Final = (
    "synthetic-",
    "synthetic/",
    "placeholder-",
    "placeholder/",
)
_CONTENT_FIELDS: Final = frozenset(
    {
        "query",
        "name",
        "caseRef",
        "claimRef",
        "expectedAnswer",
        "claim",
        "topicCluster",
        "path",
        "sourceRef",
        "resourceRef",
        "revisionRef",
        "fragmentRef",
    }
)
_CANONICAL_SCHEMA_PATHS: Final = frozenset(
    {Path("v0/schema.json"), Path("v1/schema.json")}
)


@dataclass(frozen=True, slots=True)
class _VerifiedMaintainerAuthentication:
    principal_ref: str
    authentication_binding_ref: str
    authority_ref: str


@dataclass(frozen=True, slots=True)
class _LocalPublicSubsetAuthorityConfiguration:
    governance: PublicSubsetGovernance
    credential: bytes


class PublicSubsetPromotionRejected(RuntimeError):
    """The caller lacks the configured maintainer privacy authority."""


@dataclass(frozen=True, slots=True)
class PublicSubsetGovernance:
    promotion_authority: str

    def __post_init__(self) -> None:
        if (
            type(self.promotion_authority) is not str
            or not self.promotion_authority
            or self.promotion_authority.isspace()
        ):
            raise ValueError("public subset promotion authority is unavailable")


@dataclass(frozen=True, slots=True, init=False)
class VerifiedPublicSubsetMaintainerIdentity:
    """Nominal trusted identity facts returned by the maintainer authenticator."""

    principal_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    authority_ref: str = field(repr=False)

    def __init__(
        self,
        authentication: _VerifiedMaintainerAuthentication,
    ) -> None:
        if type(authentication) is not _VerifiedMaintainerAuthentication:
            raise TypeError(
                "verified public subset maintainer identity is authenticated"
            )
        if (
            authentication.principal_ref != "maintainer:local"
            or authentication.authentication_binding_ref
            != "binding:local-maintainer:v1"
            or authentication.authority_ref != "maintainer"
        ):
            raise TypeError("verified public subset maintainer identity is malformed")
        object.__setattr__(self, "principal_ref", authentication.principal_ref)
        object.__setattr__(
            self,
            "authentication_binding_ref",
            authentication.authentication_binding_ref,
        )
        object.__setattr__(self, "authority_ref", authentication.authority_ref)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError(
            "verified public subset maintainer identity must not be subclassed"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError(
            "verified public subset maintainer identity is not serializable"
        )


class PublicSubsetPromotionAuthority:
    """Production-composed local privacy authority without publication authority."""

    __slots__ = ("_credential", "_governance")
    _credential: bytes
    _governance: PublicSubsetGovernance

    def __init__(
        self,
        configuration: _LocalPublicSubsetAuthorityConfiguration,
    ) -> None:
        if type(configuration) is not _LocalPublicSubsetAuthorityConfiguration:
            raise TypeError("public subset authority is production-composed")
        if type(configuration.governance) is not PublicSubsetGovernance:
            raise TypeError("public subset governance is required")
        if (
            type(configuration.credential) is not bytes
            or len(configuration.credential) < 32
        ):
            raise ValueError("public subset maintainer authentication is unavailable")
        self._governance = configuration.governance
        self._credential = configuration.credential

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError("public subset authority must not be subclassed")

    def authorize(
        self,
        opaque_credential: str,
    ) -> None:
        """Authenticate and refuse every non-maintainer privacy principal."""

        if (
            type(opaque_credential) is not str
            or not opaque_credential
            or opaque_credential.isspace()
        ):
            raise PublicSubsetPromotionRejected("public subset promotion refused")
        try:
            supplied = opaque_credential.encode("utf-8")
        except UnicodeEncodeError:
            raise PublicSubsetPromotionRejected(
                "public subset promotion refused"
            ) from None
        if not hmac.compare_digest(supplied, self._credential):
            raise PublicSubsetPromotionRejected("public subset promotion refused")
        verified = VerifiedPublicSubsetMaintainerIdentity(
            _VerifiedMaintainerAuthentication(
                principal_ref="maintainer:local",
                authentication_binding_ref="binding:local-maintainer:v1",
                authority_ref="maintainer",
            )
        )
        if verified.authority_ref != self._governance.promotion_authority:
            raise PublicSubsetPromotionRejected("public subset promotion refused")


def _local_public_subset_promotion_authority(
    governance: PublicSubsetGovernance,
    credential: bytes,
) -> PublicSubsetPromotionAuthority:
    """Compose the fixed local authenticator; applications supply no strategy."""

    return PublicSubsetPromotionAuthority(
        _LocalPublicSubsetAuthorityConfiguration(
            governance=governance,
            credential=credential,
        )
    )


def load_public_subset_governance(path: Path) -> PublicSubsetGovernance:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise ValueError("public subset governance is unavailable") from None
    if type(document) is not dict or frozenset(document) != frozenset(
        {"promotionAuthority", "schemaVersion"}
    ):
        raise ValueError("public subset governance is malformed")
    if document["schemaVersion"] != PUBLIC_SUBSET_GOVERNANCE_VERSION:
        raise ValueError("public subset governance version is unavailable")
    return PublicSubsetGovernance(
        promotion_authority=cast(str, document["promotionAuthority"])
    )


def _assert_placeholder_text(field_name: str, value: object, path: Path) -> None:
    if type(value) is not str or not value.startswith(_PLACEHOLDER_PREFIXES):
        raise ValueError(
            f"tracked golden content requires placeholder text: {path.name} "
            f"field {field_name}"
        )


def _scan_value(value: object, path: Path) -> None:
    if type(value) is dict:
        mapping = cast(Mapping[object, object], value)
        for raw_key, child in mapping.items():
            if type(raw_key) is not str:
                raise ValueError(f"tracked golden object is malformed: {path.name}")
            if raw_key in _CONTENT_FIELDS:
                if type(child) is list:
                    for item in cast(list[object], child):
                        _assert_placeholder_text(raw_key, item, path)
                else:
                    _assert_placeholder_text(raw_key, child, path)
            _scan_value(child, path)
        return
    if type(value) is list:
        for child in cast(list[object], value):
            _scan_value(child, path)


def assert_tracked_golden_tree_is_synthetic(root: Path) -> None:
    """Reject real/anonymized content in every tracked golden JSON fixture."""

    if not isinstance(root, Path):
        raise TypeError("tracked golden root must be Path")
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        if path.name == "schema.json":
            if path.relative_to(root) not in _CANONICAL_SCHEMA_PATHS:
                raise ValueError(
                    f"tracked golden schema path is unavailable: {path.name}"
                )
            continue
        if path.name.endswith(".lock.json"):
            golden_path = path.with_name(
                path.name.removesuffix(".lock.json") + ".json"
            )
            try:
                load_golden_set(
                    golden_path,
                    lock_path=path,
                    validate_set_composition=False,
                )
            except RuntimeError as error:
                raise ValueError(
                    f"tracked golden lock is invalid: {path.name}: {error}"
                ) from None
            continue
        if path.suffix != ".json":
            raise ValueError(f"tracked golden file is not synthetic JSON: {path.name}")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            raise ValueError(f"tracked golden JSON is malformed: {path.name}") from None
        if (
            type(document) is dict
            and "entries" in document
            and document.get("synthetic") is not True
        ):
            raise ValueError(f"tracked golden set must be synthetic: {path.name}")
        _scan_value(document, path)
        try:
            validate_golden_document_schema(document)
        except RuntimeError as error:
            raise ValueError(
                f"tracked golden schema is invalid: {path.name}: {error}"
            ) from None
