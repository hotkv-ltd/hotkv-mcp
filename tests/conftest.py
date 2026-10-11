# Copyright (c) 2026 HotKV Ltd
# Licensed under the Apache License, Version 2.0. See LICENSE in the project root.

import os
import uuid

import pytest

import hotkv
from hotkv_mcp import Settings, create_server

URL = os.environ.get("HOTKV_TEST_URL")


@pytest.fixture(scope="session")
def live():
    if not URL:
        pytest.skip("set HOTKV_TEST_URL to a licensed HotKV server to run the integration tests")
    client = hotkv.from_url(URL)
    client.ping()
    return client


@pytest.fixture(scope="session")
def licensed(live):
    """Skip the tests of the AI engines (memory, prompts, RAG) on a server without a license for them."""
    probe = f"mcptest-probe-{uuid.uuid4().hex[:8]}"
    try:
        live.amem_conv_create(probe)
    except hotkv.NotLicensedError:
        pytest.skip("this HotKV server is not licensed for the AI engines")
    live.amem_conv_drop(probe)


@pytest.fixture
def prefix():
    return f"mcptest-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def make_server(live):
    def make(**kw):
        return create_server(Settings(url=URL, **kw), client=live)

    return make
