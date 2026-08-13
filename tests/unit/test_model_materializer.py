from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self, cast

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


def _open_descriptor_count() -> int:
    if sys.platform == "darwin":
        descriptor_directory = "/dev/fd"
    elif sys.platform.startswith("linux"):
        descriptor_directory = "/proc/self/fd"
    else:
        pytest.skip("descriptor table is unavailable")
    return len(os.listdir(descriptor_directory))


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


def test_production_transport_disables_automatic_redirects_and_ambient_auth(
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

    assert observed == [
        {
            "follow_redirects": False,
            "timeout": httpx.Timeout(60.0, connect=10.0),
            "trust_env": False,
            "headers": {"Accept": "application/octet-stream"},
        }
    ]


@pytest.mark.parametrize("failure_phase", ("build_request", "send"))
def test_transport_closes_client_when_request_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    close_calls = 0

    class FailingClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def build_request(self, method: str, url: str) -> object:
            del method, url
            if failure_phase == "build_request":
                raise OSError("synthetic request-build failure")
            return object()

        def send(self, request: object, *, stream: bool) -> object:
            del request, stream
            raise OSError("synthetic send failure")

        def close(self) -> None:
            nonlocal close_calls
            close_calls += 1

    monkeypatch.setattr(httpx, "Client", FailingClient)

    with pytest.raises(model_materializer.MaterializationRefused) as failure:
        model_materializer.HttpArtifactTransport.production().fetch(
            _snapshot(),
            "model.safetensors",
        )

    assert failure.value.category == "transport_refused"
    assert close_calls == 1


@pytest.mark.parametrize("flag_name", ("O_DIRECTORY", "O_NOFOLLOW"))
def test_materializer_requires_safe_descriptor_flags_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag_name: str,
) -> None:
    class UnusedTransport:
        def fetch(
            self,
            snapshot: model_materializer.RegisteredModelSnapshot,
            path: str,
        ) -> model_materializer.ArtifactResponse:
            del snapshot, path
            raise AssertionError("transport must remain unused")

    destination = tmp_path / "durable-model"
    monkeypatch.delattr(os, flag_name)

    with pytest.raises(model_materializer.MaterializationRefused) as failure:
        model_materializer.materialize_registered_snapshot(
            "primary",
            destination,
            snapshot_loader=lambda _role: _snapshot(),
            transport=UnusedTransport(),
        )

    assert failure.value.category == "destination_unavailable"
    assert failure.value.exit_code == 10
    assert not destination.exists()


def test_materializer_closes_parent_descriptor_after_destination_refusal(
    tmp_path: Path,
) -> None:
    class UnusedTransport:
        def fetch(
            self,
            snapshot: model_materializer.RegisteredModelSnapshot,
            path: str,
        ) -> model_materializer.ArtifactResponse:
            del snapshot, path
            raise AssertionError("transport must remain unused")

    destination = tmp_path / "durable-model"
    destination.mkdir()
    descriptors_before = _open_descriptor_count()

    for _attempt in range(32):
        with pytest.raises(model_materializer.MaterializationRefused) as failure:
            model_materializer.materialize_registered_snapshot(
                "primary",
                destination,
                snapshot_loader=lambda _role: _snapshot(),
                transport=UnusedTransport(),
            )
        assert failure.value.category == "destination_exists"

    assert _open_descriptor_count() == descriptors_before


def test_materializer_does_not_close_artifact_descriptor_after_file_owns_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InterruptedResponse:
        def content_length(self) -> str | None:
            return None

        def content_encoding(self) -> str | None:
            return None

        def iter_bytes(self, chunk_size: int) -> Iterator[bytes]:
            del chunk_size
            yield b"partial artifact"
            raise OSError("synthetic interrupted stream")

        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            del exc_type, exc_value, traceback

    class InterruptedTransport:
        def fetch(
            self,
            snapshot: model_materializer.RegisteredModelSnapshot,
            path: str,
        ) -> InterruptedResponse:
            del snapshot, path
            return InterruptedResponse()

    original_fdopen = os.fdopen
    original_close = os.close
    file_owned_descriptors: list[int] = []
    file_exit_descriptors: list[int] = []
    explicit_close_descriptors: list[int] = []

    class TrackingFile:
        def __init__(self, descriptor: int, handle: BinaryIO) -> None:
            self._descriptor = descriptor
            self._handle = handle

        def __enter__(self) -> BinaryIO:
            return self._handle

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool | None:
            file_exit_descriptors.append(self._descriptor)
            return self._handle.__exit__(exc_type, exc_value, traceback)

    def tracking_fdopen(
        descriptor: int,
        mode: str,
        *,
        closefd: bool,
    ) -> TrackingFile:
        file_owned_descriptors.append(descriptor)
        return TrackingFile(
            descriptor,
            cast(BinaryIO, original_fdopen(descriptor, mode, closefd=closefd)),
        )

    def tracking_close(descriptor: int) -> None:
        explicit_close_descriptors.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(os, "fdopen", tracking_fdopen)
    monkeypatch.setattr(os, "close", tracking_close)

    with pytest.raises(model_materializer.MaterializationRefused) as failure:
        model_materializer.materialize_registered_snapshot(
            "primary",
            tmp_path / "durable-model",
            snapshot_loader=lambda _role: _snapshot(),
            transport=InterruptedTransport(),
        )

    assert failure.value.category == "transport_refused"
    assert len(file_owned_descriptors) == 1
    assert file_exit_descriptors == file_owned_descriptors
    assert file_owned_descriptors[0] not in explicit_close_descriptors
