# Copyright (c) 2026 HotKV Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE in the project root.

import pytest

from hotkv_mcp.__main__ import build_parser, settings_from_args
from hotkv_mcp.config import Settings
from hotkv_mcp.server import Namespace, to_plain


def test_to_plain_decodes_bytes_and_nested_values():
    assert to_plain(b"hello", 100) == "hello"
    assert to_plain({b"k": [b"a", (b"b", 3)]}, 100) == {"k": ["a", ["b", 3]]}


def test_to_plain_marks_cut_values():
    out = to_plain(b"x" * 50, 10)
    assert out == {"value": "x" * 10, "truncated": True, "total_bytes": 50}
    out = to_plain("y" * 50, 10)
    assert out["truncated"] is True and out["total_bytes"] == 50


def test_to_plain_does_not_split_a_character():
    text = "é" * 10  # two bytes each
    out = to_plain(text.encode(), 5)
    assert out["truncated"] is True
    assert out["value"] == "éé"


def test_to_plain_binary_is_base64():
    out = to_plain(b"\xff\xfe\x00", 100)
    assert out == {"base64": "//4A", "truncated": False, "total_bytes": 3}


def test_namespace_prefixes_and_hides():
    ns = Namespace("agent7")
    assert ns.to_server("notes") == "agent7:notes"
    assert ns.to_agent("agent7:notes") == "notes"
    assert ns.to_agent("other:notes") is None
    assert ns.scan_pattern_prefix == "agent7:"


def test_namespace_escapes_glob_characters_in_prefix():
    assert Namespace("a*b").scan_pattern_prefix == "a\\*b:"


def test_empty_namespace_is_transparent():
    ns = Namespace("")
    assert ns.to_server("k") == "k"
    assert ns.to_agent("k") == "k"


def test_namespace_rejects_empty_names():
    with pytest.raises(ValueError):
        Namespace("x").to_server("")


def test_settings_from_env():
    s = Settings.from_env({"HOTKV_URL": "hotkv://h:1", "HOTKV_MCP_READ_ONLY": "yes",
                           "HOTKV_MCP_NAMESPACE": "t1", "HOTKV_MCP_MAX_ITEMS": "5"})
    assert (s.url, s.read_only, s.namespace, s.max_items) == ("hotkv://h:1", True, "t1", 5)


def test_settings_reject_nonsense_limits():
    with pytest.raises(ValueError):
        Settings(max_items=0)


def test_cli_flags_override_environment():
    args = build_parser().parse_args(["--url", "hotkv://cli:2", "--read-only", "--namespace", "n"])
    s = settings_from_args(args, {"HOTKV_URL": "hotkv://env:1", "HOTKV_MCP_NAMESPACE": "e"})
    assert (s.url, s.read_only, s.namespace) == ("hotkv://cli:2", True, "n")
