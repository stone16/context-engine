from __future__ import annotations

from hashlib import sha256

import httpx
import pytest
import rfc8785

from applications import model_materializer


def _snapshot() -> model_materializer.RegisteredModelSnapshot:
    artifacts = (("model.safetensors", sha256(b"synthetic").hexdigest()),)
    manifest = [{"path": path, "sha256": digest} for path, digest in artifacts]
    return model_materializer.RegisteredModelSnapshot(
        model_id="synthetic/model",
        revision="a" * 40,
        artifact_digest=sha256(rfc8785.dumps(manifest)).hexdigest(),
        artifacts=artifacts,
    )


def test_production_transport_accepts_only_registered_https_host_family() -> None:
    transport = model_materializer.HttpArtifactTransport.production()

    assert transport._url_allowed("https://huggingface.co/cache/object")
    assert transport._url_allowed("https://us.aws.cdn.hf.co/xet/object?signature=x")
    for refused in (
        "http://huggingface.co/cache/object",
        "https://huggingface.co:444/cache/object",
        "https://credential@huggingface.co/cache/object",
        "https://huggingface.co.example.invalid/cache/object",
        "https://cdn.hf.co.example.invalid/cache/object",
        "https://cdn.hf.co/cache/object#fragment",
    ):
        assert not transport._url_allowed(refused)


def test_production_transport_ignores_ambient_proxy_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, object]] = []

    class RefusingClient:
        def __init__(self, **kwargs: object) -> None:
            observed.append(kwargs)
            raise ValueError("synthetic stop before network")

    monkeypatch.setattr(httpx, "Client", RefusingClient)

    with pytest.raises(model_materializer.MaterializationRefused):
        model_materializer.HttpArtifactTransport.production().fetch(
            _snapshot(),
            "model.safetensors",
        )

    assert len(observed) == 1
    assert observed[0]["trust_env"] is False
