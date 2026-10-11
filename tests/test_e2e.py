# Copyright (c) 2026 HotKV Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE in the project root.

"""The real `hotkv-mcp` process, spoken to the way Claude Desktop or Cursor would: over stdio and over HTTP."""

import os
import socket
import subprocess
import sys
import time

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import URL

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _text(result):
    return result.content[0].text if result.content else ""


def _is_error(result):
    return getattr(result, "is_error", None) if hasattr(result, "is_error") else getattr(result, "isError", False)


def _params(*extra):
    env = dict(os.environ, HOTKV_URL=URL)
    return StdioServerParameters(command=sys.executable, args=["-m", "hotkv_mcp", *extra], env=env)


async def test_stdio_roundtrip_and_errors(live, prefix):
    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert len(names) == 28 and {"get", "set", "scan", "rag_search"} <= names
            await session.call_tool("set", {"key": f"{prefix}:k", "value": "from-stdio"})
            got = await session.call_tool("get", {"key": f"{prefix}:k"})
            assert _text(got) == "from-stdio"
            bad = await session.call_tool("hash_get", {"key": f"{prefix}:k"})
            assert _is_error(bad) and "wrong kind of value" in _text(bad)
            await session.call_tool("delete", {"key": f"{prefix}:k"})


async def test_stdio_read_only_and_namespace_flags(live, prefix):
    live.set(f"{prefix}-outside", "secret")
    async with stdio_client(_params("--read-only", "--namespace", f"{prefix}-ns")) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert "set" not in names and "delete" not in names and "get" in names
            # a hidden tool cannot be called either
            result = await session.call_tool("set", {"key": "x", "value": "y"})
            assert _is_error(result)
            # the namespace hides what is outside it
            assert _text(await session.call_tool("get", {"key": f"{prefix}-outside"})) in ("", "null")
    live.delete(f"{prefix}-outside")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_http_transport(live, prefix):
    from mcp.client.streamable_http import streamable_http_client

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "hotkv_mcp", "--transport", "streamable-http", "--port", str(port)],
        env=dict(os.environ, HOTKV_URL=URL), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("the HTTP server did not start: " + proc.stderr.read().decode())
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                await session.call_tool("set", {"key": f"{prefix}:http", "value": "via-http"})
                assert _text(await session.call_tool("get", {"key": f"{prefix}:http"})) == "via-http"
                await session.call_tool("delete", {"key": f"{prefix}:http"})
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_serving_beyond_localhost_prints_a_warning(live):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "hotkv_mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", str(port)],
        env=dict(os.environ, HOTKV_URL=URL), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        time.sleep(1.5)
        proc.terminate()
        err = proc.communicate(timeout=10)[1].decode()
    finally:
        if proc.poll() is None:
            proc.kill()
    assert "no authentication" in err
