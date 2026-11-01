"""Outlet enable and persistent controller behaviour."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from .entity import ABLEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            ABLSwitch(entry.runtime_data, key, name)
            for key, name in (
                ("enabled", "Ladefreigabe"),
                ("fallback_enabled", "Fallback bei Steuerungsausfall"),
                ("start_on_boot", "Ladefreigabe nach Neustart"),
            )
        ]
    )


class ABLSwitch(ABLEntity, SwitchEntity):
    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key, name)
        if key != "enabled":
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self):
        return self.value

    async def _set(self, value):
        if self.key == "enabled":
            await self.coordinator.async_command(self.coordinator.controller.set_enabled(value))
        else:
            await self.coordinator.async_configure(**{self.key: value})

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)
