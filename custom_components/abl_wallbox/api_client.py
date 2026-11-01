"""Board API adapter implementing the existing controller interface and entity data."""
from __future__ import annotations

import asyncio
import json
import math
import secrets
import urllib.error
import urllib.request

from .protocol import ABLError, ControllerBusy


class APIAuthenticationError(ABLError):
    """The configured board key is missing, expired or incorrect."""


class APIConnection:
    def __init__(self, host, port=8080, token=''):
        self.host, self.port, self._token = host, int(port), token
        self.last_frames = {}

    async def close(self):
        return None

    def _request(self, method, path, payload=None):
        host = f'[{self.host}]' if ':' in self.host and not self.host.startswith('[') else self.host
        request = urllib.request.Request(
            f'http://{host}:{self.port}{path}',
            data=json.dumps(payload, allow_nan=False).encode('utf-8') if payload is not None else None,
            headers={'Authorization': f'Bearer {self._token}', 'Content-Type': 'application/json', 'Accept': 'application/json'},
            method=method,
        )
        # Never forward the API key to a redirect destination or an environment proxy.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=70 if method == 'POST' else 10) as response:
                body = response.read(1_048_577)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise APIAuthenticationError('Zugriffsschlüssel prüfen oder Anmeldung am Board erneuern.') from None
            if 300 <= error.code < 400:
                raise ABLError('Die Board-API darf nicht auf eine andere Adresse weiterleiten.') from None
            body = error.read(1_048_577)
            if len(body) > 1_048_576:
                raise ABLError('Antwort der Board-API ist zu groß.') from None
            try:
                result = json.loads(body)
            except (ValueError, UnicodeError):
                raise ABLError(f'Board-API meldet HTTP {error.code}.') from None
            return result
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise ABLError('Board-API ist nicht erreichbar.') from error
        if len(body) > 1_048_576:
            raise ABLError('Antwort der Board-API ist zu groß.')
        try:
            result = json.loads(body)
        except (ValueError, UnicodeError) as error:
            raise ABLError('Ungültige JSON-Antwort der Board-API.') from error
        return result

    async def request(self, method, path, payload=None):
        return await asyncio.to_thread(self._request, method, path, payload)


