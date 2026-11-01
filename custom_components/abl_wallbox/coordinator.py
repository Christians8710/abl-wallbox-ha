"""Polling, controller configuration, and persistent observations of faults."""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DEFAULT_INTERVAL, DOMAIN, STATE_LABELS
from .protocol import ABLError, Controller

_LOGGER = logging.getLogger(__name__)


class ABLCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, controller: Controller) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            update_interval=timedelta(seconds=entry.options.get("scan_interval", DEFAULT_INTERVAL)),
            config_entry=entry,
        )
        self.entry, self.controller = entry, controller
        self.initial_options = dict(entry.options)
        self.history: list[dict] = []
        self._previous_counters: dict[str, int] = {}
        self._previous_state: str | None = None
        self._counter_time: float | None = None
        self._store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.fault_history")

    async def async_initialize(self) -> None:
        saved = await self._store.async_load()
        if saved:
            self.history = saved.get("history", [])[-100:]
            self._previous_counters = saved.get("counters", {})

    def _storage_data(self) -> dict:
        return {"history": self.history, "counters": self._previous_counters}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            with_counters = (
                self._counter_time is None
                or time.monotonic() - self._counter_time >= self.entry.options.get("counter_interval", 300)
            )
            data = await self.controller.refresh(counters=with_counters)
            if with_counters:
                self._counter_time = time.monotonic()
        except (ABLError, OSError, ValueError) as err:
            raise UpdateFailed(str(err)) from err
        state = data["state"]
        data["state_description"] = STATE_LABELS.get(state, state)
        data["fault"] = state.startswith("F") or any(
            data[k] for k in ("imbalance_detected", "overtemperature", "meter_fault", "upstream_lost")
        )
        changed = False
        now = dt_util.utcnow().isoformat()
        if state.startswith("F") and state != self._previous_state:
            self.history.append(
                {"time": now, "type": "observed_fault", "code": state, "description": data["state_description"]}
            )
            changed = True
        self._previous_state = state
        for code, value in data["error_counters"].items():
            if value is None:
                continue
            previous = self._previous_counters.get(code)
            if previous is not None and previous != value:
                self.history.append(
                    {
                        "time": now,
                        "type": "counter_changed",
                        "code": code,
                        "previous": previous,
                        "count": value,
                        "description": STATE_LABELS.get(code, code),
                    }
                )
                changed = True
            self._previous_counters[code] = value
        self.history = self.history[-100:]
        if changed or with_counters:
            self._store.async_delay_save(self._storage_data, 2)
        data["last_fault"] = self.history[-1]["code"] if self.history else "none"
        data["fault_history"] = list(self.history)
        return data

    async def async_command(self, command) -> None:
        try:
            await command
        except (ABLError, ValueError, OSError) as err:
            raise HomeAssistantError(str(err)) from err
        await self.async_refresh()

    async def async_configure(self, **settings: Any) -> None:
        new_address = settings.get("address", self.controller.address)
        if new_address != self.controller.address:
            for other in self.hass.config_entries.async_entries(DOMAIN):
                if other.entry_id == self.entry.entry_id:
                    continue
                if (
                    other.data.get("connection_type"),
                    other.data.get("host"),
                    other.data.get("port"),
                    other.data.get("device"),
                    other.data.get("address"),
                ) == (
                    self.entry.data.get("connection_type"),
                    self.entry.data.get("host"),
                    self.entry.data.get("port"),
                    self.entry.data.get("device"),
                    new_address,
                ):
                    raise HomeAssistantError("Another ABL entry already uses this bus address")
        try:
            await self.controller.configure(**settings)
        except (ABLError, ValueError, OSError) as err:
            raise HomeAssistantError(str(err)) from err
        finally:
            if self.entry.data["address"] != self.controller.address:
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, "address": self.controller.address}
                )
            await self.async_refresh()

    async def async_refresh_counters(self) -> None:
        self._counter_time = None
        await self.async_refresh()
