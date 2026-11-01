"""Exercise the actual HTTP transport without any Shelly or wallbox writes."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import sys
import threading
import urllib.request

import pytest

from conftest import protocol as p
from test_protocol import FIRMWARE


@pytest.fixture
def http_bridge():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(FIRMWARE)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, received
    server.shutdown()
    server.server_close()
    thread.join()


async def test_shelly_http_preserves_ascii_frame(http_bridge):
    port, received = http_bridge
    bus = p.ShellyBus("127.0.0.1", port=port, script_id=2)
    version = p.Version.parse(await bus.read(1, 1, 2))
    assert version.firmware == "4.1"
    assert received == [("/script/2/abl", b":010300010002F9\r\n")]
    await bus.close()


def test_shelly_script_id_is_validated():
    with pytest.raises(ValueError):
        p.ShellyBus("localhost", script_id=-1)


@pytest.mark.skipif(sys.version_info < (3, 14), reason="SHA-256 Digest support was added in Python 3.14")
async def test_shelly_sha256_digest_authentication():
    authenticated = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            authorization = self.headers.get("Authorization")
            if not authorization:
                self.send_response(401)
                self.send_header(
                    "WWW-Authenticate", 'Digest realm="shelly-test", nonce="test-nonce", algorithm=SHA-256, qop="auth"'
                )
                self.end_headers()
                return
            fields = urllib.request.parse_keqv_list(
                urllib.request.parse_http_list(authorization.removeprefix("Digest "))
            )

            def digest(value):
                return hashlib.sha256(value.encode()).hexdigest()

            a1 = digest("admin:shelly-test:example-password")
            a2 = digest("POST:" + fields["uri"])
            expected = digest(f"{a1}:test-nonce:{fields['nc']}:{fields['cnonce']}:auth:{a2}")
            if fields["response"] != expected:
                self.send_response(403)
                self.end_headers()
                return
            authenticated.append(body)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(FIRMWARE)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bus = p.ShellyBus("127.0.0.1", port=server.server_port, password="example-password")
        assert p.Version.parse(await bus.read(1, 1, 2)).firmware == "4.1"
        assert authenticated == [b":010300010002F9\r\n"]
        await bus.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
