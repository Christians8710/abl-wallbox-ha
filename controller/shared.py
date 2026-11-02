"""Load the same protocol implementation that is shipped with the HA integration."""
import importlib.util
from pathlib import Path
import sys


def load(name, filename):
    local = Path(__file__).with_name(filename)
    source = local if local.exists() else Path(__file__).parents[1] / 'custom_components/abl_wallbox' / filename
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


p = load('abl_board_protocol', 'protocol.py')
STATE_LABELS = load('abl_board_labels', 'const.py').STATE_LABELS
