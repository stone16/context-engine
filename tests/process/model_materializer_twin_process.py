"""Test-only process composition for the deterministic materializer twin."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import cast

from adapters.local_embedding_model import verify_model_artifacts_descriptor
from applications.model_materializer import (
    HttpArtifactTransport,
    RegisteredModelSnapshot,
    main,
)


def _snapshot(path: Path) -> RegisteredModelSnapshot:
    document = cast(dict[str, object], json.loads(path.read_bytes()))
    return RegisteredModelSnapshot(
        model_id=cast(str, document["modelId"]),
        revision=cast(str, document["revision"]),
        artifact_digest=cast(str, document["artifactDigest"]),
        artifacts=tuple(
            (cast(str, item["path"]), cast(str, item["sha256"]))
            for value in cast(list[object], document["artifacts"])
            for item in [cast(dict[str, object], value)]
        ),
    )


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--staging-attack",
        choices=(
            "none",
            "directory",
            "symlink",
            "fifo",
            "destination-race",
            "parent-swap",
            "staging-swap",
            "interrupt",
        ),
        default="none",
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    app_arguments = arguments.arguments
    if app_arguments[:1] == ["--"]:
        app_arguments = app_arguments[1:]

    def verifier(
        staging_descriptor: int,
        artifacts: tuple[tuple[str, str], ...],
        artifact_digest: str,
    ) -> None:
        if arguments.staging_attack == "directory":
            os.mkdir("unregistered", dir_fd=staging_descriptor)
        elif arguments.staging_attack == "symlink":
            os.symlink(
                artifacts[0][0],
                "unregistered",
                dir_fd=staging_descriptor,
            )
        elif arguments.staging_attack == "fifo":
            os.mkfifo("unregistered", dir_fd=staging_descriptor)
        verify_model_artifacts_descriptor(
            staging_descriptor,
            artifacts,
            artifact_digest,
        )
        destination_index = app_arguments.index("--destination") + 1
        destination = Path(app_arguments[destination_index])
        if arguments.staging_attack == "destination-race":
            destination.mkdir()
            (destination / "malicious.bin").write_bytes(b"malicious-race-canary\n")
        elif arguments.staging_attack == "parent-swap":
            retained_parent = destination.parent.with_name("retained-parent")
            destination.parent.rename(retained_parent)
            destination.parent.mkdir()
            (destination.parent / "malicious.bin").write_bytes(
                b"malicious-parent-canary\n"
            )
        elif arguments.staging_attack == "staging-swap":
            retained = os.fstat(staging_descriptor)
            staging = next(
                child
                for path in destination.parent.glob(".context-engine-model-work-*")
                for child in path.glob(".snapshot-*")
                if (child.stat().st_dev, child.stat().st_ino)
                == (retained.st_dev, retained.st_ino)
            )
            staging.rename(destination.parent / "retained-verified-staging")
            staging.mkdir()
            (staging / "model.safetensors").write_bytes(b"unverified swap bytes\n")
        elif arguments.staging_attack == "interrupt":
            raise KeyboardInterrupt

    main(
        app_arguments,
        snapshot_loader=lambda _role: _snapshot(arguments.manifest),
        transport=HttpArtifactTransport.for_test_twin(arguments.base_url),
        verifier=verifier,
    )


if __name__ == "__main__":
    run()
