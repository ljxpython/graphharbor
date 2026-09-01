"""Configuration assembly for a claimed Run."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

THREAD_METADATA_KEY = "__graphharbor_thread_metadata"


def attach_thread_metadata(metadata: dict[str, Any], thread_metadata: Mapping[str, Any]) -> None:
    """Expose immutable Thread facts under a server-owned metadata key."""

    metadata[THREAD_METADATA_KEY] = dict(thread_metadata)


__all__ = ["THREAD_METADATA_KEY", "attach_thread_metadata"]
