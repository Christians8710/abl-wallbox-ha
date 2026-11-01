"""Standalone controller ownership, durable commands and real authenticated HTTP transport."""
import asyncio
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import types
from http.server import ThreadingHTTPServer

import pytest

from controller.http_api import ControllerAPI
from controller.runtime import CommandError, ControllerRuntime, RuntimeThread
from controller.shared import p
from conftest import protocol as test_protocol
from test_protocol import SimulatedBus


class RuntimeBus(SimulatedBus):
    closed = False

    async def close(self):
        self.closed = True

    async def read(self, *args, **kwargs):
        try:
            return await super().read(*args, **kwargs)
        except test_protocol.ABLError as error:
            raise p.ABLError(str(error)) from error


def load_adapter():
    package = types.ModuleType('standalone_adapter_test')
    package.__path__ = []
    sys.modules[package.__name__] = package
    sys.modules[package.__name__ + '.protocol'] = p
    spec = importlib.util.spec_from_file_location(package.__name__ + '.api_client', Path(__file__).parents[1] / 'custom_components/abl_wallbox/api_client.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adapter = load_adapter()


def make_runtime(path, **kwargs):
    bus = RuntimeBus()
    return ControllerRuntime(p.Controller(bus), path, **kwargs), bus


def command(operation='set_current', *, identifier='a' * 32, **kwargs):
    return {'request_id': identifier, 'operation': operation, 'address': 1, **kwargs}


async def test_startup_polling_and_reconnect_never_write(tmp_path):
    runtime, bus = make_runtime(tmp_path, poll_interval=.01)
    await runtime.start()
    await asyncio.sleep(.04)
    assert runtime.snapshot()['available']
    assert runtime.snapshot()['data']['installation_limit'] == 16
    assert bus.writes == []
    await runtime.stop()
    assert bus.closed
    restored, _ = make_runtime(tmp_path)
    assert restored.controller_id == runtime.controller_id
    assert restored._counters['F3'] == 7


async def test_limit_reduction_is_verified_and_does_not_repeat_after_restart(tmp_path):
    runtime, bus = make_runtime(tmp_path)
    bus.installation = bus.requested = p.amps_to_pwm(32)
    request = command('configure', settings={'installation_limit': 16})
    result = await runtime.execute(request)
    assert result['data']['installation_limit'] == result['data']['requested_current'] == 16
    writes = list(bus.writes)
    result = await runtime.execute(request)
    assert result['replayed'] and bus.writes == writes
    restored, fresh_bus = make_runtime(tmp_path)
    assert (await restored.execute(request))['replayed']
    assert fresh_bus.writes == []
    with pytest.raises(CommandError, match='andere Daten'):
        await runtime.execute({**request, 'settings': {'installation_limit': 20}})


async def test_current_limit_enforced_and_failures_not_retried(tmp_path):
    runtime, bus = make_runtime(tmp_path)
    request = command(value=32)
    with pytest.raises(CommandError):
        await runtime.execute(request)
    assert bus.writes == []
    bus.installation = p.amps_to_pwm(32)
    with pytest.raises(CommandError):
        await runtime.execute(request)
    assert bus.writes == []


async def test_address_change_is_persistent_without_new_startup_writes(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    result = await runtime.execute(command('configure', settings={'address': 2}))
    assert result['address'] == result['data']['address'] == 2
    restored, bus = make_runtime(tmp_path)
    assert restored.controller.address == 2 and bus.writes == []
    with pytest.raises(CommandError) as error:
        await restored.execute(command(identifier='b' * 32, value=6))
    assert error.value.code == 'address_mismatch' and bus.writes == []


async def test_pending_intent_after_interruption_does_not_repeat_write(tmp_path):
    runtime, bus = make_runtime(tmp_path)
    async def interrupted(_):
        raise asyncio.CancelledError()
    runtime.controller.set_requested_current = interrupted
    with pytest.raises(asyncio.CancelledError):
        await runtime.execute(command(value=6))
    assert not runtime.snapshot()['active_command']
    restored, _ = make_runtime(tmp_path)
    with pytest.raises(CommandError) as error:
        await restored.execute(command(value=6))
    assert error.value.code == 'uncertain' and bus.writes == []


async def test_offline_keeps_cached_values_marked_unavailable(tmp_path):
    runtime, bus = make_runtime(tmp_path)
    await runtime.refresh()
    bus.address = 2
    result = await runtime.refresh()
    assert result['available'] is False and result['observed_at']
    assert result['data']['installation_limit'] == 16 and bus.writes == []


async def test_fault_observations_persist_independently_of_ha(tmp_path):
    runtime, bus = make_runtime(tmp_path)
    await runtime.refresh()
    bus.state = 0xF3
    await runtime.refresh()
    await runtime.refresh()
    assert len(runtime.snapshot()['history']) == 1
    restored, _ = make_runtime(tmp_path)
    assert restored.snapshot()['history'][0]['code'] == 'F3'


async def test_full_commands_and_polls_do_not_interleave(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    active = maximum = 0
    async def observed(method, *args, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        try:
            await asyncio.sleep(.002)
            return await method(*args, **kwargs)
        finally:
            active -= 1
    original_refresh = runtime.controller.refresh
    original_set = runtime.controller.set_requested_current
    runtime.controller.refresh = lambda **kwargs: observed(original_refresh, **kwargs)
    runtime.controller.set_requested_current = lambda value: observed(original_set, value)
    await asyncio.gather(runtime.execute(command(value=10)), runtime.refresh(), runtime.execute(command(identifier='b' * 32, value=12)))
    assert maximum == 1
    assert runtime.snapshot()['data']['requested_current'] == 12


async def test_adapter_rejects_invalid_data_and_other_board_before_changing_address(tmp_path):
    runtime, _ = make_runtime(tmp_path)
    await runtime.refresh()
    snapshot = runtime.snapshot()
    client = adapter.APIController(None, controller_id='other-board')
    snapshot['address'] = 2
    with pytest.raises(p.ABLError, match='anderes Board'):
        client._state(snapshot, allow_address_change=True)
    assert client.address == 1
    for field, value in [('enabled', 'yes'), ('state', []), ('effective_current', float('nan')), ('error_counters', {'F1': 0})]:
        snapshot = runtime.snapshot()
        snapshot['data'][field] = value
        with pytest.raises(p.ABLError):
            adapter.APIController(None)._state(snapshot)


@pytest.mark.parametrize('payload', [
    command(value=True), command(value=1.5), command(value=5), command(value=81),
    command('set_enabled', value=1), command('configure', settings={'mode': []}),
    command('configure', settings={'address': True}), command('configure', settings={'unknown': 1}),
    command('restart'), command('clear_fault_counters', confirm=False),
    command('refresh', value=6), command(identifier='short', value=6),
])
async def test_invalid_commands_are_rejected_before_bus_access(tmp_path, payload):
    runtime, bus = make_runtime(tmp_path)
    with pytest.raises(CommandError):
        await runtime.execute(payload)
    assert bus.writes == [] and not (tmp_path / 'commands.json').exists()


@pytest.fixture
def http_board(tmp_path):
    spec = importlib.util.spec_from_file_location('test_standalone_bridge', Path(__file__).parents[1] / 'bridge/abl-bridge.py')
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    runtime, bus = make_runtime(tmp_path)
    worker = RuntimeThread(runtime)
    token = 'test-only-key-' + 'a' * 40
    token_file = tmp_path / 'api-key'
    token_file.write_text(token)
    application = ControllerAPI(worker, token_file)
    class Handler(bridge.StatusPage):
        def do_GET(self):
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/api/v1/state')
                self.end_headers()
            else:
                super().do_GET()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.controller_api = application
    worker.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while not runtime.snapshot()['available'] and time.monotonic() < deadline:
        time.sleep(.01)
    assert runtime.snapshot()['available']
    port = server.server_address[1]
    def request(method, path, body=None, *, headers=None, authorized=True, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        sent = {'Content-Type': 'application/json'}
        if authorized:
            sent['Authorization'] = 'Bearer ' + token
        sent.update(headers or {})
        connection.request(method, path, raw if raw is not None else json.dumps(body) if body is not None else None, headers=sent)
        response = connection.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), json.loads(data) if response.getheader('Content-Type', '').startswith('application/json') else data
        connection.close()
        return result
    yield runtime, bus, request, token, port
    server.shutdown()
    server.server_close()
    worker.stop()
    thread.join(3)


def test_http_requires_auth_and_hides_cache_from_unknown_clients(http_board):
    runtime, bus, request, _, port = http_board
    status, headers, body = request('GET', '/api/v1/state', authorized=False)
    assert status == 401 and 'data' not in body and 'state' not in body
    assert headers['Cache-Control'] == 'no-store'
    assert request('GET', '/api/v1/state', headers={'Host': f'evil.invalid:{port}'})[0] == 401
    assert request('GET', '/api/v1/state?token=secret')[0] == 400
    assert request('POST', '/api/v1/command', command(value=10), authorized=False)[0] == 401
    assert request('GET', '/api/v1/state')[2]['controller_id'] == runtime.controller_id
    assert bus.writes == []


def test_browser_session_csrf_origin_and_logout(http_board):
    _, bus, request, token, port = http_board
    origin = f'http://127.0.0.1:{port}'
    assert request('POST', '/api/v1/session', {'token': token}, authorized=False)[0] == 403
    status, headers, body = request('POST', '/api/v1/session', {'token': token}, headers={'Origin': origin}, authorized=False)
    assert status == 200 and 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=Strict' in headers['Set-Cookie']
    cookie = headers['Set-Cookie'].split(';')[0]
    session_headers = {'Cookie': cookie, 'Origin': origin}
    assert request('POST', '/api/v1/command', command(value=10), headers=session_headers, authorized=False)[0] == 403
    assert request('POST', '/api/v1/command', command(value=10), headers={**session_headers, 'X-ABL-CSRF': body['csrf'], 'Origin': 'http://evil.invalid'}, authorized=False)[0] != 200
    session_headers['X-ABL-CSRF'] = body['csrf']
    assert request('POST', '/api/v1/command', command(value=10), headers=session_headers, authorized=False)[0] == 200
    assert bus.writes
    assert request('POST', '/api/v1/logout', {}, headers=session_headers, authorized=False)[0] == 200
    assert request('GET', '/api/v1/state', headers={'Cookie': cookie}, authorized=False)[0] == 401


def test_http_strict_body_validation_and_rate_limit(http_board):
    _, bus, request, token, port = http_board
    assert request('POST', '/api/v1/command', raw='{"value":NaN}')[0] == 400
    assert request('POST', '/api/v1/command', raw='x' * 8193)[0] == 413
    assert request('POST', '/api/v1/command', command(value=6), headers={'Content-Type': 'text/plain'})[0] == 415
    assert bus.writes == []
    for _ in range(5):
        assert request('POST', '/api/v1/session', {'token': 'wrong'}, headers={'Origin': f'http://127.0.0.1:{port}'}, authorized=False)[0] == 401
    assert request('POST', '/api/v1/session', {'token': token}, headers={'Origin': f'http://127.0.0.1:{port}'}, authorized=False)[0] == 429


async def test_ha_adapter_uses_real_api_and_preserves_controller_fields(http_board):
    _, bus, _, token, port = http_board
    client = adapter.APIConnection('127.0.0.1', port, token)
    controller = adapter.APIController(client)
    data = await controller.refresh(counters=True)
    assert data['installation_limit'] == 16 and data['rated_current'] == 32 and bus.writes == []
    await controller.set_requested_current(10)
    assert (await controller.refresh())['requested_current'] == 10
    await controller.configure(address=2)
    assert controller.address == bus.address == 2
    assert (await controller.refresh())['address'] == 2
    with pytest.raises(p.ABLError, match='weiterleiten'):
        await client.request('GET', '/redirect')
    with pytest.raises(adapter.APIAuthenticationError):
        await adapter.APIConnection('127.0.0.1', port, 'wrong').request('GET', '/api/v1/state')
