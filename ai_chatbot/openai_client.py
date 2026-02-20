"""
Shared OpenAI client factory.

We keep a single lazily-initialized client instance so that modules don't
duplicate connection pools and configuration.
"""

from __future__ import annotations

from typing import Optional

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

_client: Optional[object] = None


def get_openai_client():
    global _client
    if _client is None:
        if OpenAI is None:
            raise RuntimeError("OpenAI client is unavailable (openai package not installed).")
        _client = OpenAI()
    return _client

