#!/usr/bin/python3
"""Bounded, private, durable network evidence; never restarts networking or the board."""
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import threading
import time
from datetime import datetime, timezone
from urllib.request import urlopen

DIRECTORY = Path('/var/lib/abl-bridge')


def run(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=6)
        return {'returncode': result.returncode, 'output': result.stdout.strip()[:4000]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'error': type(error).__name__}


def main():
    os.umask(0o077)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    logger = logging.getLogger('abl-network')
    logger.setLevel(logging.INFO)
    output = RotatingFileHandler(DIRECTORY / 'network-diagnostics.jsonl', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
    logger.addHandler(output)

    def record(kind, data):
        logger.info(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'monotonic': round(time.monotonic(), 2),
                                'boot_id': boot, 'kind': kind, 'data': data}, ensure_ascii=False))

    def heartbeat():
        while True:
            data = {'wifi': run(['iw', 'dev', 'wlan0', 'link']), 'power_save': run(['iw', 'dev', 'wlan0', 'get', 'power_save']),
                    'addresses': run(['ip', '-j', 'address', 'show', 'dev', 'wlan0']),
                    'routes': run(['ip', '-j', 'route', 'show', 'default']),
                    'memory_available_kib': next((line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')), None),
                    'temperatures_millidegrees': {path.parent.name: path.read_text().strip() for path in Path('/sys/class/thermal').glob('thermal_zone*/temp')}}
            try:
                for route in json.loads(data['routes'].get('output', '[]')):
                    if route.get('dev') == 'wlan0' and 'gateway' in route:
                        gateway = str(ipaddress.ip_address(route['gateway']))
                        data['gateway_probe'] = run(['ping', '-n', '-c', '1', '-W', '2', '-I', 'wlan0', gateway])
                        break
            except (ValueError, TypeError):
                pass
            try:
                config = json.loads(Path('/etc/abl-bridge.json').read_text())
                with urlopen(f"http://127.0.0.1:{int(config['status_port'])}/health", timeout=3) as response:
                    state = json.load(response)
                data['bridge_service'] = state.get('service')
            except (OSError, ValueError, KeyError):
                data['bridge_service'] = 'unreachable'
            record('heartbeat', data)
            # Unlike the RAM journal, this file is outside /var/log. Persist at least once a minute.
            output.acquire()
            try:
                output.flush()
                os.fsync(output.stream.fileno())
            finally:
                output.release()
            time.sleep(60)

    record('monitor_start', {'interval_seconds': 60, 'maximum_files': 3, 'maximum_bytes_per_file': 2_000_000})
    threading.Thread(target=heartbeat, name='network-heartbeat', daemon=True).start()
    process = subprocess.Popen(['journalctl', '--follow', '--lines=0', '--output=json', '--no-pager',
                                '_TRANSPORT=kernel', '+', '_SYSTEMD_UNIT=NetworkManager.service', '+', '_SYSTEMD_UNIT=comitup.service'],
                               stdout=subprocess.PIPE, text=True)
    try:
        for line in process.stdout:
            try:
                event = json.loads(line)
                record('journal', {key: event.get(key) for key in ('__REALTIME_TIMESTAMP', '__MONOTONIC_TIMESTAMP', '_SYSTEMD_UNIT', '_TRANSPORT', 'PRIORITY', 'MESSAGE')})
            except ValueError:
                continue
        raise RuntimeError('Journal stream stopped')
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == '__main__':
    main()
