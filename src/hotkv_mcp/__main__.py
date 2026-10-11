# Copyright (c) 2026 HotKV Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE in the project root.

"""Command line entry point: `hotkv-mcp` or `python -m hotkv_mcp`."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from typing import List, Optional

from ._version import __version__
from .config import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hotkv-mcp",
        description="MCP server that lets AI agents use a HotKV server.",
    )
    parser.add_argument("--url", help="HotKV URL (default: $HOTKV_URL or hotkv://127.0.0.1:6379)")
    parser.add_argument("--read-only", action="store_true",
                        help="do not offer any tool that writes or deletes ($HOTKV_MCP_READ_ONLY)")
    parser.add_argument("--namespace",
                        help="confine the agent to keys, sessions and names under this prefix ($HOTKV_MCP_NAMESPACE)")
    parser.add_argument("--max-value-bytes", type=int, help="cut longer values (default 65536)")
    parser.add_argument("--max-items", type=int, help="most keys, rows or hits per call (default 200)")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio",
                        help="stdio for local clients (default); streamable-http to serve over the network")
    parser.add_argument("--host", default="127.0.0.1", help="bind address for streamable-http")
    parser.add_argument("--port", type=int, default=8000, help="port for streamable-http")
    parser.add_argument("--version", action="version", version=f"hotkv-mcp {__version__}")
    return parser


def settings_from_args(args: argparse.Namespace, env: Optional[dict] = None) -> Settings:
    settings = Settings.from_env(env)
    changes = {}
    if args.url:
        changes["url"] = args.url
    if args.read_only:
        changes["read_only"] = True
    if args.namespace is not None:
        changes["namespace"] = args.namespace
    if args.max_value_bytes is not None:
        changes["max_value_bytes"] = args.max_value_bytes
    if args.max_items is not None:
        changes["max_items"] = args.max_items
    return replace(settings, **changes)


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    settings = settings_from_args(args)

    from .server import create_server

    server = create_server(settings)
    if args.transport == "streamable-http":
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            print("hotkv-mcp: serving beyond localhost with no authentication; put it behind "
                  "a reverse proxy that authenticates callers.", file=sys.stderr)
        legacy = getattr(server, "settings", None)
        if legacy is not None and hasattr(legacy, "host"):  # mcp 1.x keeps these on the server
            legacy.host = args.host
            legacy.port = args.port
            server.run(transport="streamable-http")
        else:
            server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
