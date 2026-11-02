#!/usr/bin/python3
"""Update an existing WallboxPilot board installation, preserving configuration and rolling back on failure.

Copyright (c) 2026 Christians8710. MIT License. Does not install an OS or dependencies.
"""
from pathlib import Path
import argparse
import ast
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from urllib.request import urlopen

SOURCE = Path(__file__).resolve().parent
BACKUPS = Path('/var/backups/abl-bridge')
VERSION = '1.2.1'
CONFIG = Path('/etc/abl-bridge.json')
TARGETS = {
    'abl-bridge.py': '/usr/local/sbin/abl-bridge',
    'evcc-comitup-web.py': '/usr/local/sbin/evcc-comitup-web',
    'VERSION': '/usr/local/share/abl-bridge/VERSION',
    'abl-controller.conf': '/etc/systemd/system/abl-bridge.service.d/controller.conf',
    'network-diagnostics.py': '/usr/local/sbin/abl-network-diagnostics',
    'abl-network-diagnostics.service': '/etc/systemd/system/abl-network-diagnostics.service',
    **{f'controller/{name}': f'/usr/local/share/abl-bridge/controller/{name}' for name in (
        '__init__.py', 'shared.py', 'runtime.py', 'http_api.py', 'protocol.py', 'const.py',
        'web/index.html', 'web/app.js')},
    **{f'ui/{name}': f'/usr/local/share/abl-bridge/ui/{name}' for name in (
        'abl-design.css', 'icon.png', 'LICENSE', 'portal.js', 'status.html',
        'portal/base.html', 'portal/index.html', 'portal/confirm.html', 'portal/connect.html')},
}

def source_file(name, source=SOURCE):
    path = source / name
    if not path.exists() and name.startswith('controller/'):
        # Development checkout: package the very same protocol used by the HA integration.
        if name in ('controller/protocol.py', 'controller/const.py'):
            path = source.parent / 'custom_components/abl_wallbox' / Path(name).name
        else:
            path = source.parent / name
    return path

def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=110).stdout

def validate_sources(source=SOURCE):
    for name in TARGETS:
        path = source_file(name, source)
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f'Missing or invalid update file: {name}')
        if name.endswith('.py'):
            ast.parse(path.read_text(encoding='utf-8'))
    if (source / 'ui/icon.png').read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
        raise RuntimeError('Invalid PNG logo')

def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.abl-update-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def snapshot(targets, backup):
    backup.mkdir(parents=True, exist_ok=False)
    os.chmod(backup, 0o700)
    records = {}
    for key, destination in targets.items():
        path = Path(destination)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise RuntimeError(f'Refusing a symlink target: {path}')
        record = {'exists': path.exists()}
        if path.exists():
            if not path.is_file():
                raise RuntimeError(f'Refusing a non-file target: {path}')
            record['mode'] = stat.S_IMODE(path.stat().st_mode)
            stored = backup / key
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(path.read_bytes())
            os.chmod(stored, 0o600)
        records[key] = record
    (backup / 'manifest.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    return records

def restore(targets, backup, records):
    for key, destination in targets.items():
        path = Path(destination)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise RuntimeError(f'Refusing a symlink during rollback: {path}')
        record = records[key]
        if record['exists']:
            atomic_write(path, (backup / key).read_bytes(), record['mode'])
        elif path.exists():
            path.unlink()

def write_update(source, targets, records):
    # Install assets before switching the programs that refer to them.
    for key in sorted(targets, key=lambda name: not name.startswith('ui/')):
        mode = records[key].get('mode', 0o755 if key.endswith('.py') else 0o644)
        atomic_write(Path(targets[key]), source_file(key, source).read_bytes(), mode)

def settings_fingerprints():
    paths = [Path('/etc/abl-bridge.json'), Path('/etc/comitup.conf'), Path('/var/lib/abl-bridge/ports.json')]
    paths += sorted(Path('/etc/NetworkManager/system-connections').glob('*'))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}

def status(port):
    with urlopen(f'http://127.0.0.1:{port}/status.json', timeout=3) as response:
        return json.load(response)

def preflight(*, standalone=False, controller_port=None):
    validate_sources()
    for module in ('dbus', 'flask', 'jinja2', 'comitup_web'):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(f'Missing installed dependency: {module}')
    from jinja2 import Environment, FileSystemLoader
    environment = Environment(loader=FileSystemLoader(SOURCE / 'ui/portal'), autoescape=True)
    for name in ('index.html', 'confirm.html', 'connect.html'):
        environment.get_template(name).render(points=[], ssid='Validation', ssid_encoded='Validation',
                                              encrypted='encrypted', mode='single', can_blink=False)
    for target in ('/usr/local/sbin/abl-bridge', '/usr/local/sbin/evcc-comitup-web'):
        if not Path(target).is_file():
            raise RuntimeError('This update requires an already installed ABL bridge')
    for service in ('abl-bridge', 'comitup-web'):
        command('systemctl', 'cat', service)
    config = json.loads(CONFIG.read_text())
    port = int(config['status_port'])
    before = status(port)
    if before.get('reboot_pending'):
        raise RuntimeError('Wait until the pending board reboot has finished')
    active = [service for service in ('abl-bridge', 'comitup-web')
              if subprocess.run(['systemctl', 'is-active', '--quiet', service], timeout=10).returncode == 0]
    if 'abl-bridge' not in active:
        raise RuntimeError('The existing bridge service must be running before updating')
    if standalone and not config.get('controller', {}).get('enabled'):
        bus_port = int(controller_port or config['base_port'])
        if not any(row['tcp_port'] == bus_port for row in before['adapters']):
            raise RuntimeError('Select an existing USB adapter TCP port for the standalone controller')
        if command('ss', '-Hnt', 'state', 'established', f'( sport = :{bus_port} )').strip():
            raise RuntimeError('Disconnect HA and COM software from the serial bridge before migration')
    return port, before, active

