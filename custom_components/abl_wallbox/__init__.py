"""WallboxPilot integration for compatible ABL eMH1 EVCC2/3 hardware."""

from __future__ import annotations

import hashlib
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import ABLCoordinator
from .protocol import ABLError, Bus, Controller, ShellyBus
from .api_client import APIConnection, APIController

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def bus_key(data: dict) -> tuple:
    if data["connection_type"] == "api":
        return ("api", data["host"].strip().lower(), data.get("port", 8080),
                hashlib.sha256(data.get("api_key", "").encode()).hexdigest())
    if data["connection_type"] == "tcp":
        return ("tcp", data["host"].strip().lower(), data["port"])
    if data["connection_type"] == "shelly":
        return ("shelly", data["host"].strip().lower(), data.get("port", 80), data.get("script_id", 1))
    return ("serial", data["device"].strip(), data.get("baudrate", 38400))


def make_bus(data: dict) -> Bus:
    if data["connection_type"] == "api":
        return APIConnection(data["host"], data.get("port", 8080), data.get("api_key", ""))
    if data["connection_type"] == "shelly":
        return ShellyBus(
            data["host"],
            port=data.get("port", 80),
            script_id=data.get("script_id", 1),
            username=data.get("username", "admin"),
            password=data.get("password", ""),
        )
    return Bus(
        data["connection_type"],
        host=data.get("host", ""),
        port=data.get("port", 5020),
        device=data.get("device", ""),
        baudrate=data.get("baudrate", 38400),
    )


def make_controller(bus, address, controller_id=None):
    return APIController(bus, address, controller_id=controller_id) if isinstance(bus, APIConnection) else Controller(bus, address)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    pools = hass.data.setdefault(DOMAIN, {})
    key = bus_key(entry.data)
    if key not in pools:
        data = entry.data
        pools[key] = {
            "bus": make_bus(data),
            "users": set(),
        }
    pool = pools[key]
    pool["users"].add(entry.entry_id)
    coordinator = ABLCoordinator(hass, entry, make_controller(pool["bus"], entry.data["address"], entry.data.get("controller_id")))
    coordinator.bus_key = key
    try:
        await coordinator.async_initialize()
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        pool["users"].discard(entry.entry_id)
        if not pool["users"]:
            await pool["bus"].close()
            pools.pop(key, None)
        if isinstance(err, (ABLError, OSError)):
            raise ConfigEntryNotReady(str(err)) from err
        raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def options_updated(hass: HomeAssistant, updated: ConfigEntry) -> None:
        if dict(updated.options) != coordinator.initial_options:
            await hass.config_entries.async_reload(updated.entry_id)

    entry.async_on_unload(entry.add_update_listener(options_updated))
    if not hass.services.has_service(DOMAIN, "configure_controller"):
        _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    pools = hass.data[DOMAIN]
    key = entry.runtime_data.bus_key
    pool = pools.get(key)
    if pool:
        pool["users"].discard(entry.entry_id)
        if not pool["users"]:
            await pool["bus"].close()
            pools.pop(key, None)
    if not pools:
        for service in ("configure_controller", "clear_fault_counters", "scan_bus"):
            hass.services.async_remove(DOMAIN, service)
    return True


def _register_services(hass: HomeAssistant) -> None:
    def coordinator_for(call: ServiceCall) -> ABLCoordinator:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.domain != DOMAIN or not getattr(entry, "runtime_data", None):
            raise HomeAssistantError("Choose a loaded WallboxPilot integration entry")
        return entry.runtime_data

    async def configure(call: ServiceCall) -> None:
        coordinator = coordinator_for(call)
        settings = {k: v for k, v in call.data.items() if k != "config_entry_id"}
        if not settings:
            raise HomeAssistantError("Specify at least one controller setting")
        await coordinator.async_configure(**settings)

    async def clear(call: ServiceCall) -> None:
        coordinator = coordinator_for(call)
        await coordinator.async_command(coordinator.controller.clear_fault_counters(confirm=call.data["confirm"]))
        await coordinator.async_refresh_counters()

    async def scan(call: ServiceCall) -> dict:
        coordinator = coordinator_for(call)
        try:
            return {"controllers": await coordinator.controller.scan()}
        except ABLError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        "configure_controller",
        configure,
        schema=vol.Schema(
            {
                vol.Required("config_entry_id"): cv.string,
                vol.Optional("installation_limit"): vol.All(vol.Coerce(int), vol.Range(min=6, max=80)),
                vol.Optional("address"): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
                vol.Optional("mode"): vol.In(["standalone", "rfid", "sbc"]),
                vol.Optional("imbalance"): vol.In(["off", "16", "20"]),
                vol.Optional("fallback_enabled"): cv.boolean,
                vol.Optional("start_on_boot"): cv.boolean,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "clear_fault_counters",
        clear,
        schema=vol.Schema(
            {
                vol.Required("config_entry_id"): cv.string,
                vol.Required("confirm"): vol.Equal(True),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "scan_bus",
        scan,
        schema=vol.Schema(
            {
                vol.Required("config_entry_id"): cv.string,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
