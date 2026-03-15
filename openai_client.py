"""
Shared OpenAI client factory.

We keep a single lazily-initialized client instance so that modules don't
duplicate connection pools and configuration.
"""

from __future__ import annotations

import threading
from typing import Optional

try:
    from openai import OpenAI  # type: ignore
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore

_client: Optional[object] = None
_client_lock = threading.Lock()


def get_openai_client():
    global _client
    if _client is None:
        with _client_lock:
            # Double-check — thread אחר יכול היה ליצור בינתיים
            if _client is None:
                if OpenAI is None:
                    raise RuntimeError("OpenAI client is unavailable (openai package not installed).")
                _client = OpenAI()
    return _client
