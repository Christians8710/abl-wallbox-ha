"""Operating mode and phase imbalance threshold."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from .entity import ABLEntity

MODES = {"Standalone": "standalone", "RFID / lokale Freigabe": "rfid", "Externe Steuerung / SBC": "sbc"}
IMBALANCE = {"Aus": "off", "16 A": "16", "20 A": "20"}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            ABLSelect(entry.runtime_data, "mode", "Betriebsart"),
            ABLSelect(entry.runtime_data, "imbalance", "Schieflasterkennung"),
        ]
    )


class ABLSelect(ABLEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def options(self):
        if self.key == "mode":
            return list(MODES)
        return list(IMBALANCE) if self.coordinator.data["new_api"] else ["Aus", "20 A"]

    @property
    def current_option(self):
        data = self.coordinator.data
        if self.key == "mode":
            return next((label for label, value in MODES.items() if value == data["mode"]), None)
        if not data["imbalance_enabled"]:
            return "Aus"
        return f"{data['imbalance_limit']} A" if data["imbalance_limit"] in (16, 20) else None

    async def async_select_option(self, option):
        value = (MODES if self.key == "mode" else IMBALANCE)[option]
        await self.coordinator.async_configure(**{self.key: value})
