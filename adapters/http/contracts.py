"""Closed untrusted HTTP request models for the first acquire slice."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContextNeedWire(BaseModel):
    """Untrusted context need; it carries no identity or authority."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, pattern=r".*\S.*")


class AcquireWire(BaseModel):
    """Only the narrow acquire shape activated by the trust-boundary issue."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["acquire"]
    need: ContextNeedWire


class AuthenticationFailureWire(BaseModel):
    """Closed public response for every transport authentication rejection."""

    model_config = ConfigDict(extra="forbid")

    code: Literal["authentication_failed"]


class InvalidRequestWire(BaseModel):
    """Closed public response for request syntax or schema rejection."""

    model_config = ConfigDict(extra="forbid")

    code: Literal["invalid_request"]
