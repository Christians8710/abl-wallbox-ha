#!/usr/bin/python3
"""Stable Comitup portal with per-call D-Bus connections.

The upstream portal shares a connection created before Flask request threads
and the connection worker are started. Do not reuse that connection across
threads or a fork when requesting WiFi networks or saving credentials.
"""
import dbus
from pathlib import Path
from flask import Flask, send_from_directory
from comitup_web import comitupweb as portal

class PortalClient:
    methods = portal.ciu.CiuClient.methods
    def __init__(self):
        self.service = None
    def __getattr__(self, name):
        if name not in self.methods:
            raise AttributeError(name)
        def call(*args):
            bus = dbus.SystemBus(private=True)
            try:
                proxy = bus.get_object("com.github.davesteele.comitup", "/com/github/davesteele/comitup", introspect=False)
                method = proxy.get_dbus_method(self.methods[name], "com.github.davesteele.comitup")
                values = tuple(value.decode("utf-8") if isinstance(value, bytes) else value for value in args)
                return method(*values, timeout=20)
            finally:
                bus.close()
        return call

portal.ciu.CiuClient = PortalClient
ui_path = Path(__file__).resolve().parent / 'ui'
if not ui_path.is_dir():
    ui_path = Path('/usr/local/share/abl-bridge/ui')
upstream_create_app = portal.create_app

def create_abl_app(log):
    """Use local WallboxPilot templates; leave radio, credentials and routes upstream."""
    portal.TEMPLATE_PATH = str(ui_path / 'portal')
    app = upstream_create_app(log)

    @app.route('/abl-assets/<name>')
    def abl_asset(name):
        if name not in ('abl-design.css', 'icon.png', 'LICENSE', 'portal.js'):
            from flask import abort
            abort(404)
        return send_from_directory(ui_path, name)

    return app

portal.create_app = create_abl_app
upstream_run = Flask.run
def run_portal(app, *args, **kwargs):
    kwargs["threaded"] = False
    return upstream_run(app, *args, **kwargs)
Flask.run = run_portal

if __name__ == "__main__":
    portal.main()
