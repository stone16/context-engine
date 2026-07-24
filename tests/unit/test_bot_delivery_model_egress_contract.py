from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
BOT_DELIVERY_ROOT = ROOT / "bot_delivery/typescript"


@pytest.mark.security_evidence(id="TS-MODEL-EGRESS-070", layer="property")
def test_typescript_model_egress_is_closed_pinned_and_zero_byte_on_denial() -> None:
    package = json.loads(
        (BOT_DELIVERY_ROOT / "package.json").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (BOT_DELIVERY_ROOT / "package-lock.json").read_text(encoding="utf-8")
    )

    assert package["private"] is True
    assert package["exports"] == {
        ".": {"types": "./dist/public.d.ts", "import": "./dist/public.js"}
    }
    assert package["engines"] == {"node": "22.12.0", "npm": "10.9.0"}
    assert package["packageManager"] == "npm@10.9.0"
    assert package["dependencies"] == {"canonicalize": "3.0.0", "pg": "8.22.0"}
    assert package["peerDependencies"] == {
        "@context-engine/action-plane": "0.0.0-m2-perform",
        "@context-engine/resolve-sdk": "0.0.0-v0"
    }
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    assert lock["packages"][""]["peerDependencies"] == package["peerDependencies"]
    assert (BOT_DELIVERY_ROOT / ".node-version").read_text(
        encoding="ascii"
    ).strip() == package["engines"]["node"]
    public_source = (BOT_DELIVERY_ROOT / "src/public.ts").read_text(encoding="utf-8")
    assert "ModelEgressDatabase" not in public_source
    assert "createModelGenerationBoundaryForTest" not in public_source
    live_integration = (
        ROOT / "tests/integration/test_z_egress_grant_file.py"
    ).read_text(encoding="utf-8")
    assert "local_production_dependencies" in live_integration
    assert '"optionalDependencies": local_optional_dependencies' in live_integration

    for cwd, command in (
        (ROOT / "sdk/typescript", ["npm", "run", "build"]),
        (BOT_DELIVERY_ROOT, ["npm", "run", "build"]),
        (BOT_DELIVERY_ROOT, ["npm", "run", "test:runtime"]),
    ):
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "tests 17" in completed.stdout
    assert "pass 17" in completed.stdout
    assert "fail 0" in completed.stdout
