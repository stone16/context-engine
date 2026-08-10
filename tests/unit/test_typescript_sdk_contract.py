from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SDK_ROOT = ROOT / "sdk/typescript"
SDK_V1_ROOT = ROOT / "sdk/typescript-v1"


def _generated_tree_digest() -> str:
    generated_root = SDK_ROOT / "src/generated"
    digest = sha256()
    digest.update(b"context-engine-generated-sdk-v1\0")
    generated_files = (
        candidate for candidate in generated_root.rglob("*") if candidate.is_file()
    )
    for path in sorted(
        generated_files,
        key=lambda candidate: candidate.relative_to(generated_root).as_posix(),
    ):
        contents = path.read_bytes()
        digest.update(path.relative_to(generated_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(str(len(contents)).encode("ascii"))
        digest.update(b"\0")
        digest.update(contents)
    return digest.hexdigest()


@pytest.mark.security_evidence(id="SDK-CONTRACT-064", layer="property")
def test_typescript_sdk_metadata_locks_contract_and_public_exports() -> None:
    package = json.loads((SDK_ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((SDK_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert package["contextEngineContract"] == {
        "version": "v0",
        "snapshot": "../../openapi/v0/openapi.json",
        "checksum": "../../openapi/v0/openapi.sha256",
        "generator": "@hey-api/openapi-ts@0.95.0",
    }
    assert package["devDependencies"] == {
        "@hey-api/openapi-ts": "0.95.0",
        "@types/node": "22.10.2",
        "typescript": "5.9.3",
    }
    assert package["exports"] == {
        ".": {"types": "./dist/index.d.ts", "import": "./dist/index.js"},
        "./contract/openapi-v0.sha256": "./contract/openapi-v0.sha256",
    }
    assert package["engines"] == {"node": "22.12.0", "npm": "10.9.0"}
    assert package["packageManager"] == "npm@10.9.0"
    assert lock["packages"][""]["devDependencies"] == package["devDependencies"]
    assert lock["packages"][""]["engines"] == package["engines"]
    assert (SDK_ROOT / ".node-version").read_text(encoding="ascii").strip() == (
        package["engines"]["node"]
    )
    assert (SDK_ROOT / "contract/openapi-v0.sha256").read_bytes() == (
        ROOT / "openapi/v0/openapi.sha256"
    ).read_bytes()
    recorded_generated_digest = (
        SDK_ROOT / "src/generated.sha256"
    ).read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", recorded_generated_digest)
    assert recorded_generated_digest == _generated_tree_digest()


@pytest.mark.security_evidence(id="SDK-CONTRACT-V1-217", layer="property")
def test_v1_sdk_is_independent_and_v0_facade_stays_frozen() -> None:
    package = json.loads((SDK_V1_ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["name"] == "@context-engine/resolve-sdk-v1"
    assert package["contextEngineContract"] == {
        "version": "v1",
        "snapshot": "../../openapi/v1/openapi.json",
        "checksum": "../../openapi/v1/openapi.sha256",
        "generator": "@hey-api/openapi-ts@0.95.0",
    }
    assert package["exports"] == {
        ".": {"types": "./dist/index.d.ts", "import": "./dist/index.js"},
        "./contract/openapi-v1.sha256": "./contract/openapi-v1.sha256",
    }
    assert sha256((SDK_ROOT / "src/index.ts").read_bytes()).hexdigest() == (
        "a1fa3e41037d7f97a9fea5e3e721cc435709e4ef327266e555d151ce4e7da207"
    )
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {
        "sdk/typescript-v1/node_modules/",
        "sdk/typescript-v1/dist/",
        "sdk/typescript-v1/*.tgz",
        "sdk/typescript-v1/*.tsbuildinfo",
        "sdk/typescript-v1/governance/",
        "sdk/typescript-v1/third_party/",
    } <= set(ignored)
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    sdk_pack = makefile.split("sdk-pack:\n", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
    assert sdk_pack.splitlines() == [
        "\tnpm --prefix sdk/typescript run pack:artifact",
        "\tnpm --prefix sdk/typescript-v1 run pack:artifact",
    ]
    sdk_generate = makefile.split("sdk-generate:\n", maxsplit=1)[1].split(
        "\n\n", maxsplit=1
    )[0]
    assert sdk_generate.splitlines() == [
        "\tnpm --prefix sdk/typescript run generate",
        "\tnpm --prefix sdk/typescript-v1 run generate",
    ]
