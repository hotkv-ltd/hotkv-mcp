# Copyright (c) 2026 HotKV Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE in the project root.

"""MCP server for HotKV."""

from ._version import __version__
from .config import Settings
from .server import create_server

__all__ = ["Settings", "create_server", "__version__"]
