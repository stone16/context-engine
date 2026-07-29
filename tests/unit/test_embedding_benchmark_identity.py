from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from eval.embedding_benchmark import (
    EMBEDDING_DIMENSION,
    BenchmarkUnavailable,
    ModelIdentity,
    ModelTransformationPipeline,
)


def _resolved_identity() -> ModelIdentity:
    return ModelIdentity(
        model_id="Qwen/Qwen3-Embedding-0.6B",
        revision="a" * 40,
        artifact_digest="b" * 64,
        dimension=EMBEDDING_DIMENSION,
        transformation_pipeline=ModelTransformationPipeline.PRIMARY,
        pooling="last_token",
        query_prefix="Instruct: retrieve relevant passages\nQuery:",
        document_prefix="",
        precision="float32",
        batch_size=8,
    )


@pytest.mark.parametrize(
    ("field_name", "unresolved"),
    (
        ("model_id", ""),
        ("revision", "main"),
        ("artifact_digest", "pending"),
        ("dimension", 0),
        ("transformation_pipeline", "unknown"),
        ("pooling", ""),
        ("query_prefix", None),
        ("document_prefix", None),
        ("precision", ""),
        ("batch_size", 0),
    ),
)
def test_benchmark_refuses_every_unresolved_model_identity_field(
    field_name: str,
    unresolved: object,
) -> None:
    with pytest.raises(BenchmarkUnavailable, match="model identity is unresolved"):
        replace(_resolved_identity(), **cast(Any, {field_name: unresolved}))


def test_empty_document_prefix_is_an_explicit_resolved_identity() -> None:
    identity = _resolved_identity()

    assert identity.document_prefix == ""
    assert identity.dimension == 384
