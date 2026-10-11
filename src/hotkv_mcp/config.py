# Copyright (c) 2026 HotKV Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE in the project root.

"""Settings of the MCP server, read from the command line and the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

DEFAULT_URL = "hotkv://127.0.0.1:6379"

__all__ = ["Settings", "DEFAULT_URL"]


def _truthy(value: Optional[str]) -> bool:
    return value is not None and value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """What the server may do and how much it may return.

    `url`            where the HotKV server is (`hotkv://`, `hotkvs://` for TLS,
                     `redis://` and `rediss://` also work).
    `read_only`      write tools are not registered at all, so an agent cannot see
                     or call them.
    `namespace`      every key, session, agent, prompt and pipeline name the agent
                     uses is stored under `<namespace>:`; the agent can neither
                     read nor list anything outside it.
    `max_value_bytes` a larger value is cut and flagged `truncated`.
    `max_items`      cap on the number of keys, rows or hits one call returns.
    """

    url: str = DEFAULT_URL
    read_only: bool = False
    namespace: str = ""
    max_value_bytes: int = 65536
    max_items: int = 200

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        return cls(
            url=env.get("HOTKV_URL", DEFAULT_URL),
            read_only=_truthy(env.get("HOTKV_MCP_READ_ONLY")),
            namespace=env.get("HOTKV_MCP_NAMESPACE", ""),
            max_value_bytes=int(env.get("HOTKV_MCP_MAX_VALUE_BYTES", "65536")),
            max_items=int(env.get("HOTKV_MCP_MAX_ITEMS", "200")),
        )

    def __post_init__(self) -> None:
        if self.max_value_bytes < 1:
            raise ValueError("max_value_bytes must be at least 1")
        if self.max_items < 1:
            raise ValueError("max_items must be at least 1")
