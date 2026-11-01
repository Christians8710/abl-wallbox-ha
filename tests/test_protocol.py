"""Regression tests based on actual EVCC responses and stateful wire simulations."""

import asyncio
import struct

import pytest

from conftest import protocol as p

FIRMWARE = b">010304014141165F\r\n"
LIMITS = b">01030A0F940215010B0064006464\r\n"
CURRENT = b">01030A2EA1106403E803E803E8EE\r\n"
FLAGS = b">01030406020000F0\r\n"


def response(payload):
    return b">" + p.encode_frame(payload)[1:]


def test_real_firmware_and_hardware_are_not_register_echo():
    version = p.Version.parse(p.decode_frame(FIRMWARE)[3:])
    assert version.firmware == "4.1"
    assert version.pcb == "160307"
    assert version.address == 1
    assert version.new_api


def test_real_32a_rated_and_16a_installation_limit_are_separate():
    limits = p.Limits.parse(p.decode_frame(LIMITS)[3:])
    assert limits.rated == 32
    assert limits.installation == 16
    assert limits.requested == 6
    assert limits.effective == 6
    assert limits.imbalance_enabled
    assert limits.imbalance_limit == 20


@pytest.mark.parametrize("amps", [6, 10, 16, 20, 32, 51, 63, 80])
def test_pwm_roundtrip(amps):
    assert abs(p.pwm_to_amps(p.amps_to_pwm(amps)) - amps) < 0.13


@pytest.mark.parametrize("amps", [0, 5, 81, float("nan"), float("inf")])
def test_invalid_charging_current_is_rejected(amps):
    with pytest.raises(ValueError):
        p.amps_to_pwm(amps)


def test_100_percent_means_pause_not_sixty_amps():
    assert p.pwm_to_amps(1000) == 0


def test_request_matches_vendor_protocol():
    assert p.read_request(1, 15, 5) == b":0103000F0005E8\r\n"
    assert p.write_request(1, 20, 166) == b":0110001400010200A632\r\n"


@pytest.mark.parametrize("frame", [b"garbage", b">010304014141165E", b">GG0304014141165F"])
def test_corrupted_frames_are_rejected(frame):
    with pytest.raises(p.ProtocolError):
        p.decode_frame(frame)


def test_fault_state_low_nibble_is_decimal():
    assert p.state_name(0xFA) == "F10"
    assert p.state_name(0xC2) == "C2"


class SimulatedBus:
    def __init__(self):
        self.address = 1
        self.state = 0xA1
        self.version_features = 0x16
        self.flags = 0
        self.installation = 267
        self.requested = 100
        self.imbalance = 20
        self.writes = []
        self.reject_limit = False
        self.occupied = False
        self.lost_address_ack = False

    async def read(self, address, register, count, **kwargs):
        if address != self.address:
            if self.occupied:
                return bytes([1, 0x40 | address, 0x41, self.version_features])
            raise p.ABLError("no controller")
        if register == 1:
            return bytes([1, 0x40 | self.address, 0x41, self.version_features])
        if register == 4:
            return bytes([4, self.state])
        if register == 6:
            return bytes([6, 2]) + self.flags.to_bytes(2, "big")
        if register == 15:
            return bytes([15, (0x80 | self.imbalance) if self.imbalance < 63 else 63]) + struct.pack(
                ">4H", 533, self.installation, self.requested, self.requested
            )
        if register == 46:
            return bytes([46, self.state]) + struct.pack(">4H", 0x1000 | self.requested, 1000, 1000, 1000)
        if 26 <= register <= 42:
            return bytes([register, 7 if register == 28 else 0])
        raise AssertionError(register)

    async def write(self, address, register, value):
        assert address == self.address
        self.writes.append((register, value))
        if register == 5:
            if value in (0xE2E2, 0xA1A1, 0xE0E0):
                self.state = value & 255
            elif value == 0x4141:
                self.flags |= 0x8000
            elif value == 0x4040:
                self.flags &= ~0x8000
        elif register == 20:
            self.requested = value
        elif register == 45:
            if value & 0x8000:
                self.imbalance = p.integer_amps(value & 0x7FFF)
            elif not self.reject_limit:
                self.installation = value
        elif register == 44:
            new_address = value & 255
            if value & 0xC000 == 0x4000:
                self.version_features &= ~64
                self.flags &= ~0x8000
            elif value & 0xC000 == 0x8000:
                self.version_features |= 64
                self.flags |= 0x8000
            if value & 0x3000 == 0x1000:
                self.flags &= ~0x4000
            elif value & 0x3000 == 0x2000:
                self.flags |= 0x4000
            changed = self.address != new_address
            self.address = new_address
            if changed and self.lost_address_ack:
                raise p.ABLError("ACK lost after address changed")
        return struct.pack(">HH", register, 1)


async def test_polling_is_read_only_and_reads_real_fault_counters():
    bus = SimulatedBus()
    data = await p.Controller(bus).refresh(counters=True)
    assert bus.writes == []
    assert data["rated_current"] == 32
    assert data["installation_limit"] == 16
    assert data["current_l1"] is None
    assert data["estimated_power"] is None
    assert data["error_counters"]["F3"] == 7
    assert data["mode"] == "standalone"


async def test_dynamic_target_cannot_exceed_installation_limit():
    bus = SimulatedBus()
    with pytest.raises(ValueError):
        await p.Controller(bus).set_requested_current(32)
    assert bus.writes == []


