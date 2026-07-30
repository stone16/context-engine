"""Single-question local consumer for fresh loopback ContextPackages."""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Protocol
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from adapters.http.contracts import (
    RequestNotAvailableWire,
    ResolutionOutcomeWire,
    ResolvedWire,
)
from applications.dogfood_evaluation import (
    MAX_QUERY_CHARACTERS,
    DogfoodEvaluationUnavailable,
    DogfoodHttpConfiguration,
    DogfoodResolveClient,
    DogfoodSecretExclusionUnavailable,
)
from engine.learning.golden_storage import durable_golden_root

LOCAL_CANDIDATE_SCHEMA_VERSION: Final = "context-engine-golden-candidate-v1"
LOCAL_CANDIDATE_FILENAME: Final = "claude-code-candidates-v1.jsonl"
REFUSAL_PREFIX: Final = "Authorized context is unavailable for this question."
_OUTCOME_ADAPTER: Final[TypeAdapter[ResolutionOutcomeWire]] = TypeAdapter(
    ResolutionOutcomeWire
)


class LocalContextUnavailable(RuntimeError):
    """The local consumer cannot complete its closed delivery contract."""


class LocalResolveCaller(Protocol):
    """One redacted caller of the frozen loopback Acquire operation."""

    def resolve_acquire(
        self,
        *,
        query: str,
        request_id: str,
    ) -> dict[str, object]: ...

    def reject_secret_material(self, value: object) -> None: ...


class CandidateRecorder(Protocol):
    """Private question-only golden-candidate capture."""

    def record(
        self,
        *,
        question: str,
        disposition: str,
        captured_at: datetime,
    ) -> None: ...


def _refusal(code: str) -> str:
    return f"{REFUSAL_PREFIX}\nrefusal: {code}"


def _new_request_id() -> str:
    return f"claude-skill-{uuid4().hex}"


def _new_candidate_ref() -> str:
    return f"candidate_{uuid4().hex}"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _question(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
        or len(value) > MAX_QUERY_CHARACTERS
    ):
        raise LocalContextUnavailable("local question is unavailable")
    return value


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LocalContextUnavailable("Package expiry is unavailable")
    return value.astimezone(UTC)


def _write_all(file_descriptor: int, content: bytes) -> None:
    position = 0
    while position < len(content):
        written = os.write(file_descriptor, content[position:])
        if written <= 0:
            raise OSError("candidate write failed")
        position += written


