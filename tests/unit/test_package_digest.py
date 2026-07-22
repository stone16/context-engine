import pickle
from collections.abc import Mapping
from dataclasses import fields
from types import MappingProxyType
from typing import cast
from uuid import UUID

import pytest

from engine.runtime.package_digest import (
    PACKAGE_DIGEST_PROFILE,
    QUERY_DIGEST_PROFILE,
    QueryDigest,
    QueryDigestKeyring,
    context_package_digest,
    query_digest,
    verify_context_package_digest,
)

ORGANIZATION_ID = UUID("12345678-1234-5678-90ab-cdef12345678")
QUERY_KEY = bytes(range(32))


def test_context_package_digest_has_a_fixed_canonical_unicode_vector() -> None:
    document: dict[str, object] = {
        "usage": {"tokens": 7},
        "purpose": "support",
        "enabled": True,
        "coverage": {"status": "sufficient", "reason": None},
        "blocks": [{"ordinal": 1, "body": "你好, 🌍"}],
    }

    assert PACKAGE_DIGEST_PROFILE == "context-package-canonical-json-v1"
    assert context_package_digest(document) == (
        "630ba3a578634388e9d107f318a9ba7e2f7c2b9313f8c1bd9034e9325797aa43"
    )


def test_context_package_digest_verification_detects_mutation_and_bad_format() -> None:
    document: dict[str, object] = {
        "purpose": "support",
        "blocks": [{"body": "authorized text"}],
    }
    expected = context_package_digest(document)

    assert verify_context_package_digest(document, expected)
    assert not verify_context_package_digest(
        {"purpose": "support", "blocks": [{"body": "altered text"}]},
        expected,
    )
    assert not verify_context_package_digest(document, expected.upper())
    assert not verify_context_package_digest(document, expected[:-1])


def test_context_package_digest_uses_rfc_8785_utf16_property_order() -> None:
    document: dict[str, object] = {
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "דּ": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "😀": "Emoji: Grinning Face",
        "\u0080": "Control",
        "ö": "Latin Small Letter O With Diaeresis",
    }

    # RFC 8785 section 3.2.3 orders the emoji's UTF-16 surrogate pair before
    # U+FB33.  Sorting Python Unicode code points produces the opposite order.
    assert context_package_digest(document) == (
        "5e321556d22018a9656991a9e94f77ec175fa193e52a2429d312f8419ec8b08c"
    )


def test_context_package_digest_uses_rfc_8785_number_serialization() -> None:
    document: dict[str, object] = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27, -0.0],
    }

    # This is SHA-256 over the RFC 8785 section 3.2.2 canonical number vector:
    # {"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27,0]}
    assert context_package_digest(document) == (
        "5c34ad2f0b62822dda6a8fbcb5ad901f69fba1c84571ee09063f63266f06fc58"
    )


def test_context_package_digest_is_order_invariant_and_tuples_are_json_arrays() -> None:
    first = MappingProxyType(
        {
            "z": (MappingProxyType({"beta": 2, "alpha": 1}), "tail"),
            "a": [True, None],
        }
    )
    reordered: dict[str, object] = {
        "a": [True, None],
        "z": [{"alpha": 1, "beta": 2}, "tail"],
    }

    assert context_package_digest(first) == context_package_digest(reordered)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_context_package_digest_rejects_nonfinite_numbers(value: float) -> None:
    with pytest.raises(ValueError):
        context_package_digest({"value": value})


def test_context_package_digest_uses_rfc_8785_large_integer_vectors() -> None:
    document: dict[str, object] = {
        "numbers": [9_007_199_254_740_992, 295_147_905_179_352_825_856],
    }

    # RFC 8785 Appendix B serializes both exactly representable binary64 values
    # as integers, even though they are outside the usual safe-integer range.
    assert context_package_digest(document) == (
        "ce19560fd6d09c1841c89fe99d741d7176b969eeb8d405a75ff75888d2958aaa"
    )


def test_context_package_digest_rejects_integer_not_exactly_binary64() -> None:
    with pytest.raises(ValueError, match="exact IEEE 754 binary64"):
        context_package_digest({"value": 9_007_199_254_740_993})


class _StringSubclass(str):
    pass


class _IntegerSubclass(int):
    pass


class _ListSubclass(list[object]):
    pass


@pytest.mark.parametrize(
    "value",
    [
        b"bytes",
        bytearray(b"bytes"),
        {"set-member"},
        _StringSubclass("text"),
        _IntegerSubclass(3),
        _ListSubclass(["item"]),
    ],
)
def test_context_package_digest_rejects_non_json_values_and_subclasses(
    value: object,
) -> None:
    with pytest.raises(TypeError, match="exact JSON values"):
        context_package_digest({"value": value})


@pytest.mark.parametrize(
    "document",
    [
        {1: "integer key"},
        {_StringSubclass("key"): "string-subclass key"},
    ],
)
def test_context_package_digest_requires_exact_string_mapping_keys(
    document: dict[object, object],
) -> None:
    with pytest.raises(TypeError, match="exact string keys"):
        context_package_digest(document)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "document",
    [
        {"value": "before\ud800after"},
        {"key\udfff": "value"},
    ],
)
def test_context_package_digest_rejects_surrogate_code_units(
    document: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="Unicode scalar values"):
        context_package_digest(document)


