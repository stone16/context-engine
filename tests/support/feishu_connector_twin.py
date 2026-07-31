"""Deterministic fixture-driven twin for the clean-room Feishu connector."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from adapters.connectors.feishu import (
    FeishuAclFailure,
    FeishuAclResponse,
    FeishuChangePage,
    FeishuGroupSnapshot,
    FeishuIdentityMapping,
    FeishuSourceError,
)


class SyntheticFeishuTwin:
    """In-memory Feishu behavior surface with scripted pages and failures."""

    def __init__(
        self,
        *,
        pages: Mapping[str | None, FeishuChangePage | Exception],
        acl_responses: Mapping[
            str,
            FeishuAclResponse | FeishuAclFailure | Exception,
        ],
        identity_mappings: Mapping[str, FeishuIdentityMapping | Exception],
        group_snapshot: FeishuGroupSnapshot | Exception,
        policy_epoch: int = 1,
        acl_sequences: Mapping[
            str,
            tuple[FeishuAclResponse | FeishuAclFailure | Exception, ...],
        ]
        | None = None,
        identity_sequences: Mapping[
            str,
            tuple[FeishuIdentityMapping | Exception, ...],
        ]
        | None = None,
        group_snapshot_sequence: tuple[FeishuGroupSnapshot | Exception, ...]
        | None = None,
    ) -> None:
        if type(policy_epoch) is not int or policy_epoch < 1:
            raise ValueError("synthetic Feishu Policy Epoch must be positive")
        self.policy_epoch = policy_epoch
        self._pages = dict(pages)
        self._acl_responses = dict(acl_responses)
        self._acl_sequences = dict(acl_sequences or {})
        self._identity_mappings = dict(identity_mappings)
        self._identity_sequences = dict(identity_sequences or {})
        self._group_snapshot = group_snapshot
        self._group_snapshot_sequence = group_snapshot_sequence
        self.network_accesses = 0
        self.credential_accesses = 0
        self.page_calls: list[str | None] = []
        self.acl_calls: list[str] = []
        self.identity_calls: list[str] = []
        self.group_snapshot_calls = 0

    def read_changes(self, page_token: str | None) -> FeishuChangePage:
        self.page_calls.append(page_token)
        result = self._pages.get(page_token)
        if isinstance(result, Exception):
            raise result
        if type(result) is not FeishuChangePage:
            raise FeishuSourceError("synthetic Feishu page is unavailable")
        return result

    def observe_acl(
        self,
        document_ref: str,
    ) -> FeishuAclResponse | FeishuAclFailure:
        call_index = self.acl_calls.count(document_ref)
        self.acl_calls.append(document_ref)
        sequence = self._acl_sequences.get(document_ref)
        result = (
            sequence[call_index]
            if sequence is not None and call_index < len(sequence)
            else self._acl_responses.get(document_ref)
        )
        if isinstance(result, Exception):
            raise result
        if type(result) not in {FeishuAclResponse, FeishuAclFailure}:
            raise FeishuSourceError("synthetic Feishu ACL is unavailable")
        return cast(FeishuAclResponse | FeishuAclFailure, result)

    def map_identity(self, external_ref: str) -> FeishuIdentityMapping:
        call_index = self.identity_calls.count(external_ref)
        self.identity_calls.append(external_ref)
        sequence = self._identity_sequences.get(external_ref)
        result = (
            sequence[call_index]
            if sequence is not None and call_index < len(sequence)
            else self._identity_mappings.get(
                external_ref,
                FeishuIdentityMapping(external_ref=external_ref),
            )
        )
        if isinstance(result, Exception):
            raise result
        return result

    def group_snapshot(self) -> FeishuGroupSnapshot:
        call_index = self.group_snapshot_calls
        self.group_snapshot_calls += 1
        sequence = self._group_snapshot_sequence
        result = (
            sequence[call_index]
            if sequence is not None and call_index < len(sequence)
            else self._group_snapshot
        )
        if isinstance(result, Exception):
            raise result
        return result

    def replace_acl(
        self,
        document_ref: str,
        response: FeishuAclResponse | FeishuAclFailure,
    ) -> None:
        self._acl_responses[document_ref] = response


SYNTHETIC_OBSERVED_AT = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)

__all__ = ["SYNTHETIC_OBSERVED_AT", "SyntheticFeishuTwin"]
