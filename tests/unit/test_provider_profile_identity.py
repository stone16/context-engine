from dataclasses import replace

import pytest

from engine.supply import (
    CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    EmbeddingProviderProfile,
)


def _profile() -> EmbeddingProviderProfile:
    return EmbeddingProviderProfile(
        model_id="Qwen/Qwen3-Embedding-0.6B",
        revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        artifact_digest=(
            "8cb25677d5be69ce6ac88ebbdfb5dad30980fee39c35c6324a583e325917eddc"
        ),
        dimension=CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
        pooling="last_token",
        query_prefix=(
            "Instruct: Given a web search query, retrieve relevant passages that "
            "answer the query\nQuery:"
        ),
        document_prefix="",
        transformation_pipeline="l2 -> truncate 1024->384 -> l2",
        precision="float32",
        batch_size=8,
    )


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("model_id", ""),
        ("model_id", " mutable model "),
        ("revision", "main"),
        ("revision", "0" * 39),
        ("artifact_digest", "f" * 63),
        ("artifact_digest", "F" * 64),
        ("dimension", 0),
        ("dimension", True),
        ("pooling", ""),
        ("query_prefix", None),
        ("document_prefix", None),
        ("transformation_pipeline", ""),
        ("precision", ""),
        ("batch_size", 0),
    ],
)
def test_unresolved_provider_profile_field_is_not_constructible(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises((TypeError, ValueError), match="profile identity"):
        replace(_profile(), **{field_name: invalid})  # type: ignore[arg-type]


def test_profile_digest_binds_every_identity_field() -> None:
    profile = _profile()

    variants = (
        replace(profile, model_id="context-engine/different-model"),
        replace(profile, revision="1" * 40),
        replace(profile, artifact_digest="2" * 64),
        replace(profile, dimension=383),
        replace(profile, pooling="mean"),
        replace(profile, query_prefix="query: "),
        replace(profile, document_prefix="passage: "),
        replace(profile, transformation_pipeline="l2 -> keep 384 -> l2"),
        replace(profile, precision="float16"),
        replace(profile, batch_size=4),
    )

    assert len(profile.profile_digest) == 64
    assert all(variant.profile_digest != profile.profile_digest for variant in variants)
    assert len({variant.profile_digest for variant in variants}) == len(variants)
