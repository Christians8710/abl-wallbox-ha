"""Shared device identity and entity metadata."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ABLCoordinator


class ABLEntity(CoordinatorEntity[ABLCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ABLCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        data = coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer="ABL",
            model="eMH1 (EVCC2/3)",
            sw_version=data.get("firmware"),
            hw_version=data.get("hardware"),
        )

    @property
    def value(self):
        return self.coordinator.data.get(self.key)
