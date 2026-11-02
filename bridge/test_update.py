"""Exercise installer rollback using only a temporary directory, never system paths."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

spec = spec_from_file_location('installer', Path(__file__).with_name('install-update.py'))
installer = module_from_spec(spec)
spec.loader.exec_module(installer)

class UpdateTests(unittest.TestCase):
    def test_standalone_migration_preserves_original_settings_and_is_opt_in(self):
        original = {'base_port': 5020, 'status_port': 8080, 'baudrate': 38400, 'comset': '8E1', 'other': {'preserve': True}}
        actual = installer.standalone_config(original, address=2)
        self.assertNotIn('controller', original)
        self.assertEqual(actual['controller'], {'enabled': True, 'address': 2, 'tcp_port': 5020})
        self.assertEqual(actual['listen_host'], '127.0.0.1')
        self.assertEqual(actual['other'], original['other'])
        self.assertEqual(actual['baudrate'], original['baudrate'])
        with self.assertRaises(ValueError):
            installer.standalone_config(original, address=True)

    def test_all_payload_files_are_present_and_parse(self):
        installer.validate_sources()

    def test_snapshot_update_and_rollback_restore_existing_and_remove_new_files(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            targets = {'abl-bridge.py': str(base / 'installed/program'), 'ui/abl-design.css': str(base / 'installed/style.css')}
            original = Path(targets['abl-bridge.py']); original.parent.mkdir(); original.write_bytes(b'original')
            records = installer.snapshot(targets, base / 'backup')
            installer.write_update(installer.SOURCE, targets, records)
            self.assertNotEqual(original.read_bytes(), b'original')
            self.assertTrue(Path(targets['ui/abl-design.css']).exists())
            installer.restore(targets, base / 'backup', records)
            self.assertEqual(original.read_bytes(), b'original')
            self.assertFalse(Path(targets['ui/abl-design.css']).exists())

    def test_partial_write_failure_can_be_rolled_back(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            targets = {'abl-bridge.py': str(base / 'program'), 'ui/abl-design.css': str(base / 'style.css')}
            Path(targets['abl-bridge.py']).write_bytes(b'original')
            records = installer.snapshot(targets, base / 'backup')
            original_write = installer.atomic_write
            def fail_program(path, data, mode):
                if str(path) == targets['abl-bridge.py']:
                    raise OSError('Simulated interrupted update')
                original_write(path, data, mode)
            with patch.object(installer, 'atomic_write', side_effect=fail_program):
                with self.assertRaises(OSError):
                    installer.write_update(installer.SOURCE, targets, records)
            installer.restore(targets, base / 'backup', records)
            self.assertEqual(Path(targets['abl-bridge.py']).read_bytes(), b'original')
            self.assertFalse(Path(targets['ui/abl-design.css']).exists())

if __name__ == '__main__': unittest.main(verbosity=2)
