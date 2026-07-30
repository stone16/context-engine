from __future__ import annotations

import pytest

from adapters.connectors.file import FileConnectorAdapter, PermissionObservationFailed
from engine.supply import ConnectorCheckpointBinding, SourceAclEvidenceClass
from tests.support.file_connector_twin import SyntheticVaultTwin


def test_local_vault_records_explicit_honest_weak_evidence() -> None:
    twin = SyntheticVaultTwin({"alpha.md": b"# Alpha\n"})
    adapter = FileConnectorAdapter.from_twin(twin)
    binding = ConnectorCheckpointBinding(
        organization_id=twin.organization_id,
        source_version_id=twin.source_version_id,
        worker_job_id=twin.worker_job_id,
    )
    adapter.load_checkpoint(None)

    page = adapter.load(binding)

    assert len(page.documents) == 1
    observation = page.documents[0].acl_observation
    assert observation.evidence_class is SourceAclEvidenceClass.WEAK
    assert observation.evidence_payload is None
    assert observation.source_lacks_stronger_acl == (
        "local File/Obsidian has no corpus ACL API"
    )


def test_failed_permission_observation_emits_no_article_or_checkpoint() -> None:
    twin = SyntheticVaultTwin(
        {"alpha.md": b"# Alpha\n"},
        fail_acl_for={"alpha.md"},
    )
    adapter = FileConnectorAdapter.from_twin(twin)
    binding = ConnectorCheckpointBinding(
        organization_id=twin.organization_id,
        source_version_id=twin.source_version_id,
        worker_job_id=twin.worker_job_id,
    )
    adapter.load_checkpoint(None)

    with pytest.raises(PermissionObservationFailed, match="unavailable"):
        adapter.load(binding)

    assert adapter.last_emitted_page is None
