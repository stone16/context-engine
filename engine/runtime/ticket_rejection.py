"""Shared non-enumerating rejection for distinct bounded ticket protocols."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class TicketRejectionCategory(StrEnum):
    """The sole caller-safe category for bounded ticket failures."""

    CAPABILITY_NOT_AVAILABLE = "capability_not_available"


@dataclass(frozen=True, slots=True)
class TicketRejectionAuditReceipt:
    """Restricted rejection metadata containing no target or claim detail."""

    category: TicketRejectionCategory = (
        TicketRejectionCategory.CAPABILITY_NOT_AVAILABLE
    )
    denied_detail_count: Literal[0] = 0

    def __post_init__(self) -> None:
        if self.category is not TicketRejectionCategory.CAPABILITY_NOT_AVAILABLE:
            raise ValueError("ticket rejection category must remain closed")
        if self.denied_detail_count != 0:
            raise ValueError("ticket rejection detail count must remain zero")


class TicketNotAvailable(Exception):
    """The sole caller-safe failure for either bounded ticket protocol."""

    def __init__(self) -> None:
        self.audit_receipt = TicketRejectionAuditReceipt()
        super().__init__("capability not available")
