"""Owned local/acceptance runner for DOCX and PDF-outline profiles."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Final, Protocol, cast

from adapters.parsers.ragflow_documents import compile_document_bytes, profile_for_ref
from engine.supply import (
    CompilationProfileRef,
    DocumentCompilationFailure,
    DocumentCompilationFailureCode,
    ParsedDocument,
    canonicalize_parsed_document,
    deserialize_parsed_document,
)
from eval._compiler_acceptance import (
    _AcceptanceContext,
    is_acceptance_context,
)

MAX_DOCUMENT_ARTIFACT_BYTES: Final = 32 * 1024 * 1024
DOCUMENT_RUNNER_TIMEOUT_SECONDS: Final = 30.0
_RUNNER_MODULE: Final = "applications.document_compiler_runner"
_REPOSITORY_ROOT: Final = Path(__file__).parents[1]


class ArtifactSource(ABC):
    """Artifact byte port opened only after exact profile selection."""

    @abstractmethod
    def read(self) -> bytes:
        """Return the complete bounded artifact."""


class BytesArtifactSource(ArtifactSource):
    """In-memory artifact source for acceptance and tests."""

    def __init__(self, payload: bytes) -> None:
        if type(payload) is not bytes:
            raise TypeError("artifact payload must be exact bytes")
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


type DocumentCompilationOutcome = (
    ParsedDocument[CompilationProfileRef] | DocumentCompilationFailure
)


class _AcceptanceEntryPoint(Protocol):
    def __call__(
        self,
        source: ArtifactSource,
        profile_ref: str,
        *,
        acceptance_context: _AcceptanceContext,
    ) -> DocumentCompilationOutcome: ...


def _failure(code: DocumentCompilationFailureCode) -> DocumentCompilationFailure:
    return DocumentCompilationFailure(code)


def _bounded_source(source: ArtifactSource) -> bytes | DocumentCompilationFailure:
    payload = source.read()
    if type(payload) is not bytes:
        raise TypeError("artifact source must return exact bytes")
    if not payload:
        return _failure(DocumentCompilationFailureCode.INVALID_ARTIFACT)
    if len(payload) > MAX_DOCUMENT_ARTIFACT_BYTES:
        return _failure(DocumentCompilationFailureCode.ARTIFACT_BOUND_EXCEEDED)
    return payload


def _require_acceptance_context(
    entry_point: _AcceptanceEntryPoint,
) -> _AcceptanceEntryPoint:
    def guarded(
        source: ArtifactSource,
        profile_ref: str,
        *,
        acceptance_context: _AcceptanceContext | None = None,
    ) -> DocumentCompilationOutcome:
        if is_acceptance_context(acceptance_context):
            return entry_point(
                source,
                profile_ref,
                acceptance_context=cast(_AcceptanceContext, acceptance_context),
            )
        return _failure(DocumentCompilationFailureCode.RUNNER_UNAVAILABLE)

    return cast(_AcceptanceEntryPoint, guarded)


@_require_acceptance_context
def compile_in_local_document_runner(
    source: ArtifactSource,
    profile_ref: str,
    *,
    acceptance_context: _AcceptanceContext,
) -> DocumentCompilationOutcome:
    """Compile in an unleased local process that production must never call."""

    assert is_acceptance_context(acceptance_context)
    if not isinstance(source, ArtifactSource):
        raise TypeError("document runner requires an ArtifactSource")
    if profile_for_ref(profile_ref) is None:
        return _failure(DocumentCompilationFailureCode.UNKNOWN_PROFILE)
    payload = _bounded_source(source)
    if type(payload) is DocumentCompilationFailure:
        return payload
    assert type(payload) is bytes
    try:
        completed = subprocess.run(
            [sys.executable, "-m", _RUNNER_MODULE, "--profile", profile_ref],
            input=payload,
            capture_output=True,
            check=False,
            cwd=_REPOSITORY_ROOT,
            env={"PYTHONPATH": str(_REPOSITORY_ROOT)},
            timeout=DOCUMENT_RUNNER_TIMEOUT_SECONDS,
        )
    except Exception:
        return _failure(DocumentCompilationFailureCode.RUNNER_UNAVAILABLE)
    if completed.returncode != 0:
        return _failure(DocumentCompilationFailureCode.RUNNER_UNAVAILABLE)
    try:
        raw = json.loads(completed.stdout)
        if type(raw) is not dict:
            raise ValueError("runner envelope must be an object")
        envelope = cast(dict[str, object], raw)
        if envelope.get("outcome") == "failure":
            failure_value = envelope.get("failure")
            if type(failure_value) is not dict:
                raise ValueError("runner failure must be an object")
            failure = cast(dict[str, object], failure_value)
            if set(failure) != {"code"}:
                raise ValueError("runner failure shape is not closed")
            return _failure(DocumentCompilationFailureCode(cast(str, failure["code"])))
        if envelope.get("outcome") != "parsed":
            raise ValueError("runner envelope outcome is not closed")
        encoded = envelope.get("document")
        if type(encoded) is not str:
            raise ValueError("runner document must be encoded text")
        return deserialize_parsed_document(base64.b64decode(encoded, validate=True))
    except Exception:
        return _failure(DocumentCompilationFailureCode.RUNNER_UNAVAILABLE)


def _emit(source: bytes, profile: CompilationProfileRef) -> None:
    if not source:
        outcome: DocumentCompilationOutcome = _failure(
            DocumentCompilationFailureCode.INVALID_ARTIFACT
        )
    elif len(source) > MAX_DOCUMENT_ARTIFACT_BYTES:
        outcome = _failure(DocumentCompilationFailureCode.ARTIFACT_BOUND_EXCEEDED)
    else:
        try:
            outcome = compile_document_bytes(source, profile)
        except Exception:
            outcome = _failure(DocumentCompilationFailureCode.INVALID_ARTIFACT)
    if type(outcome) is ParsedDocument:
        envelope: dict[str, object] = {
            "outcome": "parsed",
            "document": base64.b64encode(
                canonicalize_parsed_document(outcome)
            ).decode("ascii"),
        }
    else:
        assert type(outcome) is DocumentCompilationFailure
        envelope = {"outcome": "failure", "failure": {"code": outcome.code.value}}
    sys.stdout.write(json.dumps(envelope, sort_keys=True, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    profile = profile_for_ref(cast(str, args.profile))
    if profile is None:
        envelope = {
            "outcome": "failure",
            "failure": {"code": DocumentCompilationFailureCode.UNKNOWN_PROFILE.value},
        }
        sys.stdout.write(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
        return
    _emit(sys.stdin.buffer.read(MAX_DOCUMENT_ARTIFACT_BYTES + 1), profile)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("document compiler runner operation failed") from None


__all__: list[str] = []
