# Copyright (c) 2026 HotKV Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE in the project root.

"""The MCP server: a fixed set of tools over a HotKV connection.

There is deliberately no tool that runs an arbitrary command. An agent gets
keys, hashes, lists, agent memory, the prompt registry and RAG search, each
with the limits in `Settings`, and nothing that can flush a database, change
the server's configuration or read another tenant's namespace.
"""

import base64
import functools
import inspect
from typing import Any, Callable, Dict, List, Optional

import anyio
import hotkv
from hotkv import exceptions as hx

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError

from mcp.types import ToolAnnotations

from ._version import __version__
from .config import Settings

__all__ = ["create_server", "Namespace", "to_plain"]

INSTRUCTIONS = (
    "Tools for a HotKV in-memory data store. Use key tools (get, set, hash_get, "
    "list_range, scan) for plain data; memory_* for an agent's conversation, "
    "working notes, event log and rolling summary; prompt_* for versioned prompt "
    "templates; rag_* for document search. Values larger than the server's limit "
    "come back cut, with truncated=true."
)


class Namespace:
    """Maps the names an agent uses onto the names stored on the server.

    With an empty namespace the names are used as they are. With one, every
    name is stored as `<namespace>:<name>`, and names read back from the server
    are shown without it, so the agent never sees or reaches anything else.
    """

    def __init__(self, prefix: str) -> None:
        self.prefix = f"{prefix}:" if prefix else ""

    def to_server(self, name: str) -> str:
        if not isinstance(name, str) or not name:
            raise ValueError("a name must be a non-empty string")
        return self.prefix + name

    def to_agent(self, name: str) -> Optional[str]:
        """The agent's name for a server name, or None if it is outside the namespace."""
        if not self.prefix:
            return name
        return name[len(self.prefix):] if name.startswith(self.prefix) else None

    @property
    def scan_pattern_prefix(self) -> str:
        return _glob_escape(self.prefix)


def _glob_escape(text: str) -> str:
    return "".join("\\" + c if c in "*?[]\\" else c for c in text)


def to_plain(value: Any, max_bytes: int) -> Any:
    """JSON-safe copy of a reply: bytes become text, long text is cut.

    Bytes that are not UTF-8 become `{"base64": ...}`. A cut value becomes
    `{"value": ..., "truncated": true, "total_bytes": n}` so the agent can tell.
    """
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        total = len(raw)
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"base64": base64.b64encode(raw[:max_bytes]).decode("ascii"),
                    "truncated": total > max_bytes, "total_bytes": total}
        if total <= max_bytes:
            return raw.decode("utf-8")
        # cut on a character boundary: drop a character the cut split
        return {"value": raw[:max_bytes].decode("utf-8", errors="ignore"),
                "truncated": True, "total_bytes": total}
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) > max_bytes:
            return {"value": encoded[:max_bytes].decode("utf-8", errors="ignore"),
                    "truncated": True, "total_bytes": len(encoded)}
        return value
    if isinstance(value, dict):
        return {(k.decode("utf-8", errors="replace") if isinstance(k, (bytes, bytearray)) else k): to_plain(v, max_bytes)
                for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_plain(v, max_bytes) for v in value]
    return value


def _explain(exc: Exception) -> Exception:
    """A short error an agent can act on, without the client's internals."""
    if isinstance(exc, hx.NotLicensedError):
        return ToolError("This HotKV server is not licensed for that feature.")
    if isinstance(exc, (hx.ConnectionError, hx.TimeoutError)):
        return ToolError(f"Cannot reach the HotKV server: {exc}")
    if isinstance(exc, hx.ResponseError):
        return ToolError(f"HotKV refused the request: {exc}")
    if isinstance(exc, ValueError):
        return ToolError(str(exc))
    return exc