async def test_charging_target_and_pause_are_verified():
    bus = SimulatedBus()
    controller = p.Controller(bus)
    await controller.set_requested_current(16)
    assert bus.writes == [(20, 267)]
    await controller.set_requested_current(0)
    assert bus.writes[-1] == (20, 1000)


async def test_configuration_preserves_disabled_outlet():
    bus = SimulatedBus()
    bus.state = 0xE0
    await p.Controller(bus).configure(installation_limit=20)
    assert bus.state == 0xE0
    assert bus.installation == p.amps_to_pwm(20)
    assert bus.writes[0] == (5, 0xE2E2)
    assert bus.writes[-1] == (5, 0xE0E0)


async def test_acknowledged_state_transitions_can_be_delayed():
    class DelayedBus(SimulatedBus):
        pending = None
        reads = 0

        async def write(self, address, register, value):
            if register == 5 and value in (0xE2E2, 0xA1A1, 0xE0E0):
                self.writes.append((register, value))
                self.pending = value & 255
                self.reads = 0
                return struct.pack(">HH", register, 1)
            return await super().write(address, register, value)

        async def read(self, address, register, count, **kwargs):
            if register == 4 and self.pending is not None:
                self.reads += 1
                if self.reads >= 2:
                    self.state = self.pending
                    self.pending = None
            return await super().read(address, register, count, **kwargs)

    bus = DelayedBus()
    await p.Controller(bus).configure(installation_limit=16)
    assert bus.state == 0xA1


async def test_installation_limit_may_not_exceed_hardware_rating():
    bus = SimulatedBus()
    with pytest.raises(ValueError):
        await p.Controller(bus).configure(installation_limit=63)
    assert bus.writes == []


@pytest.mark.parametrize("state", [0xB1, 0xB2, 0xC2])
async def test_configuration_is_rejected_while_vehicle_present(state):
    bus = SimulatedBus()
    bus.state = state
    with pytest.raises(p.ControllerBusy):
        await p.Controller(bus).configure(installation_limit=20)
    assert bus.writes == []


async def test_failed_write_is_not_reported_as_success_and_restores_state():
    bus = SimulatedBus()
    bus.reject_limit = True
    with pytest.raises(p.ABLError, match="not confirmed"):
        await p.Controller(bus).configure(installation_limit=20)
    assert bus.state == 0xA1


async def test_bus_address_change_follows_controller_and_restores_state():
    bus = SimulatedBus()
    controller = p.Controller(bus)
    await controller.configure(address=2)
    assert controller.address == bus.address == 2
    assert bus.state == 0xA1


async def test_lost_address_ack_recovers_at_new_address():
    bus = SimulatedBus()
    bus.lost_address_ack = True
    controller = p.Controller(bus)
    await controller.configure(address=2)
    assert controller.address == 2
    assert bus.state == 0xA1


async def test_address_collision_is_rejected_without_writes():
    bus = SimulatedBus()
    bus.occupied = True
    with pytest.raises(ValueError, match="occupied"):
        await p.Controller(bus).configure(address=2)
    assert bus.writes == []


async def test_phase_imbalance_changes_do_not_change_installation_limit():
    bus = SimulatedBus()
    controller = p.Controller(bus)
    await controller.configure(imbalance="16")
    assert bus.imbalance == 16
    assert bus.installation == 267
    await controller.configure(imbalance="off")
    assert bus.imbalance == 63
    assert bus.installation == 267


@pytest.mark.parametrize("mode", ["standalone", "rfid", "sbc"])
async def test_operating_mode_read_back(mode):
    bus = SimulatedBus()
    controller = p.Controller(bus)
    await controller.configure(mode=mode)
    assert (await controller.refresh())["mode"] == mode


async def test_fault_counters_are_never_erased_without_confirmation():
    bus = SimulatedBus()
    with pytest.raises(ValueError, match="confirmation"):
        await p.Controller(bus).clear_fault_counters()
    assert bus.writes == []


async def test_tcp_client_ignores_echo_wrong_register_corruption_and_wrong_device():
    async def handle(reader, writer):
        request = await reader.readline()
        writer.write(request)
        writer.write(response(bytes([2, 3, 4, 1, 0x42, 0x41, 0x16])))
        writer.write(b">010304014141165E\r\n")
        writer.write(response(bytes([1, 3, 4, 6, 0, 0, 0])))
        writer.write(FIRMWARE[:8])
        await writer.drain()
        writer.write(FIRMWARE[8:])
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    bus = p.Bus("tcp", "127.0.0.1", server.sockets[0].getsockname()[1])
    try:
        assert p.Version.parse(await bus.read(1, 1, 2)).firmware == "4.1"
    finally:
        await bus.close()
        server.close()
        await server.wait_closed()


async def test_first_timeout_wakes_controller_without_reopening_tcp_connection():
    requests = []

    async def handle(reader, writer):
        requests.append(await reader.readline())
        requests.append(await reader.readline())
        writer.write(FIRMWARE)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    bus = p.Bus("tcp", "127.0.0.1", server.sockets[0].getsockname()[1], timeout=0.05)
    try:
        assert p.Version.parse(await bus.read(1, 1, 2)).firmware == "4.1"
        assert len(requests) == 2
    finally:
        await bus.close()
        server.close()
        await server.wait_closed()
