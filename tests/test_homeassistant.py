"""Actual HA setup, service dispatch, entity limits, and unload tests (Linux CI)."""

from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.abl_wallbox.const import DOMAIN
from custom_components.abl_wallbox.protocol import ABLError
from test_protocol import SimulatedBus, p

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


class FakeBus(SimulatedBus):
    def __init__(self):
        super().__init__()
        self.last_frames = {}
        self.closed = False

    async def close(self):
        self.closed = True

    async def read(self, address, register, count, **kwargs):
        try:
            return await super().read(address, register, count, **kwargs)
        except p.ABLError as err:
            raise ABLError(str(err)) from err


async def setup_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ABL Test",
        version=1,
        unique_id="test-controller",
        data={"name": "ABL Test", "connection_type": "tcp", "host": "127.0.0.1", "port": 5020, "address": 1},
    )
    entry.add_to_hass(hass)
    bus = FakeBus()
    with patch("custom_components.abl_wallbox.Bus", return_value=bus):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, bus


def entity_id(hass, entry, platform, key):
    registry = er.async_get(hass)
    return registry.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")


async def test_setup_entities_limits_and_unload(hass):
    entry, bus = await setup_entry(hass)
    assert bus.writes == []
    target = hass.states.get(entity_id(hass, entry, "number", "requested_current"))
    installation = hass.states.get(entity_id(hass, entry, "number", "installation_limit"))
    rated = hass.states.get(entity_id(hass, entry, "sensor", "rated_current"))
    assert target.attributes["max"] == 16
    assert installation.attributes["max"] == 32
    assert rated.state == "32"
    assert hass.states.get(entity_id(hass, entry, "sensor", "F3")).state == "7"
    assert hass.states.get(entity_id(hass, entry, "sensor", "firmware")).state == "4.1"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert bus.closed


async def test_persistent_limit_updates_dynamic_target_maximum(hass):
    entry, bus = await setup_entry(hass)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id(hass, entry, "number", "installation_limit"), "value": 20},
        blocking=True,
    )
    await hass.async_block_till_done()
    target = hass.states.get(entity_id(hass, entry, "number", "requested_current"))
    assert target.attributes["max"] == 20
    assert bus.state == 0xA1


async def test_lowering_limit_reduces_target_value_and_its_maximum(hass):
    entry, bus = await setup_entry(hass)
    bus.installation = bus.requested = p.amps_to_pwm(32)
    await entry.runtime_data.async_request_refresh()
    assert hass.states.get(entity_id(hass, entry, "number", "requested_current")).state == "32"
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": entity_id(hass, entry, "number", "installation_limit"), "value": 16},
        blocking=True,
    )
    await hass.async_block_till_done()
    target = hass.states.get(entity_id(hass, entry, "number", "requested_current"))
    assert target.state == "16"
    assert target.attributes["max"] == 16
    assert bus.state == 0xA1


async def test_consecutive_current_commands_update_ha_immediately(hass):
    entry, _bus = await setup_entry(hass)
    target_id = entity_id(hass, entry, "number", "requested_current")
    for amps in (10, 16):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": target_id, "value": amps}, blocking=True
        )
        assert hass.states.get(target_id).state == str(amps)


async def test_service_changes_bus_address_without_replacing_device(hass):
    entry, bus = await setup_entry(hass)
    original = entity_id(hass, entry, "number", "address")
    await hass.services.async_call(
        DOMAIN, "configure_controller", {"config_entry_id": entry.entry_id, "address": 2}, blocking=True
    )
    await hass.async_block_till_done()
    assert bus.address == 2
    assert entry.data["address"] == 2
    assert entity_id(hass, entry, "number", "address") == original
    assert hass.states.get(original).state == "2"


async def test_config_flow_uses_read_only_probe(hass):
    bus = FakeBus()
    with patch("custom_components.abl_wallbox.config_flow.make_bus", return_value=bus):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == "form"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"connection_type": "tcp"}
        )
        assert result["step_id"] == "connection"
        with patch("custom_components.abl_wallbox.async_setup_entry", return_value=True):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={
                    "name": "ABL Test",
                    "host": "127.0.0.1",
                    "port": 5020,
                    "address": 1,
                },
            )
        assert result["type"] == "create_entry"
    assert bus.writes == []
    assert bus.closed


@pytest.fixture
def board_backend(tmp_path):
    import time
    from controller.runtime import ControllerRuntime, RuntimeThread
    from controller.shared import p as board_protocol
    from test_controller import RuntimeBus
    bus = RuntimeBus()
    runtime = ControllerRuntime(board_protocol.Controller(bus), tmp_path)
    worker = RuntimeThread(runtime)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while not runtime.snapshot()['available'] and time.monotonic() < deadline:
            time.sleep(.01)
        assert runtime.snapshot()['available']
        yield runtime, bus, worker
    finally:
        worker.stop()


async def test_reconfigure_tcp_to_api_preserves_all_entity_and_device_ids(hass, board_backend):
    from custom_components.abl_wallbox.api_client import APIConnection
    from homeassistant.helpers import device_registry as dr

    entry, old_bus = await setup_entry(hass)
    registry = er.async_get(hass)
    original_entities = {(item.unique_id, item.entity_id, item.device_id) for item in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert len(original_entities) == 50
    original_device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    history = {'time': '2026-01-01T00:00:00+00:00', 'type': 'observed_fault', 'code': 'F3', 'description': 'Test history'}
    entry.runtime_data.history.append(history)
    await entry.runtime_data._store.async_save(entry.runtime_data._storage_data())

    runtime, bus, worker = board_backend
    async def request(_connection, method, path, payload=None):
        assert path in ('/api/v1/state', '/api/v1/command')
        return await hass.async_add_executor_job(worker.submit, payload) if method == 'POST' else runtime.snapshot()

    with patch.object(APIConnection, 'request', request):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'reconfigure', 'entry_id': entry.entry_id})
        result = await hass.config_entries.flow.async_configure(result['flow_id'], user_input={'connection_type': 'api'})
        result = await hass.config_entries.flow.async_configure(result['flow_id'], user_input={
            'name': 'ABL Test', 'host': 'board.example', 'port': 8080, 'address': 1, 'api_key': 'test-only-' + 'x' * 32,
        })
        assert result['type'] == 'abort' and result['reason'] == 'reconfigure_successful'
        await hass.async_block_till_done()
        assert old_bus.closed
        assert len(hass.data[DOMAIN]) == 1
        assert entry.data['connection_type'] == 'api' and entry.data['controller_id'] == runtime.controller_id
        assert {(item.unique_id, item.entity_id, item.device_id) for item in er.async_entries_for_config_entry(registry, entry.entry_id)} == original_entities
        assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)}).id == original_device.id
        assert history in entry.runtime_data.history
        assert bus.writes == []
        target_id = entity_id(hass, entry, 'number', 'requested_current')
        await hass.services.async_call('number', 'set_value', {'entity_id': target_id, 'value': 10}, blocking=True)
        assert hass.states.get(target_id).state == '10'
        # A local change of the physical address follows the same pinned board, without replacing HA entities.
        await hass.async_add_executor_job(worker.submit, {'operation': 'configure', 'settings': {'address': 2}, 'address': 1, 'request_id': 'local-address-test-0001'})
        await entry.runtime_data.async_request_refresh()
        assert entry.data['address'] == 2
        assert entity_id(hass, entry, 'number', 'requested_current') == target_id
        assert await hass.config_entries.async_unload(entry.entry_id)
