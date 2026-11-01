"""Authenticated local API and browser sessions. No CORS or credentials in URLs."""
from collections import OrderedDict
from concurrent.futures import TimeoutError as FutureTimeout
from http.cookies import CookieError, SimpleCookie
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit

from .runtime import CommandError


def valid_host(handler, *, require_origin=False):
    try:
        host = urlsplit('http://' + handler.headers.get('Host', ''))
        names = {handler.connection.getsockname()[0], socket.gethostname().lower(),
                 socket.gethostname().lower() + '.local', 'abl-bridge', 'abl-bridge.local', 'localhost', '127.0.0.1'}
        if (host.hostname not in names or host.username or host.password or host.path or host.query or host.fragment
                or (host.port or 80) != handler.server.server_address[1]):
            return False
        origin_header = handler.headers.get('Origin')
        if not origin_header:
            return not require_origin
        origin = urlsplit(origin_header)
        return (origin.scheme == 'http' and not origin.username and not origin.password and not origin.path
                and not origin.query and not origin.fragment and origin.hostname == host.hostname
                and (origin.port or 80) == (host.port or 80))
    except ValueError:
        return False


def json_response(handler, status, body, *, cookie=None):
    data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
    handler.send_response(status)
    for key, value in {
        'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
        'X-Frame-Options': 'DENY', 'Content-Security-Policy': "frame-ancestors 'none'",
        'X-Content-Type-Options': 'nosniff', 'Content-Length': str(len(data)),
    }.items():
        handler.send_header(key, value)
    if cookie:
        handler.send_header('Set-Cookie', cookie)
    handler.end_headers()
    try:
        handler.wfile.write(data)
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionError):
        pass


class APIAuth:
    def __init__(self, token_path):
        path = Path(token_path)
        if path.is_symlink():
            raise ValueError('Refusing an API key symlink')
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
                stream.write(secrets.token_urlsafe(32) + '\n')
        os.chmod(path, 0o600)
        self._token = path.read_text(encoding='ascii').strip()
        if len(self._token) < 32 or not self._token.isascii():
            raise ValueError('Invalid API key file')
        self._sessions = OrderedDict()
        self._failures = OrderedDict()
        self._lock = threading.Lock()

    def _session_key(self, handler):
        try:
            cookies = SimpleCookie()
            cookies.load(handler.headers.get('Cookie', ''))
            return cookies['abl_session'].value if 'abl_session' in cookies else ''
        except CookieError:
            return ''

    def context(self, handler):
        if not valid_host(handler):
            return None
        authorization = handler.headers.get('Authorization', '')
        if authorization.startswith('Bearer '):
            supplied = authorization[7:]
            if len(supplied) < 256 and secrets.compare_digest(supplied.encode('utf-8'), self._token.encode('ascii')):
                return {'kind': 'api'}
            return None
        key = self._session_key(handler)
        with self._lock:
            session = self._sessions.get(key)
            if session and session['expires'] > time.monotonic():
                return {'kind': 'browser', 'key': key, 'csrf': session['csrf']}
            self._sessions.pop(key, None)
        return None

    def login(self, handler, token):
        if not valid_host(handler, require_origin=True):
            raise CommandError('Anmeldung nur von dieser Webseite erlaubt.', 'forbidden', 403)
        address, now = handler.client_address[0], time.monotonic()
        with self._lock:
            failures, since = self._failures.get(address, (0, now))
            if now - since >= 60:
                failures, since = 0, now
            if failures >= 5:
                raise CommandError('Bitte nach einer Minute erneut versuchen.', 'rate_limited', 429)
            if not isinstance(token, str) or len(token) >= 256 or not secrets.compare_digest(token.encode('utf-8'), self._token.encode('ascii')):
                self._failures[address] = failures + 1, since
                while len(self._failures) > 256:
                    self._failures.popitem(last=False)
                raise CommandError('Zugriffsschlüssel nicht gültig.', 'invalid_auth', 401)
            self._failures.pop(address, None)
            key, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self._sessions[key] = {'csrf': csrf, 'expires': now + 12 * 3600}
            while len(self._sessions) > 64:
                self._sessions.popitem(last=False)
        return key, csrf

    def verify_write(self, handler, context):
        if not context:
            raise CommandError('Bitte anmelden oder API-Zugriffsschlüssel prüfen.', 'invalid_auth', 401)
        if context['kind'] == 'browser':
            supplied = handler.headers.get('X-ABL-CSRF', '')
            if not valid_host(handler, require_origin=True) or not secrets.compare_digest(supplied.encode('utf-8'), context['csrf'].encode('ascii')):
                raise CommandError('Bitte die Seite neu laden und erneut versuchen.', 'forbidden', 403)

    def logout(self, context):
        if context['kind'] == 'browser':
            with self._lock:
                self._sessions.pop(context['key'], None)


