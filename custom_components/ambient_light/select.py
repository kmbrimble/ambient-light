"""The blend select: switch between the wheel and OKLab blends without a reload."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AmbientConfigEntry
from .const import BLEND_BY_LABEL, BLEND_LABELS, CONF_BLEND, DOMAIN
from .engine import Engine


async def async_setup_entry(
    hass: HomeAssistant, entry: AmbientConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([AmbientBlend(entry)])


class AmbientBlend(SelectEntity):
    """How Random and Drift colour changes are blended: round the wheel, or a straight line in OKLab.

    The choice lives in the entry options (so it survives restarts and shows in the options flow); a
    change that touches only the blend is applied live and does not reload the entry.
    """

    _attr_has_entity_name = True
    _attr_name = "Blend"
    _attr_should_poll = False
    _attr_options = list(BLEND_LABELS.values())

    def __init__(self, entry: AmbientConfigEntry) -> None:
        self._entry = entry
        self._engine: Engine = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_blend"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title)

    @property
    def current_option(self) -> str:
        return BLEND_LABELS[self._engine.blend]

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._engine.add_listener(self._on_engine_change))

    @callback
    def _on_engine_change(self) -> None:
        if self.hass is not None and self.entity_id:
            self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        blend = BLEND_BY_LABEL[option]
        self._engine.set_blend(blend)
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_BLEND: blend}
        )