class DurableGoldenCandidateRecorder:
    """Append private questions under the ADR-0082 durable golden root."""

    __slots__ = ("_candidate_ref_factory",)

    def __init__(
        self,
        *,
        candidate_ref_factory: Callable[[], str] = _new_candidate_ref,
    ) -> None:
        self._candidate_ref_factory = candidate_ref_factory

    def record(
        self,
        *,
        question: str,
        disposition: str,
        captured_at: datetime,
    ) -> None:
        question = _question(question)
        if (
            type(disposition) is not str
            or not disposition
            or not disposition.isascii()
            or not disposition.replace("_", "").isupper()
        ):
            raise LocalContextUnavailable("candidate disposition is unavailable")
        instant = _utc_datetime(captured_at)
        document = {
            "candidateRef": self._candidate_ref_factory(),
            "capturedAt": instant.isoformat().replace("+00:00", "Z"),
            "question": question,
            "resolveDisposition": disposition,
            "schemaVersion": LOCAL_CANDIDATE_SCHEMA_VERSION,
        }
        content = (
            json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        try:
            path = durable_golden_root() / LOCAL_CANDIDATE_FILENAME
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            file_descriptor = os.open(path, flags, 0o600)
            try:
                metadata = os.fstat(file_descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                ):
                    raise OSError("candidate store is unavailable")
                _write_all(file_descriptor, content)
                os.fsync(file_descriptor)
            finally:
                os.close(file_descriptor)
        except (OSError, ValueError):
            raise LocalContextUnavailable(
                "golden candidate capture is unavailable"
            ) from None


class LocalContextConsumer:
    """Consume exactly one question and retain no Package-derived state."""

    __slots__ = (
        "_caller",
        "_candidate_recorder",
        "_consumed",
        "_now",
        "_request_id_factory",
    )

    def __init__(
        self,
        *,
        caller: LocalResolveCaller,
        candidate_recorder: CandidateRecorder,
        now: Callable[[], datetime] = _now,
        request_id_factory: Callable[[], str] = _new_request_id,
    ) -> None:
        self._caller = caller
        self._candidate_recorder = candidate_recorder
        self._now = now
        self._request_id_factory = request_id_factory
        self._consumed = False

    def consume(self, question: str) -> str:
        """Resolve, validate, capture, and render one fresh question."""

        if self._consumed:
            return _refusal("LOCAL_CONTEXT_REFUSAL_FRESH_RESOLVE_REQUIRED")
        self._consumed = True
        try:
            exact_question = _question(question)
            self._caller.reject_secret_material(exact_question)
        except DogfoodSecretExclusionUnavailable:
            return _refusal("LOCAL_CONTEXT_REFUSAL_SECRET_EXCLUSION")
        except LocalContextUnavailable:
            return _refusal("LOCAL_CONTEXT_REFUSAL_MALFORMED_QUESTION")

        try:
            raw_outcome = self._caller.resolve_acquire(
                query=exact_question,
                request_id=self._request_id_factory(),
            )
            self._caller.reject_secret_material(raw_outcome)
        except DogfoodSecretExclusionUnavailable:
            return self._capture_then_refuse(
                question=exact_question,
                code="LOCAL_CONTEXT_REFUSAL_SECRET_EXCLUSION",
                captured_at=_utc_datetime(self._now()),
            )
        except DogfoodEvaluationUnavailable:
            return self._capture_then_refuse(
                question=exact_question,
                code="LOCAL_CONTEXT_REFUSAL_INVOCATION_UNAVAILABLE",
                captured_at=_utc_datetime(self._now()),
            )

        try:
            outcome = _OUTCOME_ADAPTER.validate_python(raw_outcome)
        except ValidationError:
            return self._capture_then_refuse(
                question=exact_question,
                code="LOCAL_CONTEXT_REFUSAL_MALFORMED_PACKAGE",
                captured_at=_utc_datetime(self._now()),
            )

        captured_at = _utc_datetime(self._now())
        if type(outcome) is RequestNotAvailableWire:
            return self._capture_then_refuse(
                question=exact_question,
                code="LOCAL_CONTEXT_REFUSAL_REQUEST_NOT_AVAILABLE",
                captured_at=captured_at,
            )
        if type(outcome) is not ResolvedWire:
            return self._capture_then_refuse(
                question=exact_question,
                code="LOCAL_CONTEXT_REFUSAL_MALFORMED_PACKAGE",
                captured_at=captured_at,
            )

        expires_at = _utc_datetime(outcome.package.expiresAt)
        if captured_at >= expires_at:
            return self._capture_then_refuse(
                question=exact_question,
                code="LOCAL_CONTEXT_REFUSAL_EXPIRED_PACKAGE",
                captured_at=captured_at,
            )
        if not outcome.package.blocks:
            return self._capture_then_refuse(
                question=exact_question,
                code="LOCAL_CONTEXT_REFUSAL_EMPTY_AUTHORIZED_SET",
                captured_at=captured_at,
            )

        try:
            rendered = _render_package(outcome, expires_at=expires_at)
            self._caller.reject_secret_material(rendered)
        except DogfoodSecretExclusionUnavailable:
            return self._capture_then_refuse(
                question=exact_question,
                code="LOCAL_CONTEXT_REFUSAL_SECRET_EXCLUSION",
                captured_at=captured_at,
            )
        except LocalContextUnavailable:
            return self._capture_then_refuse(
                question=exact_question,
                code="LOCAL_CONTEXT_REFUSAL_MALFORMED_PACKAGE",
                captured_at=captured_at,
            )
        return self._capture_then_return(
            question=exact_question,
            disposition="CONTEXT_RENDERED",
            captured_at=captured_at,
            rendered=rendered,
        )

    def _capture_then_refuse(
        self,
        *,
        question: str,
        code: str,
        captured_at: datetime,
    ) -> str:
        return self._capture_then_return(
            question=question,
            disposition=code,
            captured_at=captured_at,
            rendered=_refusal(code),
        )

    def _capture_then_return(
        self,
        *,
        question: str,
        disposition: str,
        captured_at: datetime,
        rendered: str,
    ) -> str:
        try:
            self._candidate_recorder.record(
                question=question,
                disposition=disposition,
                captured_at=captured_at,
            )
        except LocalContextUnavailable:
            return _refusal("LOCAL_CONTEXT_REFUSAL_CANDIDATE_CAPTURE_UNAVAILABLE")
        return rendered


def _render_package(outcome: ResolvedWire, *, expires_at: datetime) -> str:
    evidence_by_ref = {
        evidence.evidenceRef: evidence for evidence in outcome.package.evidence
    }
    if len(evidence_by_ref) != len(outcome.package.evidence):
        raise LocalContextUnavailable("Package Evidence closure is unavailable")
    lines = [
        "CONTEXT_ENGINE_PACKAGE",
        f"expiresAt: {expires_at.isoformat().replace('+00:00', 'Z')}",
    ]
    for ordinal, block in enumerate(outcome.package.blocks, start=1):
        if len(block.evidenceRefs) != 1:
            raise LocalContextUnavailable("Package Evidence closure is unavailable")
        evidence_ref = block.evidenceRefs[0]
        evidence = evidence_by_ref.get(evidence_ref)
        if evidence is None:
            raise LocalContextUnavailable("Package Evidence closure is unavailable")
        lines.extend(
            (
                "",
                f"BLOCK {ordinal}",
                f"evidenceRef: {evidence_ref}",
                f"text: {block.text}",
                "citationLineage:",
                f"  sourceRef: {evidence.sourceRef}",
                f"  resourceRef: {evidence.resourceRef}",
                f"  revisionRef: {evidence.revisionRef}",
                f"  fragmentRef: {evidence.fragmentRef}",
            )
        )
        if evidence.citationOpenRef is not None:
            lines.append(
                f"  citationOpenRef: {evidence.citationOpenRef} (display-only)"
            )
    return "\n".join(lines)


def _read_question() -> str:
    value = sys.stdin.read(MAX_QUERY_CHARACTERS + 2)
    if len(value) > MAX_QUERY_CHARACTERS + 1:
        raise LocalContextUnavailable("local question is unavailable")
    return _question(value.rstrip("\r\n"))


def main() -> None:
    """Read one stdin-only question and emit only a rendered package or refusal."""

    if len(sys.argv) != 1:
        print(_refusal("LOCAL_CONTEXT_REFUSAL_ARGUMENTS_FORBIDDEN"))
        raise SystemExit(1)
    try:
        question = _read_question()
        configuration = DogfoodHttpConfiguration.load()
        rendered = LocalContextConsumer(
            caller=DogfoodResolveClient(configuration),
            candidate_recorder=DurableGoldenCandidateRecorder(),
        ).consume(question)
    except (DogfoodEvaluationUnavailable, LocalContextUnavailable, ValueError):
        rendered = _refusal("LOCAL_CONTEXT_REFUSAL_INVOCATION_UNAVAILABLE")
    print(rendered)
    if rendered.startswith(REFUSAL_PREFIX):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
