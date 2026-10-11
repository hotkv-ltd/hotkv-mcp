# Copyright (c) 2026 HotKV Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE in the project root.

"""Runs the tools through the MCP server object against a live, licensed HotKV server."""

import json

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(server, tool_name, /, **args):
    result = await server.call_tool(tool_name, args)
    # mcp 1.x returns (content, structured); mcp 2.x a CallToolResult
    content = result[0] if isinstance(result, tuple) else getattr(result, "content", result)
    structured = result[1] if isinstance(result, tuple) else getattr(result, "structured_content", None)
    if structured is not None:
        return structured.get("result", structured) if isinstance(structured, dict) else structured
    text = content[0].text
    if text[:1] in ("{", "[") or text == "null":
        return json.loads(text)
    return text


async def names(server):
    return {t.name for t in await server.list_tools()}


async def test_read_only_hides_write_tools(make_server):
    full = await names(make_server())
    ro = await names(make_server(read_only=True))
    assert {"set", "delete", "hash_set", "list_push", "memory_append_message", "rag_ingest"} <= full
    assert not ({"set", "delete", "hash_set", "list_push", "memory_append_message", "rag_ingest",
                 "prompt_save", "memory_log_event", "memory_set_summary", "rag_create"} & ro)
    assert {"get", "scan", "rag_search", "memory_get_messages"} <= ro


async def test_key_roundtrip(make_server, prefix):
    s = make_server()
    assert await call(s, "get", key=f"{prefix}:a") is None
    assert await call(s, "set", key=f"{prefix}:a", value="hello", ttl_seconds=60) is True
    assert await call(s, "get", key=f"{prefix}:a") == "hello"
    info = await call(s, "key_info", key=f"{prefix}:a")
    assert info["exists"] and info["type"] == "string" and 0 < info["ttl_seconds"] <= 60
    assert await call(s, "set", key=f"{prefix}:a", value="x", only_if_missing=True) is False
    assert await call(s, "delete", key=f"{prefix}:a") is True
    assert (await call(s, "key_info", key=f"{prefix}:a")) == {"exists": False}


async def test_big_values_are_cut(make_server, prefix):
    s = make_server(max_value_bytes=16)
    await call(s, "set", key=f"{prefix}:big", value="z" * 100)
    out = await call(s, "get", key=f"{prefix}:big")
    assert out["truncated"] is True and out["total_bytes"] == 100 and len(out["value"]) == 16
    await call(s, "delete", key=f"{prefix}:big")


async def test_hash_and_list(make_server, prefix):
    s = make_server()
    assert await call(s, "hash_set", key=f"{prefix}:h", fields={"a": "1", "b": "2"}) == 2
    assert await call(s, "hash_get", key=f"{prefix}:h", field="a") == "1"
    assert await call(s, "hash_get", key=f"{prefix}:h") == {"a": "1", "b": "2"}
    assert await call(s, "list_push", key=f"{prefix}:l", values=["x", "y", "z"]) == 3
    assert await call(s, "list_range", key=f"{prefix}:l") == ["x", "y", "z"]
    await call(s, "delete", key=f"{prefix}:h")
    await call(s, "delete", key=f"{prefix}:l")


async def test_namespace_confines_the_agent(make_server, live, prefix):
    live.set(f"{prefix}-outside", "secret")
    s = make_server(namespace=f"{prefix}-ns")
    assert await call(s, "get", key=f"{prefix}-outside") is None
    await call(s, "set", key="mine", value="v")
    assert live.get(f"{prefix}-ns:mine") == b"v"
    page = await call(s, "scan", pattern="*")
    assert page["keys"] == ["mine"]
    await call(s, "delete", key="mine")
    live.delete(f"{prefix}-outside")


async def test_scan_respects_the_limit(make_server, live, prefix):
    for i in range(20):
        live.set(f"{prefix}:s:{i}", "v")
    s = make_server(max_items=5)
    seen, cursor = [], 0
    for _ in range(50):
        page = await call(s, "scan", pattern=f"{prefix}:s:*", cursor=cursor, limit=1000)
        assert len(page["keys"]) <= 5
        seen += page["keys"]
        cursor = page["next_cursor"]
        if cursor == 0:
            break
    assert len(set(seen)) == 20
    for i in range(20):
        live.delete(f"{prefix}:s:{i}")


async def test_conversation_memory(make_server, prefix, licensed):
    s = make_server()
    sess = f"{prefix}-chat"
    assert await call(s, "memory_append_message", session=sess, role="user", content="hi") is True
    await call(s, "memory_append_message", session=sess, role="assistant", content="hello")
    turns = await call(s, "memory_get_messages", session=sess)
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert [t["content"] for t in await call(s, "memory_get_messages", session=sess, last=1)] == ["hello"]
    await call(s, "memory_set_note", session=sess, key="plan", value="B")
    assert await call(s, "memory_get_note", session=sess, key="plan") == "B"
    assert "plan" in await call(s, "memory_list_notes", session=sess)
    await call(s, "memory_clear_messages", session=sess)
    assert await call(s, "memory_get_messages", session=sess) == []


async def test_events_and_summary(make_server, prefix, licensed):
    s = make_server()
    agent = f"{prefix}-agent"
    await call(s, "memory_log_event", agent=agent, event_type="decision", content="chose B")
    events = await call(s, "memory_query_events", agent=agent, event_type="decision")
    assert events and events[0]["content"] == "chose B"
    assert await call(s, "memory_get_summary", agent=agent) is None
    assert await call(s, "memory_set_summary", agent=agent, summary="one") == 1
    assert await call(s, "memory_set_summary", agent=agent, summary=" two", append=True) == 2
    got = await call(s, "memory_get_summary", agent=agent)
    assert got["summary"] == "one\n two" and got["version"] == 2


