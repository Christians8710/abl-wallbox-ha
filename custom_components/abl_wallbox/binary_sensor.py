"""Connection, digital inputs, and live faults."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from .entity import ABLEntity

SENSORS = {
    "vehicle_connected": "Fahrzeug verbunden",
    "charging": "Ladevorgang aktiv",
    "fault": "Störung",
    "imbalance_detected": "Schieflast erkannt",
    "overtemperature": "Übertemperatur",
    "meter_fault": "Strommessung gestört",
    "upstream_lost": "Externe Steuerung ausgefallen",
    "en1_closed": "Freigabeeingang EN1",
    "en2_closed": "Freigabeeingang EN2",
}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ABLBinarySensor(entry.runtime_data, key, name) for key, name in SENSORS.items()])


class ABLBinarySensor(ABLEntity, BinarySensorEntity):
    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key, name)
        if key in ("fault", "imbalance_detected", "overtemperature", "meter_fault", "upstream_lost"):
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        elif key in ("en1_closed", "en2_closed"):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self):
        return self.value
