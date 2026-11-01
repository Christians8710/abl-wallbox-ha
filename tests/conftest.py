"""Load the protocol layer without importing Home Assistant on the test host."""

import importlib.util
from pathlib import Path
import sys

import pytest

path = Path(__file__).parents[1] / "custom_components/abl_wallbox/protocol.py"
spec = importlib.util.spec_from_file_location("abl_protocol", path)
protocol = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = protocol
spec.loader.exec_module(protocol)


@pytest.fixture
def api():
    return protocol