def check_health(port, before):
    expected = {row['adapter']: row['tcp_port'] for row in before['adapters']}
    last_error = None
    for _ in range(15):
        try:
            after = status(port)
            actual = {row['adapter']: row['tcp_port'] for row in after['adapters']}
            if after['boot_id'] != before['boot_id'] or after.get('reboot_pending'):
                raise RuntimeError('Unexpected board reboot')
            if after.get('service') == 'starting' or actual != expected:
                raise RuntimeError('USB port assignment is not ready')
            for name in ('abl-design.css', 'icon.png'):
                with urlopen(f'http://127.0.0.1:{port}/assets/{name}', timeout=3) as response:
                    if response.read() != (SOURCE / 'ui' / name).read_bytes():
                        raise RuntimeError(f'Incorrect served asset: {name}')
            with urlopen(f'http://127.0.0.1:{port}/', timeout=3) as response:
                page = response.read().decode('utf-8')
                config = json.loads(CONFIG.read_text())
                autonomous = config.get('controller', {}).get('enabled')
                if autonomous:
                    if '<h1>WallboxPilot</h1>' not in page:
                        raise RuntimeError('Standalone controller page not loaded')
                    key = Path('/var/lib/abl-bridge/controller/api-key').read_text().strip()
                    from urllib.request import Request
                    request = Request(f'http://127.0.0.1:{port}/api/v1/state', headers={'Authorization': 'Bearer ' + key})
                    with urlopen(request, timeout=3) as state_response:
                        state = json.load(state_response)
                    if state.get('api_version') != 1 or not state.get('available'):
                        raise RuntimeError('Standalone controller cannot read the wallbox')
                elif '<h1>WallboxPilot</h1>' not in page or 'id="status-button"' not in page:
                    raise RuntimeError('New status page not loaded')
            return
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError(f'Bridge health check failed: {last_error}')

def standalone_config(config, *, address, controller_port=None):
    if type(address) is not int or not 1 <= address <= 16:
        raise ValueError('Controller address must be an integer from 1 to 16')
    config = json.loads(json.dumps(config))
    if not config.get('controller', {}).get('enabled'):
        config['controller'] = {**config.get('controller', {}), 'enabled': True, 'address': address, 'tcp_port': int(controller_port or config['base_port'])}
    config['listen_host'] = '127.0.0.1'
    return config

def install(*, standalone=False, address=1, controller_port=None):
    port, before, active = preflight(standalone=standalone, controller_port=controller_port)
    fingerprints = settings_fingerprints()
    backup = BACKUPS / ('update-' + VERSION + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    rollback_targets = {**TARGETS, 'previous-config.json': str(CONFIG)}
    records = snapshot(rollback_targets, backup)
    expected_fingerprints = dict(fingerprints)
    try:
        write_update(SOURCE, TARGETS, records)
        if standalone:
            config = standalone_config(json.loads(CONFIG.read_text()), address=address, controller_port=controller_port)
            encoded_config = (json.dumps(config, indent=2) + '\n').encode()
            expected_fingerprints[str(CONFIG)] = hashlib.sha256(encoded_config).hexdigest()
            atomic_write(CONFIG, encoded_config, records['previous-config.json']['mode'])
        command('systemctl', 'daemon-reload')
        for service in active:
            command('systemctl', 'restart', service)
        check_health(port, before)
        after_fingerprints = settings_fingerprints()
        if after_fingerprints != expected_fingerprints:
            raise RuntimeError('Settings changed during update; review the preserved backup')
    except BaseException:
        restore(rollback_targets, backup, records)
        command('systemctl', 'daemon-reload')
        for service in active:
            command('systemctl', 'restart', service)
        raise
    return {'version': VERSION, 'backup': str(backup), 'services_restarted': active,
            'settings_preserved': True, 'standalone': bool(json.loads(CONFIG.read_text()).get('controller', {}).get('enabled')),
            'usb_ports_preserved': True, 'board_rebooted': False}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Update an existing WallboxPilot board')
    parser.add_argument('--check', action='store_true', help='Validate without writing or restarting services')
    parser.add_argument('--standalone', action='store_true', help='Enable standalone controller; first release HA/COM TCP connection')
    parser.add_argument('--address', type=int, default=1, help='Existing controller bus address; does not write a new address')
    parser.add_argument('--controller-port', type=int, help='Existing USB adapter TCP port, defaults to base_port')
    args = parser.parse_args()
    if os.name != 'posix' or os.geteuid() != 0:
        raise SystemExit('Run with the board system Python as root: sudo /usr/bin/python3 install-update.py')
    if args.check:
        preflight(standalone=args.standalone, controller_port=args.controller_port)
        print('ABL_UPDATE_CHECK_OK')
    else:
        print(json.dumps(install(standalone=args.standalone, address=args.address, controller_port=args.controller_port), indent=2))
