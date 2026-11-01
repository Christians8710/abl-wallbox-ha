"""Controller values, limits and real fault counters."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory

from .const import STATE_LABELS
from .entity import ABLEntity

SENSORS = [
    ("state", "Betriebszustand", None),
    ("state_description", "Statusbeschreibung", None),
    ("rated_current", "Nennstrom", "A"),
    ("installation_limit", "Feste Stromgrenze", "A"),
    ("effective_current", "Effektive Stromfreigabe", "A"),
    ("duty_cycle", "PWM-Tastverhältnis", "%"),
    ("current_l1", "Strom L1", "A"),
    ("current_l2", "Strom L2", "A"),
    ("current_l3", "Strom L3", "A"),
    ("estimated_power", "Geschätzte Ladeleistung", "W"),
    ("firmware", "Firmware", None),
    ("hardware", "Hardware", None),
    ("system_flags", "Systemflags", None),
    ("last_fault", "Letzter beobachteter Fehler", None),
]


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        [ABLValueSensor(coordinator, *definition) for definition in SENSORS]
        + [ABLCounterSensor(coordinator, f"F{n}") for n in range(1, 18)]
    )


class ABLValueSensor(ABLEntity, SensorEntity):
    def __init__(self, coordinator, key, name, unit):
        super().__init__(coordinator, key, name)
        self._attr_native_unit_of_measurement = unit
        if unit == "A":
            self._attr_device_class = SensorDeviceClass.CURRENT
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit == "W":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit == "%":
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if key in ("rated_current", "firmware", "hardware", "system_flags", "last_fault"):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if key == "system_flags":
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        return self.value

    @property
    def extra_state_attributes(self):
        if self.key == "last_fault":
            return {
                "history": self.coordinator.history,
                "timestamps": "Home Assistant observation times; the controller stores counters without timestamps",
            }
        if self.key == "estimated_power":
            return {"assumed_voltage": 230, "calculation": "230 V × sum of measured phase currents"}
        return None


class ABLCounterSensor(ABLEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "Fehler"

    def __init__(self, coordinator, code):
        super().__init__(coordinator, code, f"Fehlerzähler {code}")

    @property
    def native_value(self):
        return self.coordinator.data.get("error_counters", {}).get(self.key)

    @property
    def extra_state_attributes(self):
        return {"description": STATE_LABELS.get(self.key, self.key), "source": "wallbox", "range": "0..255"}
