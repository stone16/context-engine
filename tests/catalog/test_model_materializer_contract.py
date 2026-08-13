from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).parents[2]
SCHEMA = json.loads(
    (
        ROOT
        / "docs"
        / "contracts"
        / "context-engine-model-materializer-v1.schema.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    ("status", "category"),
    (
        ("materialized", "ready"),
        ("refused", "registry_unavailable"),
        ("refused", "destination_unavailable"),
        ("refused", "transport_refused"),
        ("refused", "verification_refused"),
        ("refused", "destination_exists"),
        ("refused", "publication_refused"),
        ("refused", "operation_refused"),
    ),
)
def test_model_materializer_closed_results_validate(
    status: str,
    category: str,
) -> None:
    jsonschema.validate(
        {
            "schemaVersion": "context-engine-model-materializer-v1",
            "service": "context-engine-model-materializer",
            "status": status,
            "category": category,
        },
        SCHEMA,
    )


@pytest.mark.parametrize(
    "field",
    (
        "modelId",
        "revision",
        "artifactDigest",
        "artifact",
        "filename",
        "path",
        "destination",
        "url",
        "credential",
        "exception",
    ),
)
def test_model_materializer_schema_forbids_content_bearing_fields(field: str) -> None:
    document = {
        "schemaVersion": "context-engine-model-materializer-v1",
        "service": "context-engine-model-materializer",
        "status": "refused",
        "category": "operation_refused",
        field: "private-canary",
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(document, SCHEMA)
