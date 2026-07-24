from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
BOT_ROOT = ROOT / "bot_delivery/typescript"


@pytest.mark.security_evidence(id="TS-PRIVATE-BOT-FLOW-071", layer="property")
def test_private_bot_application_has_one_closed_process_and_import_boundary() -> None:
    package = json.loads((BOT_ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["version"] == "0.0.0-m2-private-flow"
    assert package["bin"] == {"context-engine-bot": "./dist/main.js"}
    assert package["peerDependencies"] == {
        "@context-engine/action-plane": "0.0.0-m2-perform",
        "@context-engine/resolve-sdk": "0.0.0-v0",
    }
    assert package["scripts"]["start"] == "node dist/main.js"
    assert package["scripts"]["test:runtime"] == (
        "node --test test/model-egress.test.mjs test/private-delivery.test.mjs"
    )

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((BOT_ROOT / "src").glob("*.ts"))
    )
    assert '@context-engine/resolve-sdk"' in source
    assert '@context-engine/action-plane"' in source
    import_lines = "\n".join(
        line
        for line in source.splitlines()
        if " from " in line or line.lstrip().startswith("import(")
    )
    for forbidden in (
        '"engine/',
        "migrations/",
        "AuthorizationKernel",
        "repositories/",
        "applications.api",
        "adapters.http",
        "node:http.request",
        "node:https.request",
    ):
        assert forbidden not in import_lines

    main = (BOT_ROOT / "src/main.ts").read_text(encoding="utf-8")
    assert "context-engine-bot" in main
    assert "BotDelivery + ActionPlane" in main
    assert "dispatchTwinEvent" in main
    assert "for await (const line of lines)" in main

    worker = (ROOT / "applications/worker.py").read_text(encoding="utf-8")
    assert '"--run-file-job"' in worker
    assert "PostgreSQLFileImportWorker" in worker
    assert "FileImportLeaseRedemption" in worker

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "typecheck: sdk-build action-build" in makefile


def test_public_bot_contract_does_not_export_trusted_fact_or_intent_factories() -> None:
    public_source = (BOT_ROOT / "src/public.ts").read_text(encoding="utf-8")

    assert "BotDelivery" in public_source
    assert "VerifiedQuestionTurn" in public_source
    assert "VerifiedCitationOpen" in public_source
    assert "DeliveryReceipt" in public_source
    assert "createTrustedPrivateEffectAuthority" not in public_source
    assert "createPlaceholderEffectIntent" not in public_source
    assert "TrustedDeliveryContext" not in public_source
    assert "TrustedEffectIntent" not in public_source

    action_public_source = (
        ROOT / "action_plane/typescript/src/index.ts"
    ).read_text(encoding="utf-8")
    assert "createPrivateBotActionBridge" not in action_public_source
    assert "TrustedPrivateEffectFacts" not in action_public_source
