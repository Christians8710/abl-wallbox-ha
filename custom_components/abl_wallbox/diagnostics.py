"""Downloadable diagnostics without connection addresses or credentials."""

from homeassistant.components.diagnostics import async_redact_data


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "config": async_redact_data(dict(entry.data), {"host", "device", "name"}),
        "options": dict(entry.options),
        "controller": coordinator.data,
        "frames": dict(coordinator.controller.bus.last_frames),
        "history": coordinator.history,
    }
