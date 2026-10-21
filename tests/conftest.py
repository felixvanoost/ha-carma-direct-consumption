"""Shared test fixtures."""

import pytest
from aiohttp.test_utils import TestServer

from custom_components.carma_smart_metering import api, const

from .mock_carma import MockCarma

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
async def portal(socket_enabled, monkeypatch):
    mock = MockCarma()
    server = TestServer(mock.make_app())
    await server.start_server()
    monkeypatch.setattr(const, "BASE_URL", str(server.make_url("/portal/")))
    monkeypatch.setattr(api, "REQUEST_DELAY", 0)
    monkeypatch.setattr(api, "RETRY_DELAYS", (0, 0))
    try:
        yield mock
    finally:
        await server.close()