class APIController:
    def __init__(self, bus, address=1, *, controller_id=None):
        self.bus, self.address = bus, address
        self.board_history = []
        self.controller_id = controller_id

    def _state(self, response, *, allow_address_change=False, allow_unavailable=False):
        if not isinstance(response, dict):
            raise ABLError('Ungültige API-Antwort.')
        state = response.get('state', response)
        if not isinstance(state, dict) or state.get('api_version') != 1 or not isinstance(state.get('controller_id'), str) or not state['controller_id']:
            raise ABLError('Nicht unterstützte Board-API.')
        if self.controller_id and self.controller_id != state['controller_id']:
            raise ABLError('An dieser Adresse antwortet ein anderes Board. Verbindung prüfen.')
        if allow_address_change or self.controller_id == state['controller_id']:
            data = state.get('data') if isinstance(state.get('data'), dict) else {}
            address = state.get('address', data.get('address'))
            if type(address) is int and 1 <= address <= 16:
                self.address = address
        error = response.get('error')
        if isinstance(error, dict):
            if error.get('code') == 'busy':
                raise ControllerBusy(error.get('message', 'Controller belegt.'))
            raise ABLError(error.get('message', 'Board-API meldet einen Fehler.'))
        if not state.get('available'):
            if allow_unavailable:
                return state.get('data', {})
            raise ABLError('Die Wallbox antwortet dem Board gerade nicht.')
        data = state.get('data')
        required = {
            'state', 'address', 'rated_current', 'installation_limit', 'requested_current',
            'firmware', 'hardware', 'error_counters', 'new_api', 'mode', 'enabled',
            'fallback_enabled', 'start_on_boot', 'imbalance_enabled', 'imbalance_limit',
            'imbalance_detected', 'overtemperature', 'meter_fault', 'upstream_lost',
            'effective_current', 'state_raw', 'charging', 'vehicle_connected', 'en1_closed', 'en2_closed',
            'duty_cycle', 'current_l1', 'current_l2', 'current_l3', 'estimated_power', 'upstream_timeout', 'system_flags', 'fault',
        }
        if not isinstance(data, dict) or required - set(data):
            raise ABLError('Unvollständige Daten der Board-API.')
        boolean_fields = {'new_api', 'enabled', 'charging', 'vehicle_connected', 'en1_closed', 'en2_closed', 'fallback_enabled',
                          'start_on_boot', 'imbalance_enabled', 'imbalance_detected', 'overtemperature', 'meter_fault', 'upstream_lost', 'upstream_timeout', 'fault'}
        numbers = {'rated_current', 'installation_limit', 'requested_current', 'effective_current', 'imbalance_limit', 'duty_cycle', 'state_raw', 'address'}
        nullable_numbers = {'current_l1', 'current_l2', 'current_l3', 'estimated_power'}
        if (any(type(data[key]) is not bool for key in boolean_fields)
                or any(type(data[key]) not in (int, float) or not math.isfinite(data[key]) for key in numbers)
                or any(data[key] is not None and (type(data[key]) not in (int, float) or not math.isfinite(data[key])) for key in nullable_numbers)
                or any(not isinstance(data[key], str) for key in ('state', 'mode', 'firmware', 'hardware', 'system_flags'))):
            raise ABLError('Ungültige Messwerte oder Statusangaben der Board-API.')
        counters = data['error_counters']
        if (not isinstance(counters, dict) or set(counters) != {f'F{i}' for i in range(1, 18)}
                or any(value is not None and (type(value) is not int or not 0 <= value <= 255) for value in counters.values())):
            raise ABLError('Ungültige Fehlerzähler der Board-API.')
        if data['address'] != self.address:
            raise ABLError('Controller-Busadresse stimmt nicht mit der HA-Verbindung überein.')
        self.controller_id = state['controller_id']
        history = state.get('history', [])
        if not isinstance(history, list) or len(history) > 100 or any(not isinstance(item, dict) or any(not isinstance(item.get(key), str) for key in ('time', 'code', 'description')) for item in history):
            raise ABLError('Ungültiger Fehlerverlauf der Board-API.')
        self.board_history = history
        return data

    async def refresh(self, *, counters=False):
        if counters:
            return self._state(await self._request_command('refresh'))
        return self._state(await self.bus.request('GET', '/api/v1/state'))

    async def _request_command(self, operation, **fields):
        payload = {'request_id': secrets.token_hex(16), 'operation': operation, 'address': self.address, **fields}
        return await self.bus.request('POST', '/api/v1/command', payload)

    async def _command(self, operation, **fields):
        response = await self._request_command(operation, **fields)
        self._state(response, allow_address_change=operation == 'configure', allow_unavailable=True)
        return response.get('result')

    async def set_requested_current(self, amps):
        if type(amps) is not int or (amps != 0 and not 6 <= amps <= 80):
            raise ValueError('Ladestrom muss 0 oder ein ganzer Wert ab 6 A sein.')
        await self._command('set_current', value=amps)

    async def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Ungültige Ladefreigabe.')
        await self._command('set_enabled', value=enabled)

    async def configure(self, **settings):
        await self._command('configure', settings=settings)

    async def restart(self):
        await self._command('restart', confirm=True)

    async def clear_fault_counters(self, *, confirm=False):
        if confirm is not True:
            raise ValueError('Fehlerzähler nur mit ausdrücklicher Bestätigung löschen.')
        await self._command('clear_fault_counters', confirm=True)

    async def refresh_counters(self):
        await self._command('refresh')

    async def scan(self):
        result = await self._command('scan')
        return result.get('controllers', []) if isinstance(result, dict) else []