def create_server(settings: Optional[Settings] = None, client: Optional[Any] = None) -> Any:
    """Build the MCP server. `client` is for tests; otherwise one is made from `settings.url`."""
    settings = settings or Settings.from_env()
    ns = Namespace(settings.namespace)
    hk = client if client is not None else hotkv.from_url(settings.url)
    try:
        mcp = _Server("hotkv", instructions=INSTRUCTIONS, version=__version__)
    except TypeError:  # mcp 1.x has no version argument
        mcp = _Server("hotkv", instructions=INSTRUCTIONS)
    cap = settings.max_value_bytes
    limit = settings.max_items

    def tool(*, write: bool = False, destructive: bool = False, idempotent: bool = False):
        """Register `fn` as a tool, run in a worker thread, unless it writes and the server is read-only."""

        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            if write and settings.read_only:
                return fn

            @functools.wraps(fn)
            async def run(*args: Any, **kwargs: Any) -> Any:
                def call() -> Any:
                    try:
                        return to_plain(fn(*args, **kwargs), cap)
                    except (hx.HotKVError, ValueError) as exc:
                        raise _explain(exc) from None

                return await anyio.to_thread.run_sync(call)

            run.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
            mcp.tool(
                name=fn.__name__,
                annotations=ToolAnnotations(
                    readOnlyHint=not write,
                    destructiveHint=destructive if write else False,
                    idempotentHint=idempotent or not write,
                    openWorldHint=False,
                ),
            )(run)
            return fn

        return decorate

    def clip(n: Optional[int]) -> int:
        return limit if n is None else max(1, min(int(n), limit))

    # ---- server ---------------------------------------------------------------

    @tool()
    def server_info() -> Dict[str, Any]:
        """HotKV server version, memory use and key count, plus how this MCP server is limited."""
        info = hk.info()
        keep = {k: info.get(k) for k in ("hotkv_version", "redis_version", "uptime_in_seconds",
                                         "connected_clients", "used_memory_human", "role")
                if isinstance(info, dict) and k in info}
        return {"info": keep, "keys": hk.dbsize() if not ns.prefix else None,
                "read_only": settings.read_only, "namespace": settings.namespace or None,
                "max_value_bytes": cap, "max_items": limit}

    # ---- keys -----------------------------------------------------------------

    @tool()
    def get(key: str) -> Optional[Any]:
        """Read a string key. Returns null when the key does not exist."""
        return hk.get(ns.to_server(key))

    @tool(write=True, idempotent=True)
    def set(key: str, value: str, ttl_seconds: Optional[int] = None, only_if_missing: bool = False) -> bool:
        """Write a string key, optionally expiring after ttl_seconds. With only_if_missing, an existing key is left alone. Returns whether the value was written."""
        result = hk.set(ns.to_server(key), value, ex=ttl_seconds, nx=only_if_missing)
        return bool(result)

    @tool(write=True, destructive=True, idempotent=True)
    def delete(key: str) -> bool:
        """Delete one key. Returns whether it existed."""
        return hk.delete(ns.to_server(key)) > 0

    @tool()
    def key_info(key: str) -> Dict[str, Any]:
        """Whether a key exists, its type and its time to live in seconds (null when it never expires)."""
        server_key = ns.to_server(key)
        if not hk.exists(server_key):
            return {"exists": False}
        ttl = hk.ttl(server_key)
        return {"exists": True, "type": to_plain(hk.type(server_key), cap),
                "ttl_seconds": None if ttl is None or ttl < 0 else ttl}

    @tool()
    def scan(pattern: str = "*", limit: Optional[int] = None, cursor: int = 0) -> Dict[str, Any]:
        """List key names matching a glob pattern, one page per call. Pass the returned next_cursor to continue; 0 means finished."""
        n = clip(limit)
        match = ns.scan_pattern_prefix + pattern
        next_cursor, keys = hk.scan(cursor, match=match, count=n)
        names = [ns.to_agent(to_plain(k, cap)) for k in keys]
        names = [k for k in names if k is not None][:n]
        return {"keys": names, "next_cursor": next_cursor}

    # ---- hashes ---------------------------------------------------------------

    @tool()
    def hash_get(key: str, field: Optional[str] = None) -> Any:
        """Read one field of a hash, or every field when field is omitted."""
        server_key = ns.to_server(key)
        if field is None:
            return hk.hgetall(server_key)
        return hk.hget(server_key, field)

    @tool(write=True, idempotent=True)
    def hash_set(key: str, fields: Dict[str, str]) -> int:
        """Set one or more fields of a hash. Returns how many fields are new."""
        if not fields:
            raise ValueError("fields must not be empty")
        return hk.hset(ns.to_server(key), fields)

    # ---- lists ----------------------------------------------------------------

    @tool()
    def list_range(key: str, start: int = 0, stop: int = -1) -> List[Any]:
        """Read items of a list by position (0 is the first, -1 the last), at most the server's item limit."""
        items = hk.lrange(ns.to_server(key), start, stop)
        return items[:limit]

    @tool(write=True)
    def list_push(key: str, values: List[str], at_head: bool = False) -> int:
        """Add items to the end of a list, or the head with at_head. Returns the new length."""
        if not values:
            raise ValueError("values must not be empty")
        push = hk.lpush if at_head else hk.rpush
        return push(ns.to_server(key), *values)

    # ---- agent memory ---------------------------------------------------------

    @tool(write=True)
    def memory_append_message(session: str, role: str, content: str) -> bool:
        """Add a turn (role is system, user, assistant or tool) to a conversation session, creating the session on first use."""
        server_session = ns.to_server(session)
        try:
            return hk.amem_conv_append(server_session, role, content)
        except hx.ResponseError as exc:
            if "session not found" not in str(exc).lower():
                raise
            hk.amem_conv_create(server_session)
            return hk.amem_conv_append(server_session, role, content)

    @tool()
    def memory_get_messages(session: str, last: Optional[int] = None) -> List[Any]:
        """Read a conversation session, oldest first; with last, only that many of the newest turns."""
        server_session = ns.to_server(session)
        turns = hk.amem_conv_get(server_session) if last is None else hk.amem_conv_window(server_session, clip(last))
        return turns[-limit:]

    @tool(write=True, destructive=True)
    def memory_clear_messages(session: str) -> bool:
        """Remove every turn from a conversation session, keeping the session."""
        return hk.amem_conv_clear(ns.to_server(session))

    @tool(write=True, idempotent=True)
    def memory_set_note(session: str, key: str, value: str) -> bool:
        """Keep a working note (scratchpad value) for a session, creating the session on first use."""
        server_session = ns.to_server(session)
        try:
            return hk.amem_work_set(server_session, key, value)
        except hx.ResponseError as exc:
            if "session not found" not in str(exc).lower():
                raise
            hk.amem_conv_create(server_session)
            return hk.amem_work_set(server_session, key, value)

    @tool()
    def memory_get_note(session: str, key: str) -> Optional[Any]:
        """Read one working note of a session. Returns null when it is not set."""
        return hk.amem_work_get(ns.to_server(session), key)

    @tool()
    def memory_list_notes(session: str) -> List[Any]:
        """List the names of a session's working notes."""
        return hk.amem_work_list(ns.to_server(session))[:limit]

    @tool(write=True)
    def memory_log_event(agent: str, event_type: str, content: str, metadata: Optional[str] = None) -> bool:
        """Append a timestamped event (a decision, an observation, an error) to an agent's event log."""
        return hk.amem_epis_log(ns.to_server(agent), event_type, content, metadata=metadata)

    @tool()
    def memory_query_events(agent: str, event_type: Optional[str] = None, limit: Optional[int] = None) -> List[Any]:
        """Read an agent's event log, optionally only one event type."""
        return hk.amem_epis_query(ns.to_server(agent), type=event_type, limit=clip(limit))

    @tool(write=True)
    def memory_set_summary(agent: str, summary: str, append: bool = False) -> int:
        """Replace an agent's rolling summary, or add to it with append. Returns the new version number."""
        if append:
            return hk.amem_summary_append(ns.to_server(agent), summary)
        return hk.amem_summary_set(ns.to_server(agent), summary)

    @tool()
    def memory_get_summary(agent: str) -> Optional[Dict[str, Any]]:
        """Read an agent's rolling summary with its version and update time, or null."""
        row = hk.amem_summary_get(ns.to_server(agent))
        if row is None:
            return None
        text, version, updated = row
        return {"summary": text, "version": version, "updated_unix": updated}

    # ---- prompt registry --------------------------------------------------------

    @tool(write=True)
    def prompt_save(name: str, content: str, notes: Optional[str] = None) -> int:
        """Store a new version of a prompt template. Returns the version number."""
        return hk.prompt_put(ns.to_server(name), content, meta=notes)

    @tool()
    def prompt_get(name: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Read a prompt template: the latest version, or a specific one."""
        row = hk.prompt_get(ns.to_server(name), version)
        if row is None:
            return None
        found, content, meta, created = row
        return {"version": found, "content": content, "notes": meta or None, "created_unix": created}

    @tool()
    def prompt_versions(name: str) -> List[int]:
        """List the stored version numbers of a prompt template."""
        return hk.prompt_versions(ns.to_server(name))

    @tool()
    def prompt_list() -> List[str]:
        """List prompt template names."""
        names = [ns.to_agent(to_plain(n, cap)) for n in hk.prompt_list()]
        return [n for n in names if n is not None][:limit]

    # ---- RAG --------------------------------------------------------------------

    @tool(write=True)
    def rag_create(pipeline: str, chunk_size: int = 1000, chunk_overlap: int = 100) -> bool:
        """Create a document search pipeline. Documents are split into chunks of chunk_size characters (overlapping by chunk_overlap) and found by keyword relevance."""
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return hk.rag_create(ns.to_server(pipeline), 1, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    @tool(write=True, idempotent=True)
    def rag_ingest(pipeline: str, doc_id: str, text: str, metadata: Optional[Dict[str, str]] = None) -> int:
        """Add a document to a search pipeline, replacing a document with the same id. Returns the number of chunks."""
        return hk.rag_ingest(ns.to_server(pipeline), doc_id, text, meta=metadata)

    @tool()
    def rag_search(pipeline: str, query: str, k: int = 5, filter: Optional[Dict[str, str]] = None) -> List[Any]:
        """Find the document chunks that best match a text query, most relevant first. filter keeps only documents whose metadata matches."""
        return hk.rag_search(ns.to_server(pipeline), query, k=clip(k), alpha=0.0, filter=filter or None)

    @tool()
    def rag_list() -> List[str]:
        """List document search pipelines."""
        names = [ns.to_agent(to_plain(n, cap)) for n in hk.rag_list()]
        return [n for n in names if n is not None][:limit]

    mcp.hotkv_client = hk  # type: ignore[attr-defined]
    return mcp