class ControllerAPI:
    def __init__(self, worker, token_path):
        self.worker = worker
        self.auth = APIAuth(token_path)

    def _body(self, handler):
        lengths = handler.headers.get_all('Content-Length', [])
        if handler.headers.get('Transfer-Encoding') or len(lengths) != 1:
            raise CommandError('Ungültige Anfragegröße.')
        if handler.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            raise CommandError('JSON-Anfrage erforderlich.', 'unsupported_media', 415)
        try:
            size = int(lengths[0])
            if not 0 < size <= 8192:
                raise CommandError('Anfrage zu groß oder leer.', 'invalid_request', 413)
            handler.connection.settimeout(5)
            def invalid_constant(_):
                raise ValueError('Nonfinite JSON number')
            value = json.loads(handler.rfile.read(size).decode('utf-8'), parse_constant=invalid_constant)
            if not isinstance(value, dict):
                raise ValueError('JSON object required')
            return value
        except (ValueError, UnicodeError, TimeoutError):
            raise CommandError('Ungültige JSON-Anfrage.') from None

    def get(self, handler):
        path = urlsplit(handler.path)
        if path.query or path.fragment:
            json_response(handler, 400, {'error': {'code': 'invalid_request', 'message': 'Keine Zugangsdaten in URLs verwenden.'}})
            return
        if not self.auth.context(handler):
            json_response(handler, 401, {'error': {'code': 'invalid_auth', 'message': 'Anmeldung erforderlich.'}})
            return
        if path.path == '/api/v1/session':
            context = self.auth.context(handler)
            json_response(handler, 200, {'authenticated': True, 'csrf': context.get('csrf')})
        elif path.path in ('/api/v1/state', '/api/v1/info'):
            state = self.worker.runtime.snapshot()
            if path.path.endswith('/info'):
                state = {key: state[key] for key in ('api_version', 'controller_id', 'available')}
            json_response(handler, 200, state)
        else:
            json_response(handler, 404, {'error': {'code': 'not_found', 'message': 'Schnittstelle nicht gefunden.'}})

    def post(self, handler):
        context = self.auth.context(handler)
        try:
            if handler.path != '/api/v1/session':
                self.auth.verify_write(handler, context)
            body = self._body(handler)
            if handler.path == '/api/v1/session':
                if set(body) != {'token'}:
                    raise CommandError('Nur Zugriffsschlüssel angeben.')
                key, csrf = self.auth.login(handler, body['token'])
                json_response(handler, 200, {'authenticated': True, 'csrf': csrf},
                              cookie=f'abl_session={key}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200')
            elif handler.path == '/api/v1/logout':
                if body:
                    raise CommandError('Ungültige Abmeldeanfrage.')
                self.auth.logout(context)
                json_response(handler, 200, {'authenticated': False}, cookie='abl_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            elif handler.path == '/api/v1/command':
                result = self.worker.submit(body)
                json_response(handler, 200, result)
            else:
                raise CommandError('Schnittstelle nicht gefunden.', 'not_found', 404)
        except CommandError as error:
            result = {'error': {'code': error.code, 'message': str(error)}}
            if context:
                result['state'] = self.worker.runtime.snapshot()
            json_response(handler, error.status, result)
        except FutureTimeout:
            json_response(handler, 504, {'error': {'code': 'uncertain', 'message': 'Aktion läuft möglicherweise noch. Status prüfen; dieselbe Anfrage nicht mit neuer Kennung wiederholen.'}})
        except (OSError, ValueError):
            json_response(handler, 503, {'error': {'code': 'unavailable', 'message': 'Aktion konnte nicht sicher abgeschlossen werden. Status prüfen.'}})
