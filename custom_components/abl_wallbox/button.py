"""Explicit refresh and controller restart actions."""

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from .entity import ABLEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            ABLButton(entry.runtime_data, key, name)
            for key, name in (("refresh", "Werte und Fehlerzähler aktualisieren"), ("restart", "Wallbox neu starten"))
        ]
    )


class ABLButton(ABLEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key, name)
        if key == "restart":
            self._attr_device_class = ButtonDeviceClass.RESTART

    async def async_press(self):
        if self.key == "restart":
            await self.coordinator.async_command(self.coordinator.controller.restart())
        else:
            await self.coordinator.async_refresh_counters()
