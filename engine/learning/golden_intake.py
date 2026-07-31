"""Governed case admission through the existing golden pilot lock."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from engine.learning.curation_candidate import curation_candidate_case
from engine.learning.golden import (
    GoldenSetUnavailable,
    load_golden_set,
    validate_golden_document_schema,
)


@dataclass(frozen=True, slots=True)
class GoldenIntakeReceipt:
    """Content-free receipt for one case admitted without changing the lock."""

    case_ref: str
    case_count: int
    golden_digest: str
    pilot_digest: str


def _load_candidate(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise GoldenSetUnavailable("curation candidate intake is unavailable") from None
    if type(value) is not dict:
        raise GoldenSetUnavailable("curation candidate intake is malformed")
    return cast(dict[str, object], value)


def _write_private_staged(path: Path, document: dict[str, object]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(
                json.dumps(
                    document,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def admit_evaluation_case(
    candidate_path: Path,
    *,
    golden_path: Path,
    lock_path: Path,
) -> GoldenIntakeReceipt:
    """Append one candidate dev case only after verifying the active pilot lock."""

    if not all(
        isinstance(path, Path) for path in (candidate_path, golden_path, lock_path)
    ):
        raise TypeError("golden intake paths must be Path")
    locked = load_golden_set(golden_path, lock_path=lock_path)
    case = curation_candidate_case(_load_candidate(candidate_path))
    candidate_document = locked.document()
    entries = cast(list[object], candidate_document["entries"])
    entries.append(case.document())
    validate_golden_document_schema(candidate_document)
    if case.partition != "dev":
        raise GoldenSetUnavailable(
            "feedback intake admits dev cases; "
            "pilot admission requires explicit re-lock"
        )
    case_refs = {case.case_ref for case in locked.cases}
    if case.case_ref in case_refs:
        raise GoldenSetUnavailable("evaluation intake caseRef already exists")
    combined_document = locked.document()
    combined_entries = cast(list[object], combined_document["entries"])
    combined_entries.append(case.document())
    staged = golden_path.with_name(f".{golden_path.name}.intake")
    staged_created = False
    try:
        _write_private_staged(staged, combined_document)
        staged_created = True
        admitted = load_golden_set(staged, lock_path=lock_path)
        if admitted.pilot_digest != locked.pilot_digest:
            raise GoldenSetUnavailable("evaluation intake changed the locked pilot")
        staged.replace(golden_path)
        directory = os.open(golden_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except GoldenSetUnavailable:
        raise
    except OSError:
        raise GoldenSetUnavailable("evaluation intake write is unavailable") from None
    finally:
        if staged_created:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                raise GoldenSetUnavailable(
                    "evaluation intake cleanup is unavailable"
                ) from None
    return GoldenIntakeReceipt(
        case_ref=case.case_ref,
        case_count=len(admitted.cases),
        golden_digest=admitted.digest,
        pilot_digest=admitted.pilot_digest,
    )
