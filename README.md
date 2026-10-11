# HotKV MCP Server

[![CI](https://github.com/hotkv-ltd/hotkv-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/hotkv-ltd/hotkv-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

A [Model Context Protocol](https://modelcontextprotocol.io) server for [HotKV](https://hotkv.com),
a RESP2/RESP3-compatible in-memory data store with built-in AI and LLM-serving features (semantic
and prompt caching, agent memory, RAG, feature store, rate limiting, time series and more).
It lets an AI assistant or agent (Claude, Cursor, VS Code, your own) read and write keys, keep
conversation memory and notes, log events, store prompt templates and search documents in a
HotKV server.

- A fixed set of 28 tools. There is no tool that runs an arbitrary command, so an agent cannot
  flush a database, change the server's configuration or read the keys of other tenants.
- `--read-only` removes every write tool, so the agent cannot even see them.
- `--namespace` confines the agent to one prefix: it can only use and list names under it.
- Results are capped (`--max-value-bytes`, `--max-items`), and a cut value is flagged
  `truncated`, so a huge value cannot flood the model's context.
- stdio for local clients, streamable HTTP for shared use.

Written against HotKV server 0.3.0 and the official `hotkv-client` Python package.

## Install

```bash
pip install hotkv-mcp
```

or run it without installing, with [uv](https://docs.astral.sh/uv/):

```bash
uvx hotkv-mcp --url hotkv://localhost:6379
```

Python 3.10 or newer.

## Connect a client

The server speaks stdio by default, so a client launches it as a subprocess.

**Claude Code**

```bash
claude mcp add hotkv -- uvx hotkv-mcp --url hotkv://localhost:6379
```

**Claude Desktop, Cursor and other clients that read an `mcpServers` file**

```json
{
  "mcpServers": {
    "hotkv": {
      "command": "uvx",
      "args": ["hotkv-mcp", "--read-only"],
      "env": { "HOTKV_URL": "hotkv://:your-password@localhost:6379" }
    }
  }
}
```

Keep the password in `HOTKV_URL` (or the client's secret store) and not on the command line,
where other users of the machine can see it. Use `hotkvs://` for a TLS connection.

## Options

| Option | Environment | Meaning |
| --- | --- | --- |
| `--url URL` | `HOTKV_URL` | Server to use. Default `hotkv://127.0.0.1:6379`. `hotkvs://`, `redis://` and `rediss://` also work. |
| `--read-only` | `HOTKV_MCP_READ_ONLY=1` | Offer read tools only. |
| `--namespace PREFIX` | `HOTKV_MCP_NAMESPACE` | Store everything under `PREFIX:`; the agent never sees the prefix and cannot reach other names. |
| `--max-value-bytes N` | `HOTKV_MCP_MAX_VALUE_BYTES` | Cut longer values. Default 65536. |
| `--max-items N` | `HOTKV_MCP_MAX_ITEMS` | Most keys, rows or hits per call. Default 200. |
| `--transport` | | `stdio` (default) or `streamable-http`. |
| `--host`, `--port` | | Bind address for `streamable-http`. Default `127.0.0.1:8000`, path `/mcp`. |

Command line options win over the environment.

Namespaces are plain prefixes: give each agent a namespace that is not itself a prefix of another
(`agent-a` and `agent-b`, not `agent` and `agent:b`), or the shorter one can reach the longer one's names.

## Tools

| Group | Tools |
| --- | --- |
| Server | `server_info` |
| Keys | `get`, `set`, `delete`, `key_info`, `scan` |
| Hashes | `hash_get`, `hash_set` |
| Lists | `list_range`, `list_push` |
| Conversation memory | `memory_append_message`, `memory_get_messages`, `memory_clear_messages` |
| Working notes | `memory_set_note`, `memory_get_note`, `memory_list_notes` |
| Event log | `memory_log_event`, `memory_query_events` |
| Rolling summary | `memory_set_summary`, `memory_get_summary` |
| Prompt registry | `prompt_save`, `prompt_get`, `prompt_versions`, `prompt_list` |
| Document search | `rag_create`, `rag_ingest`, `rag_search`, `rag_list` |

Each tool says in its MCP annotations whether it only reads, writes, or deletes, so clients can
ask for approval before the destructive ones. With `--read-only` the tools that write
(`set`, `delete`, `hash_set`, `list_push`, the `memory_` tools that add or clear, `prompt_save`,
`rag_create`, `rag_ingest`) are not registered at all.

The memory, prompt and document search tools use HotKV's AI engines, which need a HotKV license
that includes them. Without one the tool returns "This HotKV server is not licensed for that
feature" and the key tools keep working. `rag_search` ranks by keyword relevance, so it needs no
embedding model.

## Running over HTTP

```bash
hotkv-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

The server listens on `/mcp`. It does not authenticate callers. Keep it on localhost, or put it
behind a reverse proxy that does, and give it its own `--namespace` and `--read-only` where you can.
Starting it on another address prints a warning.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests            # unit tests; no server needed
HOTKV_TEST_URL=hotkv://127.0.0.1:6379 python -m pytest tests   # also the integration tests
```

The integration tests need a HotKV server licensed for the AI engines and create and delete keys
with a random prefix. Run them against a throwaway server.

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Support

- Bugs and feature requests: [GitHub issues](https://github.com/hotkv-ltd/hotkv-mcp/issues)
- Questions and commercial support: support@hotkv.com
- Security vulnerabilities: see [SECURITY.md](SECURITY.md)

## License

Licensed under the Apache License, Version 2.0: see [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Trademarks and affiliation

"HotKV" is a trademark of HotKV Ltd; the license does not grant rights to use it.
The HotKV server is a separate commercial product, and its enterprise commands need a HotKV license.

HotKV is an independent product of HotKV Ltd. It speaks the RESP protocol and implements
many Redis commands so that existing tools and client habits carry over, but it is not Redis,
Valkey, Dragonfly or KeyDB, and this package is built for and tested against HotKV. HotKV Ltd is not
affiliated with, endorsed by or sponsored by Redis Ltd., the Valkey project, DragonflyDB or KeyDB.
Redis is a registered trademark of Redis Ltd. Valkey, Dragonfly, KeyDB and all other product and
company names are trademarks of their respective owners; they are used here only to describe
protocol and command compatibility. "Model Context Protocol" and "MCP" refer to the open protocol
published at modelcontextprotocol.io.