@pytest.mark.parametrize(
    "document",
    [
        {"packageDigest": "0" * 64},
        {"nested": {"packageDigest": "0" * 64}},
    ],
)
def test_context_package_digest_rejects_an_embedded_digest_field(
    document: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="packageDigest"):
        context_package_digest(document)


def test_context_package_digest_rejects_recursive_containers() -> None:
    recursive: list[object] = []
    recursive.append(recursive)

    with pytest.raises(ValueError, match="cyclic"):
        context_package_digest({"recursive": recursive})


def test_query_digest_has_a_fixed_organization_bound_unicode_vector() -> None:
    keyring = QueryDigestKeyring(active_version=7, keys={7: QUERY_KEY})

    digest = query_digest(
        keyring,
        ORGANIZATION_ID,
        "权限 / Café / 😀 / \ud800",
    )

    assert QUERY_DIGEST_PROFILE == "context-query-json-hmac-sha256-v1"
    assert digest.value == (
        "2418ef2f45f7514d346f8dccba264cd7daf8e919dc2b4d69c63be30546f6a7f6"
    )
    assert digest.profile == QUERY_DIGEST_PROFILE
    assert digest.key_version == 7
    assert keyring.active_version == 7


def test_query_digest_separates_organization_query_key_and_key_version() -> None:
    base_keyring = QueryDigestKeyring(active_version=7, keys={7: QUERY_KEY})
    base = query_digest(base_keyring, ORGANIZATION_ID, "Café")

    changed_organization = query_digest(
        base_keyring,
        UUID("12345678-1234-5678-90ab-cdef12345679"),
        "Café",
    )
    changed_query = query_digest(base_keyring, ORGANIZATION_ID, "café")
    decomposed_query = query_digest(base_keyring, ORGANIZATION_ID, "Cafe\u0301")
    whitespace_query = query_digest(base_keyring, ORGANIZATION_ID, " Café ")
    changed_key = query_digest(
        QueryDigestKeyring(active_version=7, keys={7: b"z" * 32}),
        ORGANIZATION_ID,
        "Café",
    )
    changed_version = query_digest(
        QueryDigestKeyring(active_version=8, keys={8: QUERY_KEY}),
        ORGANIZATION_ID,
        "Café",
    )

    assert (
        len(
            {
                base.value,
                changed_organization.value,
                changed_query.value,
                decomposed_query.value,
                whitespace_query.value,
                changed_key.value,
            }
        )
        == 6
    )
    assert changed_version.value == base.value
    assert changed_version.key_version == 8
    assert base.key_version == 7


def test_query_digest_keyring_is_explicit_defensively_copied_and_redacted() -> None:
    raw_key = b"raw-query-digest-key-material!!!"
    source = {7: raw_key, 8: b"z" * 32}
    keyring = QueryDigestKeyring(active_version=7, keys=source)
    before_mutation = query_digest(keyring, ORGANIZATION_ID, "sensitive query")

    source[7] = b"y" * 32

    assert query_digest(keyring, ORGANIZATION_ID, "sensitive query") == (
        before_mutation
    )
    assert repr(keyring) == "QueryDigestKeyring(<redacted>)"
    assert "raw-query-digest-key-material" not in repr(keyring)
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(keyring)


@pytest.mark.parametrize(
    ("active_version", "keys"),
    [
        (0, {0: QUERY_KEY}),
        (True, {1: QUERY_KEY}),
        (7, {}),
        (7, {8: QUERY_KEY}),
        (7, {7: b"short-secret"}),
        (7, {7: bytearray(b"x" * 32)}),
    ],
)
def test_query_digest_keyring_rejects_missing_or_weak_explicit_keys(
    active_version: object,
    keys: object,
) -> None:
    with pytest.raises((TypeError, ValueError)) as rejected:
        QueryDigestKeyring(
            active_version=cast(int, active_version),
            keys=cast(Mapping[int, bytes], keys),
        )

    assert "short-secret" not in str(rejected.value)
    assert "short-secret" not in repr(rejected.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"value": "f" * 63},
        {"value": "F" * 64},
        {"profile": "another-profile"},
        {"key_version": 0},
        {"key_version": True},
    ],
)
def test_query_digest_metadata_is_closed_and_validated(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "value": "f" * 64,
        "profile": QUERY_DIGEST_PROFILE,
        "key_version": 7,
    }
    values.update(changes)

    with pytest.raises((TypeError, ValueError)):
        QueryDigest(**values)  # type: ignore[arg-type]


def test_query_digest_repr_and_type_errors_never_contain_the_raw_query() -> None:
    keyring = QueryDigestKeyring(active_version=7, keys={7: QUERY_KEY})
    raw_query = "never expose this raw query"
    digest = query_digest(keyring, ORGANIZATION_ID, raw_query)

    assert [item.name for item in fields(digest)] == [
        "value",
        "profile",
        "key_version",
    ]
    assert raw_query not in repr(digest)
    with pytest.raises(TypeError) as rejected:
        query_digest(keyring, ORGANIZATION_ID, _StringSubclass(raw_query))
    assert raw_query not in str(rejected.value)
    assert raw_query not in repr(rejected.value)