async def test_prompt_registry(make_server, prefix, licensed):
    s = make_server()
    name = f"{prefix}-greet"
    assert await call(s, "prompt_save", name=name, content="Hello {who}") == 1
    assert await call(s, "prompt_save", name=name, content="Hi {who}", notes="shorter") == 2
    assert (await call(s, "prompt_get", name=name))["content"] == "Hi {who}"
    assert (await call(s, "prompt_get", name=name, version=1))["content"] == "Hello {who}"
    assert await call(s, "prompt_versions", name=name) == [1, 2]
    assert name in await call(s, "prompt_list")


async def test_rag_search(make_server, prefix, licensed):
    s = make_server()
    p = f"{prefix}-docs"
    assert await call(s, "rag_create", pipeline=p, chunk_size=200, chunk_overlap=20) is True
    await call(s, "rag_ingest", pipeline=p, doc_id="a", text="HotKV keeps agent memory and caches prompts.")
    await call(s, "rag_ingest", pipeline=p, doc_id="b", text="Bananas are yellow and grow in bunches.")
    hits = await call(s, "rag_search", pipeline=p, query="agent memory", k=2)
    assert hits and "agent memory" in json.dumps(hits[0])
    assert p in await call(s, "rag_list")


async def test_errors_are_readable(make_server, prefix):
    s = make_server()
    await call(s, "set", key=f"{prefix}:str", value="v")
    with pytest.raises(Exception) as err:
        await call(s, "hash_get", key=f"{prefix}:str")
    assert "wrong" in str(err.value).lower()
    await call(s, "delete", key=f"{prefix}:str")


async def test_server_info_reports_limits(make_server):
    info = await call(make_server(read_only=True, namespace="n1", max_items=7), "server_info")
    assert info["read_only"] is True and info["namespace"] == "n1" and info["max_items"] == 7
    assert info["keys"] is None  # the key count would reveal what is outside the namespace
    full = await call(make_server(), "server_info")
    assert isinstance(full["keys"], int)


async def test_key_info_for_keys_that_never_expire(make_server, prefix):
    s = make_server()
    await call(s, "set", key=f"{prefix}:forever", value="v")
    info = await call(s, "key_info", key=f"{prefix}:forever")
    assert info == {"exists": True, "type": "string", "ttl_seconds": None}
    await call(s, "delete", key=f"{prefix}:forever")


async def test_a_bad_ttl_is_a_readable_error(make_server, prefix):
    with pytest.raises(Exception) as err:
        await call(make_server(), "set", key=f"{prefix}:x", value="v", ttl_seconds=-5)
    assert "x" in str(err.value)  # an error result, not a crash
    assert await call(make_server(), "get", key=f"{prefix}:x") is None


async def test_scan_patterns_cannot_leave_the_namespace(make_server, live, prefix):
    live.set(f"{prefix}-other", "1")
    live.set(f"{prefix}-ns:inside", "1")
    s = make_server(namespace=f"{prefix}-ns")
    for pattern in ["*", "?*", "[a-z]*", "*other*", chr(92) + "*", "..*"]:
        page = await call(s, "scan", pattern=pattern)
        assert all(not k.startswith(prefix) for k in page["keys"]), (pattern, page)
    assert (await call(s, "scan", pattern="ins*"))["keys"] == ["inside"]
    live.delete(f"{prefix}-other", f"{prefix}-ns:inside")


async def test_list_and_hash_limits_apply(make_server, prefix):
    s = make_server(max_items=3)
    await call(s, "list_push", key=f"{prefix}:l", values=[str(i) for i in range(10)])
    assert await call(s, "list_range", key=f"{prefix}:l") == ["0", "1", "2"]
    await call(s, "delete", key=f"{prefix}:l")


async def test_values_with_unicode_and_newlines_roundtrip(make_server, prefix):
    s = make_server()
    value = "line one" + chr(10) + "line two " + chr(0xE9) + " " + chr(0x65E5) + chr(0x672C) + " " + chr(0x1F600)
    await call(s, "set", key=f"{prefix}:u", value=value)
    assert await call(s, "get", key=f"{prefix}:u") == value
    await call(s, "delete", key=f"{prefix}:u")


async def test_binary_values_come_back_as_base64(make_server, live, prefix):
    live.set(f"{prefix}:bin", bytes([255, 254, 0, 1]))
    out = await call(make_server(), "get", key=f"{prefix}:bin")
    assert out["base64"] == "//4AAQ==" and out["total_bytes"] == 4
    live.delete(f"{prefix}:bin")


async def test_namespaced_ai_names_are_hidden_and_isolated(make_server, live, prefix, licensed):
    a = make_server(namespace=f"{prefix}-a")
    b = make_server(namespace=f"{prefix}-b")
    await call(a, "prompt_save", name="greet", content="Hello")
    assert await call(b, "prompt_get", name="greet") is None
    assert await call(a, "prompt_list") == ["greet"]
    assert "greet" not in await call(b, "prompt_list")
    await call(a, "memory_append_message", session="s", role="user", content="hi")
    assert await call(b, "memory_get_messages", session="s") == []
    live.execute_command("PROMPT.DROP", f"{prefix}-a:greet")
    live.amem_conv_drop(f"{prefix}-a:s")
