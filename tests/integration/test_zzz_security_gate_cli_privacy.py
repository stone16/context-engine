from __future__ import annotations

import re
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
_ABSOLUTE_PATHS = (
    re.compile(r"(?<![:A-Za-z0-9_])/(?:[^\s/]+/)*[^\s/]+"),
    re.compile(r"[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s\\]+"),
)
_PRIVATE_ROOT_FRAGMENT_DIGESTS = frozenset(
    {"c83ea566573fdfcacb79f350f86ea53935437f5672e1fe97703320cce4725394"}
)


def _word_sequence_digests(value: str) -> set[str]:
    words = tuple(
        word.casefold()
        for word in re.findall(r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+", value)
    )
    return {
        sha256("".join(words[index:end]).encode("utf-8")).hexdigest()
        for index in range(len(words))
        for end in range(index + 1, min(index + 4, len(words)) + 1)
    }


@pytest.mark.integration
def test_real_security_gate_cli_output_is_private_and_portable(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        (
            sys.executable,
            "scripts/run_m0_security_gate.py",
            "--output-dir",
            str(tmp_path / "gate-evidence"),
        ),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0
    assert output == "M0 SECURITY PASS\n"
    assert all(pattern.search(output) is None for pattern in _ABSOLUTE_PATHS)
    assert not (_PRIVATE_ROOT_FRAGMENT_DIGESTS & _word_sequence_digests(output))
