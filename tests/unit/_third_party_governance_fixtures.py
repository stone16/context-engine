from __future__ import annotations

import hashlib
import json
from pathlib import Path


def write_fixture_tree(root: Path, schema_source: Path) -> Path:
    (root / "schemas").mkdir(parents=True)
    (root / "schemas/third-party-upstream.schema.json").write_bytes(
        schema_source.read_bytes()
    )
    (root / "schemas/third-party-artifact-exemptions.schema.json").write_bytes(
        (schema_source.parent / "third-party-artifact-exemptions.schema.json")
        .read_bytes()
    )
    subtree = root / "third_party/example"
    (subtree / "src").mkdir(parents=True)
    (subtree / "patches").mkdir()
    vendored = subtree / "src/example.py"
    vendored.write_text(
        "def selected() -> int:\n"
        "    return 1\n"
        "\n"
        "\n"
        "def other() -> int:\n"
        "    return 2\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(vendored.read_bytes()).hexdigest()
    (subtree / "LICENSE.upstream").write_text("MIT fixture license\n", encoding="utf-8")
    (subtree / "MODIFICATIONS.md").write_text("# No modifications\n", encoding="utf-8")
    (subtree / "patches/example.patch").write_text(
        "--- a/src/example.py\n"
        "+++ b/src/example.py\n"
        "@@ -1,5 +1,5 @@\n"
        " def selected() -> int:\n"
        "-    return 0\n"
        "+    return 1\n"
        " \n"
        " \n"
        " def other() -> int:\n",
        encoding="utf-8",
    )
    (subtree / "sbom.cyclonedx.json").write_text("{}\n", encoding="utf-8")
    (root / "third_party/ARTIFACT_EXEMPTIONS.toml").write_text(
        "schema_version = 1\nexemptions = []\n",
        encoding="utf-8",
    )
    registration = f'''repository = "https://example.invalid/upstream.git"
commit = "0123456789abcdef0123456789abcdef01234567"
source_paths = ["src/example.py"]
excluded_paths = ["src/private"]
reuse_mode = "copy-patch"
approvals = [{{ reference = "issue-1", source_paths = ["src/example.py"] }}]
license = "MIT"

[[files]]
upstream_path = "src/example.py"
vendored_path = "third_party/example/src/example.py"
sha256 = "{digest}"
'''
    path = subtree / "UPSTREAM.toml"
    path.write_text(registration, encoding="utf-8")
    for name, content in {
        "LICENSE": "project license\n",
        "NOTICE": "project notice\n",
        "THIRD_PARTY_NOTICES.md": "third party notices\n",
        "THIRD_PARTY_SBOM.cyclonedx.json": json.dumps(
            {"components": [{"bom-ref": "context-engine:third-party:example"}]}
        )
        + "\n",
    }.items():
        (root / name).write_text(content, encoding="utf-8")
    return path
