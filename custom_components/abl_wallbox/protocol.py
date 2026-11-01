"""ABL EVCC2/3 ASCII framing and controller API, independent of Home Assistant.

The leading byte of read data is the requested register, not a measurement.
Only explicitly requested setters send writes. Connecting and polling are read-only.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import math
import struct
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)
TIMEOUT = 1.2


class ABLError(Exception):
    """Base communication or controller error."""


class ProtocolError(ABLError):
    """Invalid or mismatched ASCII message."""


class ControllerBusy(ABLError):
    """The controller cannot safely enter configuration mode."""


class DeviceRejected(ABLError):
    """A valid exception response was received."""


def encode_frame(payload: bytes) -> bytes:
    return b":" + (payload + bytes([(-sum(payload)) & 255])).hex().upper().encode() + b"\r\n"


def decode_frame(frame: bytes) -> bytes:
    frame = frame.strip()
    if len(frame) < 9 or len(frame) > 520 or frame[:1] not in (b">", b":"):
        raise ProtocolError("Invalid ABL frame prefix or length")
    try:
        raw = bytes.fromhex(frame[1:].decode("ascii"))
    except (ValueError, UnicodeError) as err:
        raise ProtocolError("Non-hexadecimal ABL frame") from err
    if sum(raw) & 255:
        raise ProtocolError("Invalid LRC checksum")
    return raw[:-1]


def read_request(address: int, register: int, count: int) -> bytes:
    if not 1 <= address <= 16 or not 0 <= register <= 65535 or not 1 <= count <= 64:
        raise ValueError("Invalid read request")
    return encode_frame(struct.pack(">BBHH", address, 3, register, count))


def write_request(address: int, register: int, value: int) -> bytes:
    if not 1 <= address <= 16 or not 0 <= register <= 65535 or not 0 <= value <= 65535:
        raise ValueError("Invalid write request")
    return encode_frame(struct.pack(">BBHHBH", address, 16, register, 1, 2, value))


def amps_to_pwm(amps: float) -> int:
    if not math.isfinite(amps) or not 6 <= amps <= 80:
        raise ValueError("Charging current must be between 6 and 80 A")
    duty = amps / 0.6 if amps <= 51 else amps / 2.5 + 64
    return int(duty * 10 + 0.5)


def pwm_to_amps(pwm: int) -> float | None:
    if pwm == 1000:
        return 0.0  # 100% means charging paused, never 60 A.
    if 100 <= pwm <= 850:
        return round(pwm * 0.06, 2)
    if 850 < pwm <= 960:
        return round(pwm * 0.25 - 160, 2)
    return None


def integer_amps(pwm: int) -> int:
    value = pwm_to_amps(pwm)
    if value is None or value == 0:
        raise ProtocolError("Controller returned an invalid current limit")
    return int(value + 0.5)


def state_name(code: int) -> str:
    return f"{code >> 4:X}{code & 15}"


@dataclass(frozen=True)
class Version:
    address: int
    firmware: str
    pcb: str
    major: int
    minor: int
    features: int

    @property
    def new_api(self) -> bool:
        return (self.pcb == "160307" and (self.major, self.minor) >= (4, 1)) or (
            self.pcb == "170725" and (self.major, self.minor) >= (2, 1)
        )

    @classmethod
    def parse(cls, data: bytes) -> Version:
        if len(data) != 4 or data[0] != 1:
            raise ProtocolError("Invalid firmware record")
        pcb = ("141215", "160307", "170725", "unknown")[data[1] >> 6]
        major, minor = data[2] >> 4, data[2] & 15
        return cls(data[1] & 63, f"{major}.{minor}", pcb, major, minor, data[3])


@dataclass(frozen=True)
class Limits:
    rated: int
    installation: int
    requested: int
    effective: float | None
    imbalance_enabled: bool
    imbalance_detected: bool
    imbalance_limit: int
    raw_pwm: tuple[int, ...]

    @classmethod
    def parse(cls, data: bytes) -> Limits:
        if len(data) != 10 or data[0] != 15:
            raise ProtocolError("Invalid current limits record")
        flags = data[1]
        pwm = struct.unpack(">4H", data[2:])
        requested = pwm_to_amps(pwm[2])
        if requested is None:
            raise ProtocolError("Invalid requested current")
        return cls(
            integer_amps(pwm[0]),
            integer_amps(pwm[1]),
            int(requested + 0.5),
            pwm_to_amps(pwm[3]),
            bool(flags & 128),
            bool(flags & 64),
            flags & 63,
            pwm,
        )


class Bus:
    """One serialized transport shared by all controller addresses on an endpoint."""

    def __init__(
        self,
        connection: str,
        host: str = "",
        port: int = 5020,
        device: str = "",
        baudrate: int = 38400,
        timeout: float = TIMEOUT,
    ):
        self.connection = connection
        self.host, self.port, self.device = host, port, device
        self.baudrate, self.timeout = baudrate, timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._serial: Any = None
        self._lock = asyncio.Lock()
        self.last_frames: dict[str, str] = {}

    async def _open(self) -> None:
        if self.connection == "tcp":
            if self._writer is None or self._writer.is_closing():
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port, limit=2048), self.timeout
                )
        elif self._serial is None or not self._serial.is_open:
            import serial  # optional until a serial connection is actually selected

            self._serial = await asyncio.to_thread(
                serial.serial_for_url,
                self.device,
                baudrate=self.baudrate,
                bytesize=8,
                parity="E",
                stopbits=1,
                timeout=self.timeout,
                write_timeout=self.timeout,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )

    async def close(self) -> None:
        if self._writer is not None:
            writer, self._writer, self._reader = self._writer, None, None
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, asyncio.TimeoutError):
                pass
        if self._serial is not None:
            serial_port, self._serial = self._serial, None
            await asyncio.to_thread(serial_port.close)

    async def _send(self, request: bytes) -> None:
        if self.connection == "tcp":
            assert self._writer is not None
            self._writer.write(request)
            await self._writer.drain()
        else:
            await asyncio.to_thread(self._serial.write, request)

    async def _line(self, timeout: float) -> bytes:
        if self.connection == "tcp":
            assert self._reader is not None
            return await asyncio.wait_for(self._reader.readline(), timeout)
        return await asyncio.to_thread(self._serial.readline)

    async def _exchange(
        self, address: int, register: int, *, count: int | None = None, value: int | None = None, attempts: int = 3
    ) -> bytes:
        reading = count is not None
        request = read_request(address, register, count) if reading else write_request(address, register, value)
        async with self._lock:
            for attempt in range(attempts if reading else 1):
                try:
                    await self._open()
                    await self._send(request)
                    deadline = time.monotonic() + self.timeout
                    while (remaining := deadline - time.monotonic()) > 0:
                        frame = await self._line(remaining)
                        if not frame:
                            raise ConnectionError("Connection closed or serial read timed out")
                        if b"Port already in use" in frame:
                            raise ControllerBusy("The serial bridge is already used by another program")
                        if frame.strip() == request.strip():
                            continue  # local echo is not a reply
                        try:
                            reply = decode_frame(frame)
                        except ProtocolError:
                            continue
                        if reply[0] != address:
                            continue
                        if reply[1] in (0x83, 0x90):
                            raise DeviceRejected(f"Controller rejected register {register:04X}: {reply.hex()}")
                        if reading:
                            if (
                                len(reply) < 4
                                or reply[1] != 3
                                or reply[2] != count * 2
                                or len(reply) != 3 + count * 2
                                or reply[3] != register & 255
                            ):
                                continue
                            result = reply[3:]
                        else:
                            if reply[1:] != struct.pack(">BHH", 16, register, 1):
                                continue
                            result = reply[2:]
                        self.last_frames[f"{address}:{register:04X}"] = frame.decode("ascii").strip()
                        return result
                    raise asyncio.TimeoutError()
                except (ControllerBusy, DeviceRejected):
                    raise
                except (OSError, ConnectionError, asyncio.TimeoutError, ValueError) as err:
                    if attempt + 1 == (attempts if reading else 1):
                        await self.close()
                        raise ABLError(f"No valid reply from address {address}, register {register:04X}") from err
                    # Keep the connection: the first request often only wakes the EVCC.
            raise ABLError("No controller reply")

    async def read(self, address: int, register: int, count: int, *, attempts: int = 3) -> bytes:
        return await self._exchange(address, register, count=count, attempts=attempts)

    async def write(self, address: int, register: int, value: int) -> bytes:
        return await self._exchange(address, register, value=value)


class Controller:
    """Controller settings with read-back verification and explicit write operations."""

    def __init__(self, bus: Bus, address: int = 1):
        self.bus, self.address = bus, address
        self._lock = asyncio.Lock()
        self.version: Version | None = None
        self.limits: Limits | None = None
        self.counters: dict[str, int | None] = {}

    async def _state(self) -> int:
        return (await self.bus.read(self.address, 4, 1))[1]

    async def _limits(self) -> Limits:
        self.limits = Limits.parse(await self.bus.read(self.address, 15, 5))
        return self.limits

    async def refresh(self, *, counters: bool = False) -> dict[str, Any]:
        async with self._lock:
            self.version = Version.parse(await self.bus.read(self.address, 1, 2))
            limits = await self._limits()
            flags_data = await self.bus.read(self.address, 6, 2)
            flags = int.from_bytes(flags_data[2:], "big")
            data = await self.bus.read(self.address, 46, 5)
            status = data[1]
            inputs = int.from_bytes(data[2:4], "big")
            phase_values = struct.unpack(">3H", data[4:])
            currents = tuple(None if value == 1000 else value / 10 for value in phase_values)
            upstream = bool(self.version.features & 64)
            on_boot, fallback = not bool(flags & 0x4000), not bool(flags & 0x8000)
            mode = (
                "standalone"
                if not upstream and fallback and on_boot
                else (
                    "rfid"
                    if upstream and fallback and not on_boot
                    else ("sbc" if upstream and not fallback and not on_boot else "custom")
                )
            )
            if counters:
                for index in range(17):
                    try:
                        raw = await self.bus.read(self.address, 26 + index, 1, attempts=1)
                        self.counters[f"F{index + 1}"] = raw[1]
                    except ABLError:
                        self.counters[f"F{index + 1}"] = None
            return {
                "firmware": self.version.firmware,
                "hardware": f"PCBA {self.version.pcb}",
                "new_api": self.version.new_api,
                "address": self.address,
                "mode": mode,
                "rated_current": limits.rated,
                "installation_limit": limits.installation,
                "requested_current": limits.requested,
                "effective_current": limits.effective,
                "imbalance_enabled": limits.imbalance_enabled,
                "imbalance_limit": limits.imbalance_limit,
                "imbalance_detected": limits.imbalance_detected,
                "state": state_name(status),
                "state_raw": status,
                "enabled": status >> 4 in (10, 11, 12),
                "charging": status >> 4 == 12,
                "vehicle_connected": bool(inputs & 0x8000),
                "en1_closed": bool(inputs & 0x1000),
                "en2_closed": bool(inputs & 0x2000),
                "duty_cycle": (inputs & 0x0FFF) / 10,
                "current_l1": currents[0],
                "current_l2": currents[1],
                "current_l3": currents[2],
                "estimated_power": round(sum(currents) * 230) if all(v is not None for v in currents) else None,
                "fallback_enabled": fallback,
                "start_on_boot": on_boot,
                "upstream_timeout": upstream,
                "overtemperature": bool(flags & 128),
                "meter_fault": bool(flags & 64),
                "upstream_lost": bool(flags & 512),
                "system_flags": f"0x{flags:04X}",
                "error_counters": dict(self.counters),
            }

    async def set_requested_current(self, amps: int) -> None:
        async with self._lock:
            limit = await self._limits()
            if amps != 0 and not 6 <= amps <= min(limit.rated, limit.installation):
                raise ValueError(f"Current must be 0 or 6..{min(limit.rated, limit.installation)} A")
            pwm = 1000 if amps == 0 else amps_to_pwm(amps)
            await self.bus.write(self.address, 20, pwm)
            actual = await self._limits()
            if actual.requested != amps:
                raise ABLError("Controller did not confirm the requested charging current")

    async def set_enabled(self, enabled: bool) -> None:
        async with self._lock:
            current = await self._state()
            if (enabled and current >> 4 in (10, 11, 12)) or (not enabled and current == 0xE0):
                return
            await self.bus.write(self.address, 5, 0xA1A1 if enabled else 0xE0E0)
            state = await self._state()
            if (state >> 4 in (10, 11, 12)) != enabled:
                raise ABLError("Controller did not confirm the charging enable state")

    async def configure(self, **settings: Any) -> None:
        """Apply the same setting groups as the ABL tool, preserving the prior enable state."""
        async with self._lock:
            old_state = await self._state()
            if old_state not in (0xA1, 0xE0, 0xE2):
                raise ControllerBusy("Disconnect the vehicle before changing controller settings")
            self.version = Version.parse(await self.bus.read(self.address, 1, 2))
            limits = await self._limits()
            new_address = int(settings.get("address", self.address))
            if not 1 <= new_address <= 16:
                raise ValueError("Bus address must be 1..16")
            if "installation_limit" in settings and not 6 <= settings["installation_limit"] <= limits.rated:
                raise ValueError(f"Installation limit must be 6..{limits.rated} A")
            if settings.get("mode", "standalone") not in ("standalone", "rfid", "sbc"):
                raise ValueError("Invalid operating mode")
            if settings.get("imbalance", "off") not in ("off", "16", "20"):
                raise ValueError("Invalid phase imbalance threshold")
            if settings.get("imbalance") == "16" and not self.version.new_api:
                raise ValueError("This firmware only supports the 20 A phase imbalance threshold")
            if new_address != self.address:
                try:
                    await self.bus.read(new_address, 1, 2, attempts=1)
                except ControllerBusy:
                    raise
                except ABLError:
                    pass
                else:
                    raise ValueError("The requested bus address is already occupied")
            entered = False
            try:
                await self.bus.write(self.address, 5, 0xE2E2)
                entered = True
                if await self._state() != 0xE2:
                    raise ControllerBusy("Controller did not enter setup mode E2")
                if "installation_limit" in settings:
                    await self.bus.write(self.address, 45, amps_to_pwm(settings["installation_limit"]))
                    if (await self._limits()).installation != settings["installation_limit"]:
                        raise ABLError("Installation current limit was not confirmed")
                if "imbalance" in settings:
                    threshold = settings["imbalance"]
                    if self.version.new_api:
                        await self.bus.write(
                            self.address, 45, 0x8000 | amps_to_pwm(63 if threshold == "off" else int(threshold))
                        )
                    else:
                        await self.bus.write(self.address, 5, 0x3030 if threshold == "off" else 0x3838)
                    actual = await self._limits()
                    if actual.imbalance_enabled != (threshold != "off") or (
                        threshold != "off" and actual.imbalance_limit != int(threshold)
                    ):
                        raise ABLError("Phase imbalance setting was not confirmed")
                if "mode" in settings:
                    mode = settings["mode"]
                    if mode == "sbc":
                        await self.bus.write(self.address, 5, 0x4141)
                    elif not self.version.new_api:
                        await self.bus.write(self.address, 5, 0x4040)
                    control = (0x4000 | 0x1000) if mode == "standalone" else (0x8000 | 0x2000)
                    await self.bus.write(self.address, 44, control | self.address)
                    if mode == "rfid":
                        # RFID needs the upstream watchdog but must not use the SBC/F4 fault policy.
                        await self.bus.write(self.address, 5, 0x4040)
                if "fallback_enabled" in settings:
                    if self.version.new_api and settings["fallback_enabled"]:
                        await self.bus.write(self.address, 44, self.address | 0x4000)
                    else:
                        await self.bus.write(self.address, 5, 0x4040 if settings["fallback_enabled"] else 0x4141)
                if "start_on_boot" in settings:
                    await self.bus.write(
                        self.address, 44, self.address | (0x1000 if settings["start_on_boot"] else 0x2000)
                    )
                if new_address != self.address:
                    old_address = self.address
                    try:
                        await self.bus.write(old_address, 44, new_address)
                    except ABLError:
                        # A changed address may already be active even when the old-address ACK is lost.
                        try:
                            moved = Version.parse(await self.bus.read(new_address, 1, 2))
                        except ABLError:
                            raise
                        if (moved.pcb, moved.firmware) != (self.version.pcb, self.version.firmware):
                            raise ABLError("Could not confirm the controller at its new address")
                    self.address = new_address
                    moved = Version.parse(await self.bus.read(self.address, 1, 2))
                    if moved.address != self.address:
                        raise ABLError("New bus address was not confirmed")
            finally:
                if entered:
                    restore = 0xE0E0 if old_state == 0xE0 else (0xE2E2 if old_state == 0xE2 else 0xA1A1)
                    await self.bus.write(self.address, 5, restore)
                    if await self._state() != old_state:
                        raise ABLError("Controller enable state could not be restored after configuration")
            # Verify mode/fallback settings after leaving setup mode.
            self.version = Version.parse(await self.bus.read(self.address, 1, 2))
            flags = int.from_bytes((await self.bus.read(self.address, 6, 2))[2:], "big")
            upstream, f4, on_boot = bool(self.version.features & 64), bool(flags & 0x8000), not bool(flags & 0x4000)
            if "mode" in settings:
                expected = {
                    "standalone": (False, False, True),
                    "rfid": (True, False, False),
                    "sbc": (True, True, False),
                }[settings["mode"]]
                if (upstream, f4, on_boot) != expected:
                    raise ABLError("Operating mode was not confirmed")
            if "fallback_enabled" in settings and (not f4) != settings["fallback_enabled"]:
                raise ABLError("Fallback setting was not confirmed")
            if "start_on_boot" in settings and on_boot != settings["start_on_boot"]:
                raise ABLError("Power-on enable setting was not confirmed")

    async def restart(self) -> None:
        async with self._lock:
            state = await self._state()
            if state >> 4 in (11, 12):
                raise ControllerBusy("Disconnect the vehicle before restarting the controller")
            await self.bus.write(self.address, 5, 0x5A5A)

    async def clear_fault_counters(self, *, confirm: bool = False) -> None:
        if not confirm:
            raise ValueError("Explicit confirmation is required to erase controller fault counters")
        async with self._lock:
            if await self._state() not in (0xA1, 0xE0, 0xE2):
                raise ControllerBusy("Disconnect the vehicle before clearing fault counters")
            await self.bus.write(self.address, 43, 0x00A5)
            self.counters.clear()

    async def scan(self) -> list[dict[str, Any]]:
        found = []
        for address in range(1, 17):
            try:
                version = Version.parse(await self.bus.read(address, 1, 2, attempts=2))
                found.append({"address": address, "firmware": version.firmware, "hardware": version.pcb})
            except ControllerBusy:
                raise
            except ABLError:
                continue
        return found
