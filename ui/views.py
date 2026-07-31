"""Strict tenant-safe view models derived from public wire documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast


class PublicDocumentInvalid(ValueError):
    """The public response cannot be rendered as clean authorized content."""


def _required_text(document: dict[str, object], name: str) -> str:
    value = document.get(name)
    if type(value) is not str or not value or value.isspace():
        raise PublicDocumentInvalid
    return value


@dataclass(frozen=True, slots=True)
class EvidenceView:
    evidence_ref: str
    source_ref: str
    resource_ref: str
    revision_ref: str
    fragment_ref: str
    policy_epoch: int
    citation_open_ref: str | None


@dataclass(frozen=True, slots=True)
class HitView:
    ordinal: int
    body: str
    evidence: EvidenceView
    score_status: str = "not_exposed_by_rank_free_public_contract"


@dataclass(frozen=True, slots=True)
class HitTestView:
    run_ref: str
    query: str
    coverage_status: str
    coverage_reason: str | None
    hits: tuple[HitView, ...]


@dataclass(frozen=True, slots=True)
class ProfileIdentityView:
    profile_ref: str
    digest: str


@dataclass(frozen=True, slots=True)
class ProfilesView:
    release_generation: int
    release_manifest_ref: str
    content_profile: ProfileIdentityView
    index_profile: ProfileIdentityView
    runtime_profile: ProfileIdentityView


@dataclass(frozen=True, slots=True)
class SourceHealthView:
    source_ref: str
    display_name: str
    status: str
    active_resource_count: int
    last_successful_acquisition_age_seconds: int | None
    refusal_categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OverviewView:
    release_generation: int
    release_manifest_ref: str
    sources: tuple[SourceHealthView, ...]


@dataclass(frozen=True, slots=True)
class ImportFragmentView:
    fragment_ref: str
    text: str


@dataclass(frozen=True, slots=True)
class ImportPreviewView:
    source_ref: str
    path: str
    compilation_digest: str
    fragment_digest: str
    preview_token: str
    fragments: tuple[ImportFragmentView, ...]


@dataclass(frozen=True, slots=True)
class ImportScanHandoffView:
    source_ref: str
    path: str
    scan_command: str
    worker_command: str


@dataclass(frozen=True, slots=True)
class ArticleView:
    resource_ref: str
    source_ref: str
    policy_version: int
    policy_epoch: int
    local_policy_kind: str | None
    local_group_refs: tuple[str, ...]
    effective_policy_kind: str | None
    effective_group_refs: tuple[str, ...]
    published: bool
    resolution_rung: str


def hit_test_view(document: dict[str, object], *, query: str) -> HitTestView:
    """Close Blocks over exact Evidence; reject ambiguous or incomplete lineage."""

    if document.get("kind") != "resolved":
        raise PublicDocumentInvalid
    package = document.get("package")
    if type(package) is not dict:
        raise PublicDocumentInvalid
    coverage = package.get("coverage")
    blocks = package.get("blocks")
    evidence_items = package.get("evidence")
    if type(coverage) is not dict or type(blocks) is not list:
        raise PublicDocumentInvalid
    if type(evidence_items) is not list:
        raise PublicDocumentInvalid
    coverage_status = _required_text(coverage, "status")
    coverage_reason_value = coverage.get("reason")
    if coverage_reason_value is not None and type(coverage_reason_value) is not str:
        raise PublicDocumentInvalid
    evidence_by_ref: dict[str, EvidenceView] = {}
    for item in evidence_items:
        if type(item) is not dict:
            raise PublicDocumentInvalid
        evidence_ref = _required_text(item, "evidenceRef")
        policy_epoch = item.get("policyEpoch")
        citation_ref = item.get("citationOpenRef")
        if (
            type(policy_epoch) is not int
            or policy_epoch < 1
            or (citation_ref is not None and type(citation_ref) is not str)
            or evidence_ref in evidence_by_ref
        ):
            raise PublicDocumentInvalid
        evidence_by_ref[evidence_ref] = EvidenceView(
            evidence_ref=evidence_ref,
            source_ref=_required_text(item, "sourceRef"),
            resource_ref=_required_text(item, "resourceRef"),
            revision_ref=_required_text(item, "revisionRef"),
            fragment_ref=_required_text(item, "fragmentRef"),
            policy_epoch=policy_epoch,
            citation_open_ref=citation_ref,
        )
    hits: list[HitView] = []
    observed_refs: set[str] = set()
    for ordinal, block in enumerate(blocks, start=1):
        if type(block) is not dict:
            raise PublicDocumentInvalid
        body = _required_text(block, "text")
        refs = block.get("evidenceRefs")
        if (
            type(refs) is not list
            or len(refs) != 1
            or type(refs[0]) is not str
            or refs[0] in observed_refs
        ):
            raise PublicDocumentInvalid
        evidence = evidence_by_ref.get(refs[0])
        if evidence is None:
            raise PublicDocumentInvalid
        observed_refs.add(refs[0])
        hits.append(HitView(ordinal=ordinal, body=body, evidence=evidence))
    if observed_refs != set(evidence_by_ref):
        raise PublicDocumentInvalid
    if coverage_status == "sufficient" and not hits:
        raise PublicDocumentInvalid
    if coverage_status == "empty" and hits:
        raise PublicDocumentInvalid
    return HitTestView(
        run_ref=_required_text(package, "runRef"),
        query=query,
        coverage_status=coverage_status,
        coverage_reason=coverage_reason_value,
        hits=tuple(hits),
    )


def ask_view(document: dict[str, object], *, query: str) -> HitTestView:
    """Require complete openable citation lineage for every clean answer Block."""

    view = hit_test_view(document, query=query)
    if any(
        type(hit.evidence.citation_open_ref) is not str
        or not hit.evidence.citation_open_ref
        or hit.evidence.citation_open_ref.isspace()
        for hit in view.hits
    ):
        raise PublicDocumentInvalid
    return view


def verify_citation_lineage(
    answer: HitTestView,
    opened: dict[str, dict[str, object]],
) -> HitTestView:
    """Require every locator to resolve to the exact rendered Article lineage."""

    expected_refs = {
        hit.evidence.citation_open_ref
        for hit in answer.hits
        if hit.evidence.citation_open_ref is not None
    }
    if set(opened) != expected_refs:
        raise PublicDocumentInvalid
    for hit in answer.hits:
        locator = hit.evidence.citation_open_ref
        if locator is None:
            raise PublicDocumentInvalid
        resolved = hit_test_view(opened[locator], query=answer.query)
        if len(resolved.hits) != 1:
            raise PublicDocumentInvalid
        evidence = resolved.hits[0].evidence
        if (
            evidence.source_ref != hit.evidence.source_ref
            or evidence.resource_ref != hit.evidence.resource_ref
            or evidence.revision_ref != hit.evidence.revision_ref
            or evidence.fragment_ref != hit.evidence.fragment_ref
            or evidence.policy_epoch != hit.evidence.policy_epoch
        ):
            raise PublicDocumentInvalid
    return answer


def _profile_identity(document: object) -> ProfileIdentityView:
    if type(document) is not dict:
        raise PublicDocumentInvalid
    profile_ref = _required_text(document, "profileRef")
    digest = _required_text(document, "digest")
    if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
        raise PublicDocumentInvalid
    return ProfileIdentityView(profile_ref=profile_ref, digest=digest)


def profiles_view(document: dict[str, object]) -> ProfilesView:
    generation = document.get("releaseGeneration")
    if type(generation) is not int or generation < 1:
        raise PublicDocumentInvalid
    return ProfilesView(
        release_generation=generation,
        release_manifest_ref=_required_text(document, "releaseManifestRef"),
        content_profile=_profile_identity(document.get("contentProfile")),
        index_profile=_profile_identity(document.get("indexProfile")),
        runtime_profile=_profile_identity(document.get("runtimeProfile")),
    )


def overview_view(document: dict[str, object]) -> OverviewView:
    generation = document.get("releaseGeneration")
    raw_sources = document.get("sources")
    if type(generation) is not int or generation < 1 or type(raw_sources) is not list:
        raise PublicDocumentInvalid
    sources: list[SourceHealthView] = []
    for item in raw_sources:
        if type(item) is not dict:
            raise PublicDocumentInvalid
        count = item.get("activeResourceCount")
        age = item.get("lastSuccessfulAcquisitionAgeSeconds")
        categories = item.get("refusalCategories")
        status = _required_text(item, "status")
        if (
            type(count) is not int
            or count < 0
            or (age is not None and (type(age) is not int or age < 0))
            or type(categories) is not list
            or any(type(category) is not str or not category for category in categories)
            or status not in {"ready", "refused", "waiting_first_success"}
        ):
            raise PublicDocumentInvalid
        sources.append(
            SourceHealthView(
                source_ref=_required_text(item, "sourceRef"),
                display_name=_required_text(item, "displayName"),
                status=status,
                active_resource_count=count,
                last_successful_acquisition_age_seconds=age,
                refusal_categories=tuple(categories),
            )
        )
    return OverviewView(
        release_generation=generation,
        release_manifest_ref=_required_text(document, "releaseManifestRef"),
        sources=tuple(sources),
    )


def import_preview_view(
    document: dict[str, object],
) -> ImportPreviewView | ImportScanHandoffView:
    kind = document.get("kind")
    if kind == "scan_handoff":
        if document.get("reason") != "rich_markdown_requires_leased_worker":
            raise PublicDocumentInvalid
        return ImportScanHandoffView(
            source_ref=_required_text(document, "sourceRef"),
            path=_required_text(document, "path"),
            scan_command=_required_text(document, "scanCommand"),
            worker_command=_required_text(document, "workerCommand"),
        )
    if kind != "preview_ready":
        raise PublicDocumentInvalid
    raw_fragments = document.get("fragments")
    if type(raw_fragments) is not list or not raw_fragments:
        raise PublicDocumentInvalid
    fragments: list[ImportFragmentView] = []
    for item in raw_fragments:
        if type(item) is not dict:
            raise PublicDocumentInvalid
        fragments.append(
            ImportFragmentView(
                fragment_ref=_required_text(item, "fragmentRef"),
                text=_required_text(item, "text"),
            )
        )
    compilation_digest = _required_text(document, "compilationDigest")
    fragment_digest = _required_text(document, "fragmentDigest")
    if any(
        len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        for digest in (compilation_digest, fragment_digest)
    ):
        raise PublicDocumentInvalid
    return ImportPreviewView(
        source_ref=_required_text(document, "sourceRef"),
        path=_required_text(document, "path"),
        compilation_digest=compilation_digest,
        fragment_digest=fragment_digest,
        preview_token=_required_text(document, "previewToken"),
        fragments=tuple(fragments),
    )


def _string_tuple(document: dict[str, object], name: str) -> tuple[str, ...]:
    value = document.get(name)
    if type(value) is not list or any(
        type(item) is not str or not item or item.isspace() for item in value
    ):
        raise PublicDocumentInvalid
    return tuple(value)


def article_view(document: dict[str, object]) -> ArticleView:
    policy_version = document.get("policyVersion")
    policy_epoch = document.get("policyEpoch")
    published = document.get("published")
    local_kind = document.get("localPolicyKind")
    effective_kind = document.get("effectivePolicyKind")
    rung = _required_text(document, "resolutionRung")
    kinds = {"private", "organization", "groups"}
    if (
        type(policy_version) is not int
        or policy_version < 1
        or type(policy_epoch) is not int
        or policy_epoch < 1
        or type(published) is not bool
        or (local_kind is not None and local_kind not in kinds)
        or (effective_kind is not None and effective_kind not in kinds)
        or rung
        not in {"explicit_article", "source_default", "tenant_default", "isolation"}
        or published is not (effective_kind is not None)
    ):
        raise PublicDocumentInvalid
    return ArticleView(
        resource_ref=_required_text(document, "resourceRef"),
        source_ref=_required_text(document, "sourceRef"),
        policy_version=policy_version,
        policy_epoch=policy_epoch,
        local_policy_kind=cast(str | None, local_kind),
        local_group_refs=_string_tuple(document, "localGroupRefs"),
        effective_policy_kind=cast(str | None, effective_kind),
        effective_group_refs=_string_tuple(document, "effectiveGroupRefs"),
        published=published,
        resolution_rung=rung,
    )
