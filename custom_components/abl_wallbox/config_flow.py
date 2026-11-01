"""UI setup, connection changes, and polling options."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from . import bus_key
from .const import DEFAULT_INTERVAL, DEFAULT_PORT, DOMAIN
from .protocol import ABLError, Bus, Controller, ControllerBusy


def endpoint_id(data):
    return ":".join(str(part) for part in (*bus_key(data), data["address"]))


def schema(data=None):
    data = data or {}
    return vol.Schema(
        {
            vol.Required("name", default=data.get("name", "ABL Wallbox")): str,
            vol.Required("connection_type", default=data.get("connection_type", "tcp")): vol.In(
                {"tcp": "TCP / Netzwerkbrücke", "serial": "USB / seriell / RFC2217"}
            ),
            vol.Optional("host", default=data.get("host", "")): str,
            vol.Optional("port", default=data.get("port", DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Optional("device", default=data.get("device", "")): str,
            vol.Optional("baudrate", default=data.get("baudrate", 38400)): vol.In([9600, 19200, 38400, 57600]),
            vol.Required("address", default=data.get("address", 1)): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
        }
    )


class ABLConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ABLOptionsFlow()

    async def _probe(self, data):
        if data["connection_type"] == "tcp":
            data["host"] = data.get("host", "").strip()
            if not data["host"] or "://" in data["host"] or "/" in data["host"]:
                raise ValueError("Enter the bridge hostname or IP without an http:// prefix")
        elif not data.get("device", "").strip():
            raise ValueError("Enter the serial device path or RFC2217 URL")
        pool = self.hass.data.get(DOMAIN, {}).get(bus_key(data))
        bus = (
            pool["bus"]
            if pool
            else Bus(
                data["connection_type"],
                host=data.get("host", ""),
                port=data.get("port", DEFAULT_PORT),
                device=data.get("device", ""),
                baudrate=data.get("baudrate", 38400),
            )
        )
        try:
            await Controller(bus, data["address"]).refresh()
        finally:
            if not pool:
                await bus.close()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                unique_id = endpoint_id(user_input)
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    if endpoint_id(entry.data) == unique_id:
                        return self.async_abort(reason="already_configured")
                await self._probe(user_input)
            except ControllerBusy:
                errors["base"] = "bridge_busy"
            except (ABLError, OSError):
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_connection"
            else:
                return self.async_create_entry(title=user_input["name"], data=user_input)
        return self.async_show_form(step_id="user", data_schema=schema(user_input), errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                for other in self.hass.config_entries.async_entries(DOMAIN):
                    if other.entry_id != entry.entry_id and endpoint_id(other.data) == endpoint_id(user_input):
                        return self.async_abort(reason="already_configured")
                await self._probe(user_input)
            except ControllerBusy:
                errors["base"] = "bridge_busy"
            except (ABLError, OSError):
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_connection"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(step_id="reconfigure", data_schema=schema(user_input or entry.data), errors=errors)


class ABLOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("scan_interval", default=options.get("scan_interval", DEFAULT_INTERVAL)): vol.All(
                        vol.Coerce(int), vol.Range(min=10, max=300)
                    ),
                    vol.Required("counter_interval", default=options.get("counter_interval", 300)): vol.All(
                        vol.Coerce(int), vol.Range(min=60, max=3600)
                    ),
                }
            ),
        )
