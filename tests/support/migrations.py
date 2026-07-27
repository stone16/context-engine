"""Shared migration assertions for tests that require the current schema head."""

from engine.persistence.migrations import head_revision

HEAD_REVISION = head_revision()
