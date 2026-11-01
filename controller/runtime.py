"""One owner of the bus, read-only startup, persistent observations and explicit commands."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time

from .shared import p, STATE_LABELS

API_VERSION = 1


class CommandError(Exception):
    def __init__(self, message, code='invalid_request', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.abl-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default):
    if not Path(path).exists():
        return default
    return json.loads(Path(path).read_text(encoding='utf-8'))


class ControllerRuntime:
    def __init__(self, controller, directory, *, poll_interval=5, counter_interval=300):
        self.controller, self.bus = controller, controller.bus
        self.directory = Path(directory)
        self.poll_interval, self.counter_interval = poll_interval, counter_interval
        self._lock = asyncio.Lock()
        self._snapshot_lock = threading.Lock()
        self._available = False
        self._data = {}
        self._error = None
        self._observed_at = None
        self._updated = 0.0
        self._counter_time = None
        self._previous_state = None
        self._active_command = False
        self._stopping = False
        self._poll_task = None
        identity = read_json(self.directory / 'identity.json', {})
        if not identity:
            identity = {'controller_id': secrets.token_hex(16)}
            save_json(self.directory / 'identity.json', identity)
        self.controller_id = identity['controller_id']
        settings = read_json(self.directory / 'settings.json', {})
        if settings:
            address = settings.get('address')
            if type(address) is not int or not 1 <= address <= 16:
                raise ValueError('Invalid persisted controller address')
            self.controller.address = address
        saved = read_json(self.directory / 'history.json', {})
        self.history = saved.get('history', [])[-100:]
        self._counters = saved.get('counters', {})
        ledger = read_json(self.directory / 'commands.json', {})
        self._ledger = OrderedDict(list(ledger.items())[-128:])

    def snapshot(self):
        with self._snapshot_lock:
            available = self._available and time.monotonic() - self._updated <= max(30, self.poll_interval * 3)
            return {
                'api_version': API_VERSION, 'controller_id': self.controller_id,
                'address': self.controller.address,
                'available': available, 'observed_at': self._observed_at,
                'error': self._error if not available else None,
                'active_command': self._active_command, 'data': deepcopy(self._data),
                'history': deepcopy(self.history),
                'fault_descriptions': {f'F{i}': STATE_LABELS.get(f'F{i}', f'F{i}') for i in range(1, 18)},
            }

    async def start(self):
        self._poll_task = asyncio.create_task(self._poll(), name='abl-controller-poll')

    async def stop(self):
        self._stopping = True
        # Allow an explicit configuration command to finish, including its restore block.
        async with self._lock:
            if self._poll_task:
                self._poll_task.cancel()
            await self.bus.close()
        if self._poll_task:
            await asyncio.gather(self._poll_task, return_exceptions=True)

    async def _poll(self):
        while not self._stopping:
            async with self._lock:
                await self._refresh()
            await asyncio.sleep(self.poll_interval)

    async def refresh(self, *, counters=False):
        async with self._lock:
            await self._refresh(force_counters=counters)
        return self.snapshot()

    async def _refresh(self, *, force_counters=False):
        with_counters = force_counters or self._counter_time is None or time.monotonic() - self._counter_time >= self.counter_interval
        try:
            data = await self.controller.refresh(counters=with_counters)
            now = datetime.now(timezone.utc).isoformat()
            data['state_description'] = STATE_LABELS.get(data['state'], data['state'])
            data['fault'] = data['state'].startswith('F') or any(
                data[key] for key in ('imbalance_detected', 'overtemperature', 'meter_fault', 'upstream_lost'))
            changed = False
            if data['state'].startswith('F') and data['state'] != self._previous_state:
                self.history.append({'time': now, 'type': 'observed_fault', 'code': data['state'], 'description': data['state_description']})
                changed = True
            self._previous_state = data['state']
            for code, value in data['error_counters'].items():
                previous = self._counters.get(code)
                if value is not None and previous is not None and previous != value:
                    self.history.append({'time': now, 'type': 'counter_changed', 'code': code, 'previous': previous,
                                         'count': value, 'description': STATE_LABELS.get(code, code)})
                    changed = True
                if value is not None:
                    self._counters[code] = value
            self.history = self.history[-100:]
            if changed or with_counters:
                save_json(self.directory / 'history.json', {'history': self.history, 'counters': self._counters})
            if with_counters:
                self._counter_time = time.monotonic()
            data['last_fault'] = self.history[-1]['code'] if self.history else 'none'
            data['fault_history'] = deepcopy(self.history)
            with self._snapshot_lock:
                self._data, self._observed_at, self._updated = data, now, time.monotonic()
                self._available, self._error = True, None
        except (p.ABLError, OSError, ValueError) as error:
            with self._snapshot_lock:
                self._available, self._error = False, str(error)
            await self.bus.close()

    @staticmethod
    def validate(command):
        if not isinstance(command, dict) or set(command) - {'request_id', 'operation', 'address', 'value', 'settings', 'confirm'}:
            raise CommandError('Ungültige Anfragefelder.')
        identifier = command.get('request_id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', identifier):
            raise CommandError('Eine eindeutige Anfragekennung ist erforderlich.')
        operation = command.get('operation')
        if operation not in ('set_current', 'set_enabled', 'configure', 'refresh', 'restart', 'clear_fault_counters', 'scan'):
            raise CommandError('Unbekannte Aktion.')
        if type(command.get('address')) is not int or not 1 <= command['address'] <= 16:
            raise CommandError('Ungültige Busadresse.')
        expected = {'request_id', 'operation', 'address'}
        if operation == 'set_current':
            expected.add('value')
            value = command.get('value')
            if type(value) is not int or (value != 0 and not 6 <= value <= 80):
                raise CommandError('Ladestrom muss 0 oder ein ganzer Wert ab 6 A sein.')
        if operation == 'set_enabled':
            expected.add('value')
            if type(command.get('value')) is not bool:
                raise CommandError('Ladefreigabe muss Ja oder Nein sein.')
        if operation == 'configure':
            expected.add('settings')
            settings = command.get('settings')
            allowed = {'installation_limit', 'address', 'mode', 'imbalance', 'fallback_enabled', 'start_on_boot'}
            if not isinstance(settings, dict) or not settings or set(settings) - allowed:
                raise CommandError('Ungültige Einstellungen.')
            for key, value in settings.items():
                if key in ('installation_limit', 'address'):
                    lower, upper = (6, 80) if key == 'installation_limit' else (1, 16)
                    if type(value) is not int or not lower <= value <= upper:
                        raise CommandError('Ungültiger ganzzahliger Einstellwert.')
                elif key in ('fallback_enabled', 'start_on_boot'):
                    if type(value) is not bool:
                        raise CommandError('Ungültiger Schalterwert.')
                elif not isinstance(value, str) or value not in ({'standalone', 'rfid', 'sbc'} if key == 'mode' else {'off', '16', '20'}):
                    raise CommandError('Ungültige Auswahl.')
        if operation in ('restart', 'clear_fault_counters'):
            expected.add('confirm')
            if command.get('confirm') is not True:
                raise CommandError('Eine ausdrückliche Bestätigung ist erforderlich.')
        if set(command) != expected:
            raise CommandError('Die Anfrage passt nicht zur gewählten Aktion.')
        return identifier, operation

    async def execute(self, command):
        identifier, operation = self.validate(command)
        fingerprint = hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()
        async with self._lock:
            if self._stopping:
                raise CommandError('Der Dienst wird neu gestartet.', 'unavailable', 503)
            previous = self._ledger.get(identifier)
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise CommandError('Anfragekennung wurde bereits für andere Daten benutzt.', 'conflict', 409)
                if previous['status'] == 'pending':
                    raise CommandError('Diese Anfrage wurde bereits begonnen. Bitte den aktuellen Status prüfen.', 'uncertain', 409)
                if previous['status'] == 'error':
                    raise CommandError(previous['error'], previous['code'], previous['http_status'])
                return {**self.snapshot(), 'replayed': True, 'result': previous.get('result')}
            if command['address'] != self.controller.address:
                raise CommandError('Die Busadresse wurde geändert. Verbindung neu konfigurieren.', 'address_mismatch', 409)
            # Durable intent prevents an ambiguous network retry from repeating a write after a restart.
            self._ledger[identifier] = {'fingerprint': fingerprint, 'status': 'pending'}
            while len(self._ledger) > 128:
                self._ledger.popitem(last=False)
            save_json(self.directory / 'commands.json', self._ledger)
            self._active_command = True
            result = None
            failure = None
            try:
                if operation == 'set_current':
                    await self.controller.set_requested_current(command['value'])
                elif operation == 'set_enabled':
                    await self.controller.set_enabled(command['value'])
                elif operation == 'configure':
                    await self.controller.configure(**command['settings'])
                elif operation == 'restart':
                    await self.controller.restart()
                elif operation == 'clear_fault_counters':
                    await self.controller.clear_fault_counters(confirm=True)
                elif operation == 'scan':
                    result = {'controllers': await self.controller.scan()}
            except (p.ABLError, ValueError, OSError) as error:
                code = 'busy' if isinstance(error, p.ControllerBusy) else 'controller_error'
                failure = CommandError(str(error), code, 409 if code == 'busy' else 502)
            finally:
                # The protocol may confirm a new address even when a later restore step fails.
                try:
                    save_json(self.directory / 'settings.json', {'address': self.controller.address})
                    await self._refresh(force_counters=operation in ('refresh', 'clear_fault_counters'))
                finally:
                    self._active_command = False
            if failure:
                self._ledger[identifier].update(status='error', error=str(failure), code=failure.code, http_status=failure.status)
            else:
                self._ledger[identifier].update(status='success', result=result)
            save_json(self.directory / 'commands.json', self._ledger)
            if failure:
                raise failure
            return {**self.snapshot(), 'result': result, 'replayed': False}


class RuntimeThread:
    """HTTP threads submit to one dedicated asyncio loop; they never access the bus."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name='abl-controller', daemon=True)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.runtime.start())
        self.loop.run_forever()
        self.loop.close()

    def start(self):
        self.thread.start()

    def submit(self, command, timeout=75):
        future = asyncio.run_coroutine_threadsafe(self.runtime.execute(command), self.loop)
        return future.result(timeout)

    def stop(self):
        future = asyncio.run_coroutine_threadsafe(self.runtime.stop(), self.loop)
        future.result(80)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
