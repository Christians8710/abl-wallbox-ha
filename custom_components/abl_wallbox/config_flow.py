"""Connection-specific UI setup; initial probes never change the controller."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import bus_key, make_bus, make_controller
from .api_client import APIAuthenticationError
from .const import DEFAULT_INTERVAL, DEFAULT_PORT, DOMAIN
from .protocol import ABLError, ControllerBusy

CONNECTIONS = {
    "api": "WallboxPilot / lokale API",
    "tcp": "TCP / Netzwerkbrücke",
    "serial": "USB / seriell / RFC2217",
    "shelly": "Shelly Pro RS485 (UART-Skript)",
}


def endpoint_id(data):
    if data["connection_type"] == "api":
        return ":".join(str(part) for part in ("api", data["host"].strip().lower(), data["port"], data["address"]))
    return ":".join(str(part) for part in (*bus_key(data), data["address"]))


def schema(connection, data=None):
    data = data or {}
    fields = {vol.Required("name", default=data.get("name", "WallboxPilot")): str}
    if connection in ("tcp", "shelly", "api"):
        fields.update(
            {
                vol.Required("host", default=data.get("host", "")): str,
                vol.Required("port", default=data.get("port", 8080 if connection == "api" else 80 if connection == "shelly" else DEFAULT_PORT)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }
        )
    else:
        fields.update(
            {
                vol.Required("device", default=data.get("device", "")): str,
                vol.Required("baudrate", default=data.get("baudrate", 38400)): vol.In([9600, 19200, 38400, 57600]),
            }
        )
    fields[vol.Required("address", default=data.get("address", 1))] = vol.All(vol.Coerce(int), vol.Range(min=1, max=16))
    if connection == "api":
        fields[vol.Required("api_key", default=data.get("api_key", ""))] = selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
    if connection == "shelly":
        fields.update(
            {
                vol.Required("script_id", default=data.get("script_id", 1)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=999)
                ),
                vol.Optional("username", default=data.get("username", "admin")): str,
                vol.Optional("password", default=data.get("password", "")): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
    return vol.Schema(fields)


class ABLConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _connection_type = "api"
    _reconfiguring = False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ABLOptionsFlow()

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._connection_type = user_input["connection_type"]
            return await self.async_step_connection()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("connection_type", default="api"): vol.In(CONNECTIONS)}),
        )

    async def _probe(self, data):
        if data["connection_type"] in ("tcp", "shelly", "api"):
            data["host"] = data["host"].strip()
            if not data["host"] or "://" in data["host"] or any(character in data["host"] for character in '/?#@ \r\n\t'):
                raise ValueError("Enter the hostname or IP without http://")
            if data['connection_type'] == 'api':
                data['api_key'] = data['api_key'].strip()
        else:
            data["device"] = data["device"].strip()
            if not data["device"]:
                raise ValueError("Enter the serial device path or RFC2217 URL")
        pool = self.hass.data.get(DOMAIN, {}).get(bus_key(data))
        bus = pool["bus"] if pool else make_bus(data)
        try:
            controller = make_controller(bus, data["address"])
            await controller.refresh()
            if data["connection_type"] == "api":
                data["controller_id"] = controller.controller_id
        finally:
            if not pool:
                await bus.close()

    async def async_step_connection(self, user_input=None):
        errors = {}
        current = self._get_reconfigure_entry() if self._reconfiguring else None
        defaults = dict(current.data) if current else {}
        if current and self._connection_type != current.data["connection_type"]:
            defaults.pop("port", None)
        if user_input is not None:
            data = {**user_input, "connection_type": self._connection_type}
            try:
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    if (not current or entry.entry_id != current.entry_id) and endpoint_id(entry.data) == endpoint_id(
                        data
                    ):
                        return self.async_abort(reason="already_configured")
                if not current:
                    await self.async_set_unique_id(endpoint_id(data))
                    self._abort_if_unique_id_configured()
                await self._probe(data)
            except APIAuthenticationError:
                errors["base"] = "invalid_auth"
            except ControllerBusy:
                errors["base"] = "bridge_busy"
            except (ABLError, OSError):
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_connection"
            else:
                if current:
                    self.hass.config_entries.async_update_entry(current, title=data["name"])
                    return self.async_update_reload_and_abort(current, data_updates=data)
                return self.async_create_entry(title=data["name"], data=data)
        return self.async_show_form(
            step_id="connection", data_schema=schema(self._connection_type, user_input or defaults), errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        self._reconfiguring = True
        if user_input is not None:
            self._connection_type = user_input["connection_type"]
            return await self.async_step_connection()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required("connection_type", default=entry.data["connection_type"]): vol.In(CONNECTIONS)}
            ),
        )


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
