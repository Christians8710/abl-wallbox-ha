"""Charging target, persistent installation limit and bus address."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfElectricCurrent
from .entity import ABLEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            ABLNumber(entry.runtime_data, key, name)
            for key, name in (
                ("requested_current", "Ladestromvorgabe"),
                ("installation_limit", "Feste Stromgrenze einstellen"),
                ("address", "Busadresse"),
            )
        ]
    )


class ABLNumber(ABLEntity, NumberEntity):
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key, name)
        if key != "address":
            self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
            self._attr_icon = "mdi:current-ac"
        if key != "requested_current":
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def native_min_value(self):
        return {"requested_current": 0, "installation_limit": 6, "address": 1}[self.key]

    @property
    def native_max_value(self):
        data = self.coordinator.data
        if self.key == "address":
            return 16
        if self.key == "installation_limit":
            return data["rated_current"]
        return min(data["rated_current"], data["installation_limit"])

    @property
    def native_value(self):
        return self.value

    async def async_set_native_value(self, value):
        if value != int(value):
            raise ValueError("Only integer ampere values and bus addresses are supported")
        if self.key == "requested_current":
            await self.coordinator.async_command(self.coordinator.controller.set_requested_current(int(value)))
        else:
            await self.coordinator.async_configure(**{self.key: int(value)})
